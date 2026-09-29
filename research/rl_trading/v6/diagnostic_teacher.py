"""Sparse entry/exit quote diagnostic before bracketed teacher certification.

This intentionally omits stop/target execution and terminal liquidation. It
reports only realized modeled P&L and unresolved holdings, never a full-day
teacher profit. Its purpose is to expose sizing/fill feasibility early.
"""
from __future__ import annotations

from datetime import datetime
import math
from zoneinfo import ZoneInfo

import polars as pl

from research.rl_trading.v6.oms import BracketAccount, Quote


NY = ZoneInfo('America/New_York')


def _quote(row: dict) -> Quote:
    available = bool(row['quote_available'])
    return Quote(
        bucket_end_us=int(row['arrival_bucket_end_us']),
        quote_us=int(row['quote_timestamp_us'] or 0),
        bid=float(row['bid_int'])/10_000 if available else 0.,
        ask=float(row['ask_int'])/10_000 if available else 0.,
        bid_size=float(row['bid_size']) if available else 0.,
        ask_size=float(row['ask_size']) if available else 0.,
        valid=available)


def _period(us: int) -> str:
    clock = datetime.fromtimestamp(us/1_000_000, NY)
    minute = clock.hour*60+clock.minute
    return ('premarket' if minute < 570 else
            'regular' if minute < 960 else 'after_hours')


def diagnose(allocations: pl.DataFrame, entry_quotes: pl.DataFrame,
             exit_quotes: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """Sequential account over sparse quote events, with vectorized funnel.

    The 15-second reservation and $10k desired budgets are already derived
    vectorially. Here account state requires chronological handling, but only
    first-eligible intents and their exits are visited, not every listing or
    every clock second.
    """
    required = {'episode_uid', 'ticker', 'time_us', 'exit_hint_us',
                'decision_close', 'desired_budget', 'future_reservation',
                'score', 'direction'}
    quote_required = {'episode_uid', 'quote_available', 'arrival_bucket_end_us',
                      'quote_timestamp_us', 'bid_int', 'ask_int', 'bid_size',
                      'ask_size'}
    if (not required <= set(allocations.columns) or
            not quote_required <= set(entry_quotes.columns) or
            not quote_required <= set(exit_quotes.columns)):
        raise ValueError('Missing sparse diagnostic account inputs')
    long = allocations.filter(pl.col('direction') == 1)
    if (long['episode_uid'].n_unique() != long.height or
            entry_quotes['episode_uid'].n_unique() != entry_quotes.height or
            exit_quotes['episode_uid'].n_unique() != exit_quotes.height):
        raise ValueError('Duplicate episode identity in teacher diagnostic')
    entry = long.join(entry_quotes.select('episode_uid', *(
        quote_required-{'episode_uid'})), on='episode_uid',
        how='left', validate='1:1')
    exit_rows = long.join(exit_quotes.select('episode_uid', *(
        quote_required-{'episode_uid'})), on='episode_uid',
        how='left', validate='1:1')
    if (entry['quote_available'].null_count() or
            exit_rows['quote_available'].null_count()):
        raise ValueError('Sparse quote evidence omits an episode')
    account = BracketAccount(sweep_teacher_profits=True)
    active: dict[str, str] = {}  # episode_uid -> ticker.
    events = []
    for row in entry.iter_rows(named=True):
        events.append((int(row['time_us']), 1, -float(row['score']), row))
    for row in exit_rows.iter_rows(named=True):
        events.append((int(row['exit_hint_us']), 0, 0., row))
    events.sort(key=lambda event: (event[0], event[1], event[2],
                                   event[3]['episode_uid']))
    selected = 0
    no_fill = 0
    conflicts = 0
    max_open = 0
    for clock, kind, _, row in events:
        ticker = row['ticker']
        uid = row['episode_uid']
        if kind == 0:
            if active.get(uid) != ticker:
                continue
            account.exit_long(ticker, decision_us=clock, quote=_quote(row))
            if ticker not in account.positions:
                del active[uid]
            continue
        if ticker in account.positions:
            conflicts += 1
            continue
        available_cash = max(0., account.cash-
            float(row['future_reservation']))
        budget = min(float(row['desired_budget']), available_cash)
        if not math.isfinite(budget) or budget < 0:
            raise ValueError('Nonfinite or negative sparse teacher budget')
        shares = account.enter_long(ticker, decision_us=clock,
            decision_close=float(row['decision_close']), budget=budget,
            quote=_quote(row))
        if shares:
            active[uid] = ticker
            selected += 1
            max_open = max(max_open, len(account.positions))
        else:
            no_fill += 1
    ledger = pl.DataFrame(account.closed) if account.closed else pl.DataFrame()
    order_trace = pl.DataFrame(account.orders) if account.orders else pl.DataFrame()
    period_net = {'premarket': 0., 'regular': 0., 'after_hours': 0.}
    for row in account.closed:
        period_net[_period(row['entry_us'])] += row['net_pnl']
    report = {'version': 'rl-trading-sparse-quote-diagnostic-v6',
        'scope': 'realized_modeled_quotes_only_not_full_session_profit',
        'first_eligible_long_intentions': long.height,
        'filled_entry_intentions': selected,
        'unfilled_entry_intentions': no_fill,
        'open_ticker_conflicts': conflicts,
        'closed_tranches': len(account.closed),
        'unresolved_open_positions': len(account.positions),
        'max_open_positions': max_open,
        'realized_net_pnl': sum(row['net_pnl'] for row in account.closed),
        'modeled_fees': account.fees,
        'period_realized_net_by_entry': period_net,
        'teacher_profit_bank': account.profit_bank,
        'teacher_trading_cash': account.cash}
    if selected+no_fill+conflicts != long.height:
        raise ValueError('Sparse entry intention count did not reconcile')
    return order_trace, ledger, report
