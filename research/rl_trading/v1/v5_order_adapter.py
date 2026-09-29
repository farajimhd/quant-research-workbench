"""Causal, ordered V5 imitation states from a certified teacher order ledger.

This adapter never exposes Phase 2 scores, target prices, or episode IDs to the
policy. Episode IDs are used only to reconcile teacher holdings and SELL tokens.
The teacher's fixed-bankroll/profit-bank account is reconstructed for imitation;
model replay has its own compounding execution account.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import polars as pl

from research.rl_trading.v1.features import FEATURE_NAMES


PRICE = FEATURE_NAMES.index('log_price')
AVAILABLE = FEATURE_NAMES.index('price_available')


@dataclass(frozen=True)
class OrderSecond:
    second: int
    # [O,5]: trading cash, marked equity, realized P&L, exposure, seconds
    # since the last executed order. One final STOP row follows all trades.
    account: np.ndarray
    # [O,H] and [O,H,4], padded to the maximum holdings in this second.
    held_index: np.ndarray
    held_valid: np.ndarray
    held_features: np.ndarray
    # 0=STOP, 1..N=BUY listing, N+1..N+H=SELL holding slot.
    token: np.ndarray
    size: np.ndarray
    action_mask: np.ndarray


def stop_second_indices(active_seconds: np.ndarray, decision_seconds: int, *,
                        radius: int = 15, background_stride: int = 60) -> np.ndarray:
    """Sample causal STOP examples near actions and across quiet intervals.

    The 15-second neighborhood teaches when to wait immediately before and
    after a teacher action. The fixed background stride covers the rest of the
    session without materializing a STOP decision at every second. Training
    must report these sampling parameters and calibrate with full-session
    closed-loop replay; this sampler does not alter the teacher ledger.
    """
    active = np.asarray(active_seconds, dtype=np.int64)
    if (decision_seconds < 1 or radius < 0 or background_stride < 1 or
            (active.size and (np.any(active < 0) or
                              np.any(active >= decision_seconds)))):
        raise ValueError('Invalid STOP sampling bounds')
    selected = np.zeros(decision_seconds, dtype=np.bool_)
    selected[::background_stride] = True
    for second in np.unique(active):
        selected[max(0, second-radius):min(decision_seconds, second+radius+1)] = True
    selected[active] = False
    return np.flatnonzero(selected)


def empty_stop_seconds(seconds: np.ndarray, trajectory: pl.DataFrame,
                       positions: pl.DataFrame, feature_bank: np.ndarray,
                       tickers: tuple[str, ...]):
    """Yield STOP targets from causal post-action accounts on no-order seconds.

    Position intervals are swept chronologically; no Phase 2 opportunity score
    or future price enters the observation. The selected second must have zero
    teacher buys/sells, so its post-action account equals its decision account.
    """
    requested = np.asarray(seconds, dtype=np.int64)
    if (requested.ndim != 1 or (requested.size and
            (requested[0] < 0 or requested[-1] >= trajectory.height or
             np.any(np.diff(requested) <= 0))) or
            feature_bank.shape[:2] != (len(tickers), 57_601) or
            feature_bank.shape[2] != len(FEATURE_NAMES)):
        raise ValueError('Invalid STOP seconds or identity-aligned features')
    ticker_index = {ticker: slot for slot, ticker in enumerate(tickers)}
    if len(ticker_index) != len(tickers):
        raise ValueError('Duplicate listing identities')
    by_entry = positions.sort('entry_us').to_dicts()
    by_exit = positions.sort('exit_us').to_dicts()
    events = np.sort(np.concatenate((positions['entry_us'].to_numpy(),
                                     positions['exit_us'].to_numpy())))
    cursor_in = cursor_out = 0
    held: dict[str, dict] = {}
    first = int(trajectory['time_us'][0])
    for second in requested:
        now = first+int(second)*1_000_000
        while cursor_in < len(by_entry) and int(by_entry[cursor_in]['entry_us']) <= now:
            row = by_entry[cursor_in]
            held[row['episode_uid']] = row
            cursor_in += 1
        while cursor_out < len(by_exit) and int(by_exit[cursor_out]['exit_us']) <= now:
            del held[by_exit[cursor_out]['episode_uid']]
            cursor_out += 1
        teacher = trajectory.row(int(second), named=True)
        if (int(teacher['time_us']) != now or int(teacher['bought']) or
                int(teacher['sold']) or len(held) != int(teacher['open_lots'])):
            raise ValueError('STOP second contains a trade or wrong holdings')
        features = feature_bank[:, second, :]
        # Replay holdings have listing identities, not hindsight episode IDs.
        # Use the same ticker-ordered SELL axis in training and inference.
        episodes = sorted(held, key=lambda uid: (held[uid]['ticker'], uid))
        slots = np.asarray([ticker_index[held[uid]['ticker']] for uid in episodes],
                           dtype=np.int64)
        marks = np.asarray([_marked_price(features, slot) for slot in slots])
        quantity = np.asarray([float(held[uid]['quantity']) for uid in episodes])
        exposure_value = float(np.dot(quantity, marks))
        cash = float(teacher['cash'])
        bank = float(teacher['profit_bank'])
        equity = cash+bank+exposure_value
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError('Invalid causal marked equity at STOP second')
        previous_trade = np.searchsorted(events, now, side='right')-1
        elapsed = ((now-int(events[previous_trade]))/1_000_000
                   if previous_trade >= 0 else int(second))
        account = np.asarray([[cash, equity, float(teacher['realized_net_pnl']),
                               exposure_value/equity, elapsed]], dtype=np.float32)
        width = max(1, len(episodes))
        held_index = np.zeros((1, width), dtype=np.int64)
        held_valid = np.zeros((1, width), dtype=np.bool_)
        held_features = np.zeros((1, width, 4), dtype=np.float32)
        held_index[0, :len(slots)] = slots
        held_valid[0, :len(slots)] = True
        for i, uid in enumerate(episodes):
            held_features[0, i] = [math.log1p(float(held[uid]['quantity'])),
                math.log(float(held[uid]['entry_price'])),
                math.log1p(max(0., (now-int(held[uid]['entry_us']))/1_000_000.)) /
                    math.log1p(57_600.),
                marks[i]/float(held[uid]['entry_price'])-1.]
        mask = np.zeros((1, 1+len(tickers)+width), dtype=np.bool_)
        mask[0, 0] = True
        mask[0, 1:1+len(tickers)] = features[:, AVAILABLE] > .5
        mask[0, 1+slots] = False
        mask[0, 1+len(tickers):1+len(tickers)+len(slots)] = True
        yield OrderSecond(int(second), account, held_index, held_valid,
                          held_features, np.zeros(1, dtype=np.int64),
                          np.zeros(1, dtype=np.float32), mask)


def _marked_price(features: np.ndarray, listing: int) -> float:
    price = float(np.exp(float(features[listing, PRICE])))
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Held listing lacks a causal marked price')
    return price


def order_seconds(orders: pl.DataFrame, trajectory: pl.DataFrame,
                  positions: pl.DataFrame, feature_bank: np.ndarray,
                  tickers: tuple[str, ...], *, initial_cash: float = 10_000.):
    """Yield active seconds, including a STOP decision after ordered trades.

    ``feature_bank`` is [N,seconds,F], identity-aligned and previously bound by
    ``bind_existing_features``. Account and holding snapshots are recomputed
    before *each* order; sales, the teacher's profit sweep, then buys execute in
    that order. This generator is sparse in labels and does no hindsight reads
    from Phase 2. The caller may add sampled empty STOP seconds separately.
    """
    if (not tickers or len(set(tickers)) != len(tickers) or
            feature_bank.shape[:2] != (len(tickers), 57_601) or
            feature_bank.shape[2] != len(FEATURE_NAMES) or
            not math.isfinite(initial_cash) or initial_cash <= 0):
        raise ValueError('Invalid identity-aligned causal feature bank')
    if trajectory.is_empty() or trajectory['time_us'].n_unique() != trajectory.height:
        raise ValueError('Teacher trajectory must have unique ordered seconds')
    times = trajectory['time_us'].to_numpy()
    if np.any(np.diff(times) != 1_000_000):
        raise ValueError('Teacher trajectory is not a complete second grid')
    if (orders.select('episode_uid','action').n_unique() != orders.height or
            orders.filter(~pl.col('action').is_in(['buy','sell'])).height or
            positions.select('episode_uid').n_unique() != positions.height):
        raise ValueError('Teacher orders or positions are duplicated')
    index = {ticker: slot for slot, ticker in enumerate(tickers)}
    if set(orders['ticker'].to_list()) - set(index):
        raise ValueError('Teacher order listing is absent from causal features')
    position = {row['episode_uid']: row for row in positions.to_dicts()}
    grouped = orders.sort('time_us','order_index').partition_by('time_us',
                                                                 maintain_order=True)
    held: dict[str, dict] = {}
    last_trade = -1
    realized = 0.
    first = int(times[0])
    for block in grouped:
        now = int(block['time_us'][0])
        second = (now-first)//1_000_000
        if (second < 0 or second >= trajectory.height or times[second] != now or
                block['order_index'].to_list() != list(range(block.height))):
            raise ValueError('Teacher orders do not match the decision grid')
        row = trajectory.row(second, named=True)
        previous = trajectory.row(second-1, named=True) if second else None
        cash = float(previous['cash']) if previous else initial_cash
        bank = float(previous['profit_bank']) if previous else 0.
        realized = float(previous['realized_net_pnl']) if previous else 0.
        sweep = float(row['profit_bank'])-bank
        if sweep < -1e-6:
            raise ValueError('Teacher profit bank decreased')
        features = feature_bank[:, second, :]
        records = block.to_dicts()
        if any(record['action'] == 'sell' for record in records):
            first_buy = next((i for i, record in enumerate(records)
                              if record['action'] == 'buy'), len(records))
            if any(record['action'] == 'sell' for record in records[first_buy:]):
                raise ValueError('Teacher sales must precede purchases')
        else:
            first_buy = 0
        states = []
        tokens = []
        sizes = []

        def snapshot() -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
            episodes = sorted(held, key=lambda uid: (held[uid]['ticker'], uid))
            slots = np.asarray([index[held[uid]['ticker']] for uid in episodes],
                               dtype=np.int64)
            marks = np.asarray([_marked_price(features, slot) for slot in slots],
                               dtype=np.float64)
            quantity = np.asarray([float(held[uid]['quantity']) for uid in episodes])
            exposure_value = float(np.dot(quantity, marks))
            equity = cash+bank+exposure_value
            if not math.isfinite(equity) or equity <= 0:
                raise ValueError('Nonpositive teacher marked equity')
            account = np.asarray([cash, equity, realized,
                                  exposure_value/equity,
                                  second-last_trade if last_trade >= 0 else second],
                                 dtype=np.float32)
            held_features = np.asarray([
                [math.log1p(float(held[uid]['quantity'])),
                 math.log(float(held[uid]['entry_price'])),
                 math.log1p(max(0., (now-int(held[uid]['entry_us']))/1_000_000.)) /
                     math.log1p(57_600.),
                 marks[i]/float(held[uid]['entry_price'])-1.]
                for i, uid in enumerate(episodes)], dtype=np.float32).reshape(-1, 4)
            return account, episodes, slots, held_features

        for i, record in enumerate(records):
            if i == first_buy and sweep:
                cash -= sweep
                bank += sweep
            account, episodes, slots, held_features = snapshot()
            states.append((account, slots, held_features))
            uid = record['episode_uid']
            if record['action'] == 'sell':
                if uid not in held:
                    raise ValueError('Teacher sold an unopened episode')
                tokens.append(1+len(tickers)+episodes.index(uid))
                sizes.append(0.)
                cash += float(record['cash_amount'])
                realized += float(record['net_pnl'])
                del held[uid]
            else:
                if uid in held or uid not in position:
                    raise ValueError('Teacher opened duplicate or unknown episode')
                tokens.append(1+index[record['ticker']])
                sizes.append(float(record['remaining_cash_weight']))
                cash -= float(record['cash_amount'])
                held[uid] = position[uid]
            last_trade = second
        if first_buy == len(records) and sweep:
            cash -= sweep
            bank += sweep
        account, episodes, slots, held_features = snapshot()
        states.append((account, slots, held_features))
        tokens.append(0)
        sizes.append(0.)
        if (abs(cash-float(row['cash'])) > 1e-4 or
                abs(bank-float(row['profit_bank'])) > 1e-4 or
                abs(realized-float(row['realized_net_pnl'])) > 1e-4 or
                len(held) != int(row['open_lots'])):
            raise ValueError('Teacher account or holdings did not reconcile')
        width = max(1, max(len(item[1]) for item in states))
        length = len(states)
        held_index = np.zeros((length, width), dtype=np.int64)
        held_valid = np.zeros((length, width), dtype=np.bool_)
        held_data = np.zeros((length, width, 4), dtype=np.float32)
        masks = np.zeros((length, 1+len(tickers)+width), dtype=np.bool_)
        for i, (account, slots, data) in enumerate(states):
            n = len(slots)
            held_index[i, :n] = slots
            held_valid[i, :n] = True
            held_data[i, :n] = data
            masks[i, 0] = True
            masks[i, 1:1+len(tickers)] = features[:, AVAILABLE] > .5
            masks[i, 1+slots] = False  # No second position in an already held listing.
            masks[i, 1+len(tickers):1+len(tickers)+n] = True
        target = np.asarray(tokens, dtype=np.int64)
        if any(not masks[i, token] for i, token in enumerate(target)):
            raise ValueError('Teacher action is not causally admissible')
        yield OrderSecond(second, np.stack([item[0] for item in states]),
                          held_index, held_valid, held_data, target,
                          np.asarray(sizes, dtype=np.float32), masks)
