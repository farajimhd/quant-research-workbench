"""Apply quote-bound exit attempts until filled or certified evidence ends.

One stale or shallow quote does not make a position disappear. The caller
provides observed, time-ordered 100 ms buckets from the pinned ARTE attempt;
this function does not invent a later quote or an order fill. An unfinished
position remains in the OMS and is visible to subsequent clocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from research.rl_trading.v6.oms import BracketAccount, Quote


@dataclass(frozen=True)
class ExitStreamResult:
    attempts: int
    filled_shares: int
    remaining_shares: int
    last_bucket_end_us: int | None


def apply_exit_quote_stream(account: BracketAccount, ticker: str, *,
                            decision_us: int, quotes: Iterable[Quote],
                            action: str = 'exit_long') -> ExitStreamResult:
    """Retry the remaining shares across strictly later quote buckets.

    This books the OMS's explicitly optimistic displayed-bid-size scenario.
    It says nothing about broker queue position. Missing or stale buckets are
    retained as unfilled attempts; no terminal liquidation is fabricated.
    """
    if (ticker not in account.positions or type(decision_us) is not int or
            decision_us < account.positions[ticker].last_action_us or
            action not in ('exit_long', 'stop_market')):
        raise ValueError('Exit stream lacks a held position or causal clock')
    previous = decision_us
    attempts = filled = 0
    last = None
    for quote in quotes:
        if not isinstance(quote, Quote) or quote.bucket_end_us <= previous:
            raise ValueError('Exit quotes must have increasing later buckets')
        previous = last = quote.bucket_end_us
        if ticker not in account.positions:
            raise ValueError('Quote stream continues after position close')
        retry_clock = max(decision_us, account.positions[ticker].last_action_us)
        filled += account.exit_long(ticker, decision_us=retry_clock,
                                    quote=quote, action=action)
        attempts += 1
        if ticker not in account.positions:
            break
    remaining = (account.positions[ticker].shares
                 if ticker in account.positions else 0)
    return ExitStreamResult(attempts, filled, remaining, last)
