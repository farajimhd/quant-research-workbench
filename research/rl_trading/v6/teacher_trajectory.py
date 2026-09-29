"""Hypothetical price-action-only portfolio path for V6 imitation labels.

The teacher buys at a known completed candle close and books that idealized
price at the next observed market clock. The same convention applies to its
hindsight close exit. These are label-side outcomes, not claims of executable
fills. Quotes, spread, displayed size and liquidity never enter this module.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import math
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from research.rl_trading.v6.eligibility import causal_enter_mask
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.training import ExecutionOutcome, TeacherDecision, _validate


VERSION = 'rl-trading-price-action-trajectory-v6-1'
INITIAL_CASH = 10_000.
FEE_PER_SHARE = .005
HOLD_SAMPLE_SECONDS = 5
LOG_CLOSE = SCALAR_NAMES.index('log_close')
PRICE_VALID = SCALAR_NAMES.index('bar_price_valid')
NY = ZoneInfo('America/New_York')


def _period(us: int) -> str:
    observed = datetime.fromtimestamp(us/1_000_000, NY)
    minute = observed.hour*60 + observed.minute
    return ('premarket' if minute < 570 else
            'regular' if minute < 960 else 'after_hours')


def _debit_nonnegative(balance: float, amount: float) -> float:
    """Subtract a committed amount, removing only IEEE-754 cancellation dust."""
    if (not math.isfinite(balance) or not math.isfinite(amount) or
            balance < 0 or amount < 0):
        raise ValueError('Invalid teacher account debit')
    remaining = math.fsum((balance, -amount))
    tolerance = 16*math.ulp(max(balance, amount, 1.))
    if remaining < -tolerance:
        raise ValueError('Teacher debit exceeds available balance')
    return max(0., remaining)


@dataclass(frozen=True)
class Intent:
    ticker: str
    listing_index: int
    episode_uid: str
    entry_us: int
    exit_us: int
    entry_price: float
    exit_price: float
    desired_budget: float
    future_reservation: float
    score: float
    stop: float
    target: float
    last_target_touch_us: int


@dataclass
class Holding:
    intent: Intent
    shares: float
    filled_us: int
    stop: float | None = None
    target: float | None = None
    exit_pending: bool = False


def bind_intents(allocations: pl.DataFrame, brackets: pl.DataFrame,
                 listings: tuple[str, ...]) -> tuple[tuple[Intent, ...], dict]:
    """Join only first-eligible long episodes to audited oracle geometry."""
    required = {'ticker', 'listing_id', 'episode_uid', 'time_us', 'exit_hint_us',
                'decision_close', 'exit_hint_close', 'desired_budget',
                'future_reservation', 'score', 'direction'}
    required_brackets = {'ticker', 'episode_uid', 'entry_us', 'exit_us',
                         'oracle_stop', 'oracle_target',
                         'held_last_max_high_us', 'label_available'}
    if (not required <= set(allocations.columns) or
            not required_brackets <= set(brackets.columns) or
            len(set(listings)) != len(listings)):
        raise ValueError('Missing or ambiguous V6 teacher source identity')
    long = allocations.filter(pl.col('direction') == 1)
    if (long['episode_uid'].n_unique() != long.height or
            brackets['episode_uid'].n_unique() != brackets.height):
        raise ValueError('Duplicate episode in teacher sources')
    joined = long.join(brackets.select(*sorted(required_brackets)),
        on=['ticker', 'episode_uid'], how='left', validate='1:1')
    if (joined['entry_us'].null_count() or joined['label_available'].null_count()
            or joined.filter((pl.col('time_us') != pl.col('entry_us')) |
                             (pl.col('exit_hint_us') != pl.col('exit_us'))).height):
        raise ValueError('Oracle brackets do not bind to first-eligible entries')
    permitted = joined.filter(pl.col('label_available'))
    by_listing = {identity: index for index, identity in enumerate(listings)}
    if set(permitted['listing_id']) - set(by_listing):
        raise ValueError('Teacher episode listing absent from bank')
    mapping = permitted.select('ticker', 'listing_id').unique()
    if (mapping['ticker'].n_unique() != mapping.height or
            mapping['listing_id'].n_unique() != mapping.height):
        raise ValueError('Teacher ticker/listing mapping is ambiguous')
    ordered = permitted.sort('time_us', 'score', 'episode_uid',
                             descending=[False, True, False])
    intents = []
    for row in ordered.iter_rows(named=True):
        values = [row[key] for key in ('decision_close', 'exit_hint_close',
            'desired_budget', 'future_reservation', 'score', 'oracle_stop',
            'oracle_target')]
        if (not all(math.isfinite(value) for value in values) or
                row['time_us'] >= row['exit_hint_us'] or
                row['decision_close'] <= 0 or row['exit_hint_close'] <= 0 or
                row['desired_budget'] <= 0 or row['future_reservation'] < 0 or
                not 0 < row['oracle_stop'] < row['decision_close'] <
                        row['oracle_target']):
            raise ValueError('Malformed price-action teacher intent')
        intents.append(Intent(row['ticker'], by_listing[row['listing_id']],
            row['episode_uid'], int(row['time_us']), int(row['exit_hint_us']),
            float(row['decision_close']), float(row['exit_hint_close']),
            float(row['desired_budget']), float(row['future_reservation']),
            float(row['score']), float(row['oracle_stop']),
            float(row['oracle_target']),
            int(row['held_last_max_high_us'])))
    return tuple(intents), {'first_eligible_longs': long.height,
        'oracle_geometry_rejected': long.height - len(intents),
        'oracle_geometry_accepted': len(intents)}


def compile_trajectory(session: PackedSession, intents: tuple[Intent, ...], * ,
                       initial_cash: float = INITIAL_CASH,
                       hold_sample_seconds: int = HOLD_SAMPLE_SECONDS
                       ) -> tuple[tuple[TeacherDecision, ...],
                                  tuple[ExecutionOutcome, ...], dict]:
    """Build chronological idealized decisions with original-bankroll sizing.

    Completed-candle state and previous teacher actions are the only policy
    inputs. Future episode scores choose teacher actions but never determine
    the causal action mask. Positive realized P&L is swept out of trading
    cash; losses remain, so gains cannot compound teacher sizes.
    """
    if (session.role not in ('train', 'development') or
            not math.isfinite(initial_cash) or initial_cash <= 0 or
            hold_sample_seconds < 1):
        raise ValueError('Invalid V6 teacher session or bankroll')
    if not len(session.bank.close_us):
        raise ValueError('Certified session has no observed candle clocks')
    final_close = int(np.max(session.bank.close_us))
    entry_by_clock: dict[int, list[Intent]] = defaultdict(list)
    exit_by_clock: dict[int, list[Intent]] = defaultdict(list)
    rejected_terminal = 0
    for intent in intents:
        if intent.exit_us >= final_close:
            rejected_terminal += 1
            continue
        entry_by_clock[intent.entry_us].append(intent)
        exit_by_clock[intent.exit_us].append(intent)
    for values in entry_by_clock.values():
        values.sort(key=lambda item: (-item.score, item.episode_uid))
    ticker_by_listing = {intent.listing_index: intent.ticker
                         for intent in intents}
    if len(ticker_by_listing) != len(set(ticker_by_listing.values())):
        raise ValueError('Teacher listing/ticker map changed across episodes')
    listings = len(session.listings)
    cash = initial_cash
    profit_bank = realized = fees = 0.
    reserved = 0.
    pending_buys: dict[str, tuple[Intent, float, float, tuple[int, int]]] = {}
    pending_sells: dict[str, tuple[Holding, tuple[int, int], float]] = {}
    holdings: dict[str, Holding] = {}
    marks: dict[str, float] = {}
    decisions: list[TeacherDecision] = []
    outcomes: list[ExecutionOutcome] = []
    ledger = []
    skipped_cash = skipped_duplicate = 0
    max_open = 0
    events = iter(session.candle_events())
    event = next(events, None)
    if event is None:
        raise ValueError('Certified bank has no market event')
    last_action_us = event.close_us
    event_number = 0
    observed_clocks = set()
    while event is not None:
        clock = event.close_us
        following = next(events, None)
        next_clock = following.close_us if following is not None else None
        observed_clocks.add(clock)
        # Earlier idealized orders are confirmed at this later market clock.
        for ticker, (intent, shares, spend, source) in tuple(
                pending_buys.items()):
            if source[0] >= clock:
                continue
            cash = _debit_nonnegative(cash, spend)
            reserved = _debit_nonnegative(reserved, spend)
            fees += shares*FEE_PER_SHARE
            holdings[ticker] = Holding(intent, shares, clock)
            outcomes.append(ExecutionOutcome(source[0], source[1], clock,
                1, intent.listing_index, spend/max(initial_cash, cash+spend),
                1., 0.))
            del pending_buys[ticker]
        for ticker, (holding, source, pre_exit_equity) in tuple(
                pending_sells.items()):
            if source[0] >= clock:
                continue
            intent, shares = holding.intent, holding.shares
            proceeds = shares*(intent.exit_price-FEE_PER_SHARE)
            pnl = shares*(intent.exit_price-intent.entry_price-
                          2*FEE_PER_SHARE)
            cash += proceeds
            fees += shares*FEE_PER_SHARE
            realized += pnl
            if pnl > 0:
                cash = _debit_nonnegative(cash, pnl)
                profit_bank += pnl
            outcomes.append(ExecutionOutcome(source[0], source[1], clock,
                2, intent.listing_index, 1., 1.,
                pnl/max(pre_exit_equity, 1e-9)))
            ledger.append({'ticker': ticker, 'episode_uid': intent.episode_uid,
                'entry_period': _period(intent.entry_us),
                'entry_decision_us': intent.entry_us,
                'entry_confirmed_us': holding.filled_us,
                'exit_decision_us': intent.exit_us,
                'exit_confirmed_us': clock, 'shares': shares,
                'entry_price': intent.entry_price,
                'exit_price': intent.exit_price, 'net_pnl': pnl,
                'oracle_stop': intent.stop, 'oracle_target': intent.target})
            del holdings[ticker]
            del pending_sells[ticker]
        changed = np.asarray(session.bank.scalar[event.bank_row])
        for listing_index, row in zip(event.listing_index, changed):
            if row[PRICE_VALID] != 1.:
                continue
            price = math.exp(float(row[LOG_CLOSE]))
            if math.isfinite(price) and price > 0 and (
                    int(listing_index) in ticker_by_listing):
                marks[ticker_by_listing[int(listing_index)]] = price
        index = 0

        def snapshot(token: int, *, size_fraction: float | None = None,
                     distance: float | None = None) -> tuple[int, int]:
            nonlocal index, last_action_us
            held = sorted(holdings, key=lambda name:
                          holdings[name].intent.listing_index)
            held_index = np.asarray([holdings[name].intent.listing_index
                                     for name in held], dtype=np.int64)
            features = np.zeros((len(held), 9), dtype=np.float32)
            exposure = 0.
            for slot, name in enumerate(held):
                position = holdings[name]
                mark = marks.get(name, position.intent.entry_price)
                if not math.isfinite(mark) or mark <= 0:
                    raise ValueError('Teacher holding has no causal positive mark')
                exposure += position.shares*mark
                features[slot] = (position.shares,
                    position.intent.entry_price,
                    (clock-position.filled_us)/1_000_000,
                    (mark-position.intent.entry_price)/position.intent.entry_price,
                    (mark-position.stop)/mark if position.stop else 0.,
                    (position.target-mark)/mark if position.target else 0.,
                    float(position.stop is not None),
                    float(position.target is not None), 0.)
            equity = cash+exposure
            state = np.asarray((cash, equity, realized,
                exposure/equity if equity > 0 else 0.,
                (clock-last_action_us)/1_000_000,
                reserved, len(pending_buys)), dtype=np.float32)
            pending_index = np.asarray([row[0].listing_index for row in
                pending_buys.values()], dtype=np.int64)
            try:
                mask = causal_enter_mask(listings, event.listing_index,
                    changed, cash=cash, reserved_cash=reserved,
                    held_index=held_index, pending_index=pending_index)
            except ValueError as exc:
                raise ValueError(f'{clock}: teacher account/mask invalid '
                    f'cash={cash:.8f} reserved={reserved:.8f} '
                    f'changed={len(event.listing_index)} held={held_index.tolist()} '
                    f'pending={pending_index.tolist()}') from exc
            exit_allowed = np.asarray([not holdings[name].exit_pending
                for name in held], dtype=np.bool_)
            stop_allowed = np.asarray([(holdings[name].stop is None and
                not holdings[name].exit_pending) for name in held],
                dtype=np.bool_)
            target_allowed = np.asarray([(holdings[name].target is None and
                not holdings[name].exit_pending) for name in held],
                dtype=np.bool_)
            key = (clock, index)
            decisions.append(TeacherDecision(clock, index, token, state,
                held_index, features, mask, exit_allowed, stop_allowed,
                target_allowed, size_fraction, distance))
            index += 1
            if token:
                last_action_us = clock
            return key

        # The bracket becomes an admissible action only after entry outcome.
        for ticker in sorted(holdings, key=lambda name:
                             holdings[name].intent.listing_index):
            holding = holdings[ticker]
            if holding.exit_pending or clock >= holding.intent.exit_us:
                continue
            slot = sum(holdings[name].intent.listing_index <
                       holding.intent.listing_index for name in holdings)
            if holding.stop is None:
                token = 1 + listings + len(holdings) + slot
                key = snapshot(token, distance=math.log(
                    holding.intent.entry_price/holding.intent.stop))
                holding.stop = holding.intent.stop
                if next_clock is not None:
                    outcomes.append(ExecutionOutcome(key[0], key[1],
                        next_clock, 3, holding.intent.listing_index, 1., 1., 0.))
            if (holding.target is None and
                    holding.intent.last_target_touch_us > clock):
                token = 1 + listings + 2*len(holdings) + slot
                key = snapshot(token, distance=math.log(
                    holding.intent.target/holding.intent.entry_price))
                holding.target = holding.intent.target
                if next_clock is not None:
                    outcomes.append(ExecutionOutcome(key[0], key[1],
                        next_clock, 4, holding.intent.listing_index, 1., 1., 0.))
        for intent in exit_by_clock.get(clock, ()):
            holding = holdings.get(intent.ticker)
            if holding is None or holding.intent.episode_uid != intent.episode_uid:
                continue
            held = sorted(holdings, key=lambda name:
                          holdings[name].intent.listing_index)
            slot = held.index(intent.ticker)
            pre_exit_equity = cash + sum(position.shares*
                marks.get(name, position.intent.entry_price)
                for name, position in holdings.items())
            key = snapshot(1 + listings + slot)
            holding.exit_pending = True
            pending_sells[intent.ticker] = (holding, key, pre_exit_equity)
        for intent in entry_by_clock.get(clock, ()):
            ticker = intent.ticker
            if ticker in holdings or ticker in pending_buys or (
                    ticker in pending_sells):
                skipped_duplicate += 1
                continue
            budget = min(intent.desired_budget,
                max(0., cash-reserved-intent.future_reservation))
            if budget <= 0 or next_clock is None:
                skipped_cash += 1
                continue
            shares = budget/(intent.entry_price+FEE_PER_SHARE)
            spend = shares*(intent.entry_price+FEE_PER_SHARE)
            if not math.isfinite(shares) or shares <= 0:
                skipped_cash += 1
                continue
            key = snapshot(1+intent.listing_index,
                size_fraction=spend/max(cash-reserved, 1e-9))
            reserved += spend
            pending_buys[ticker] = (intent, shares, spend, key)
        if index == 0 and event_number % hold_sample_seconds == 0:
            snapshot(0)
        max_open = max(max_open, len(holdings))
        event = following
        event_number += 1
    if ((set(entry_by_clock) | set(exit_by_clock)) - observed_clocks):
        raise ValueError('Teacher intent clock is absent from certified bank')
    # Bracket-setting outcomes are sorted with price-action order outcomes;
    # a source action may have multiple later events at the same close.
    outcomes.sort(key=lambda item: (item.bucket_end_us,
        item.source_close_us, item.source_order_index))
    result = tuple(decisions), tuple(outcomes)
    _validate(*result, listings)
    period_net = {period: sum(row['net_pnl'] for row in ledger
                             if row['entry_period'] == period)
                  for period in ('premarket', 'regular', 'after_hours')}
    report = {'version': VERSION, 'hypothetical_only': True,
        'initial_cash': initial_cash, 'ending_trading_cash': cash,
        'profit_bank': profit_bank, 'modeled_net_pnl': realized,
        'modeled_fees': fees, 'completed_positions': len(ledger),
        'period_net_by_entry': period_net,
        'stop_setting_actions': sum(item.action == 3 for item in outcomes),
        'target_setting_actions': sum(item.action == 4 for item in outcomes),
        'max_open_positions': max_open,
        'skipped_cash': skipped_cash,
        'skipped_held_or_pending_ticker': skipped_duplicate,
        'rejected_no_later_market_clock': rejected_terminal,
        'unresolved_positions': len(holdings),
        'pending_entries': len(pending_buys),
        'pending_exits': len(pending_sells),
        'hold_sampling_interval': hold_sample_seconds,
        'decision_rows': len(decisions), 'outcome_rows': len(outcomes),
        'ledger': ledger}
    return *result, report
