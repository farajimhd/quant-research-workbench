"""Chronological quote-bound entry and exit queue for the V6 research OMS.

Decisions reserve cash at a completed candle close. A later quote bucket, not
the decision clock, changes account cash and confirmed holdings. Pending
exits retry across observed buckets and keep their remainder when evidence
ends. This queue does not supply quote evidence or certify broker fills.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from collections.abc import Iterator

from research.rl_trading.v6.oms import BracketAccount, Quote


@dataclass(frozen=True)
class QueueOutcome:
    source_close_us: int
    source_order_index: int
    bucket_end_us: int
    action: str
    ticker: str
    requested_shares: int
    filled_shares: int
    net_pnl: float


@dataclass(order=True)
class _Scheduled:
    bucket_end_us: int
    priority: int
    sequence: int
    action: str = field(compare=False)
    ticker: str = field(compare=False)
    decision_us: int = field(compare=False)
    order_index: int = field(compare=False)
    quote: Quote = field(compare=False)
    decision_close: float | None = field(default=None, compare=False)
    budget: float | None = field(default=None, compare=False)
    following: Iterator[Quote] | None = field(default=None, compare=False)


class CausalOrderQueue:
    def __init__(self, account: BracketAccount):
        self.account = account
        self._heap: list[_Scheduled] = []
        self._sequence = 0
        self._reserved_cash = 0.
        self._pending_entries: set[str] = set()
        self._pending_exits: set[str] = set()
        self._last_clock = 0

    @property
    def reserved_cash(self) -> float:
        return self._reserved_cash

    @property
    def pending_entries(self) -> frozenset[str]:
        return frozenset(self._pending_entries)

    @property
    def pending_exits(self) -> frozenset[str]:
        return frozenset(self._pending_exits)

    def _push(self, action: str, ticker: str, decision_us: int,
              order_index: int, quote: Quote, *,
              decision_close: float | None = None,
              budget: float | None = None,
              following: Iterator[Quote] | None = None) -> None:
        if quote.bucket_end_us <= decision_us:
            raise ValueError('Execution evidence precedes decision close')
        self._sequence += 1
        heapq.heappush(self._heap, _Scheduled(quote.bucket_end_us,
            0 if action == 'exit_long' else 1,
            self._sequence, action, ticker, decision_us, order_index,
            quote, decision_close, budget, following))

    def submit_entry(self, ticker: str, *, decision_us: int,
                     order_index: int, decision_close: float,
                     desired_budget: float, quote: Quote) -> float:
        """Reserve at most available trading cash until one arrival quote."""
        if (decision_us < self._last_clock or order_index < 0 or
                ticker in self._pending_entries or
                ticker in self.account.positions or
                not math.isfinite(desired_budget) or desired_budget < 0 or
                not math.isfinite(decision_close) or decision_close <= 0):
            raise ValueError('Entry conflicts with position or order clock')
        available = max(0., self.account.cash-self._reserved_cash)
        budget = min(desired_budget, available)
        self._push('enter_long', ticker, decision_us, order_index, quote,
                   decision_close=decision_close, budget=budget)
        self._reserved_cash += budget
        self._pending_entries.add(ticker)
        return budget

    def submit_exit(self, ticker: str, *, decision_us: int,
                    order_index: int, quotes: Iterator[Quote]) -> bool:
        """Queue first observed quote; return false if no evidence exists."""
        if (decision_us < self._last_clock or order_index < 0 or
                ticker not in self.account.positions or
                ticker in self._pending_exits):
            raise ValueError('Exit lacks held position or conflicts with queue')
        first = next(quotes, None)
        if first is None:
            self._pending_exits.add(ticker)
            return False
        self._push('exit_long', ticker, decision_us, order_index, first,
                   following=quotes)
        self._pending_exits.add(ticker)
        return True

    def advance_to(self, close_us: int) -> tuple[QueueOutcome, ...]:
        """Apply only quote buckets at/before this completed clock."""
        if type(close_us) is not int or close_us < self._last_clock:
            raise ValueError('Decision clocks must be monotone')
        outcomes = []
        while self._heap and self._heap[0].bucket_end_us <= close_us:
            order = heapq.heappop(self._heap)
            before_closed = len(self.account.closed)
            before_orders = len(self.account.orders)
            if order.action == 'enter_long':
                self._reserved_cash -= order.budget
                self._pending_entries.remove(order.ticker)
                self.account.enter_long(order.ticker,
                    decision_us=order.decision_us,
                    decision_close=order.decision_close,
                    budget=order.budget, quote=order.quote)
            else:
                position = self.account.positions[order.ticker]
                self.account.exit_long(order.ticker,
                    decision_us=max(order.decision_us,
                                    position.last_action_us),
                    quote=order.quote)
                if order.ticker in self.account.positions:
                    following = next(order.following, None)
                    if following is not None:
                        if following.bucket_end_us <= order.bucket_end_us:
                            raise ValueError('Exit quote stream goes backwards')
                        self._push('exit_long', order.ticker,
                            order.decision_us, order.order_index, following,
                            following=order.following)
                else:
                    self._pending_exits.remove(order.ticker)
            if len(self.account.orders) != before_orders+1:
                raise ValueError('OMS attempt did not write exactly one order')
            row = self.account.orders[-1]
            net = sum(closed['net_pnl']
                      for closed in self.account.closed[before_closed:])
            outcomes.append(QueueOutcome(order.decision_us,
                order.order_index, row['bucket_end_us'], order.action,
                order.ticker, row['requested_shares'],
                row['filled_shares'], net))
        self._last_clock = close_us
        return tuple(outcomes)
