"""Quote-bound research OMS for one-entry, one-bracket long positions.

This books only an explicit optimistic displayed-liquidity scenario. Top-of-
book size and executed price-level volume are upper bounds, not broker fill
proof. Every modeled order and unresolved remainder is retained in a ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from research.rl_trading.v2.config import Config, share_cap
from research.rl_trading.v2.fees import charges


VERSION = 'rl-trading-quote-bound-bracket-oms-v6'
MAX_QUOTE_AGE_US = 1_000_000


@dataclass(frozen=True)
class Quote:
    bucket_end_us: int
    quote_us: int
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    valid: bool

    def fresh(self) -> bool:
        return (self.valid and type(self.bucket_end_us) is int and
                type(self.quote_us) is int and
                0 <= self.bucket_end_us-self.quote_us <= MAX_QUOTE_AGE_US and
                all(math.isfinite(value) for value in
                    (self.bid, self.ask, self.bid_size, self.ask_size)) and
                0 < self.bid <= self.ask and self.bid_size > 0 and
                self.ask_size > 0)


@dataclass
class Position:
    ticker: str
    shares: int
    entry_price: float
    entry_fee_remaining: float
    entry_us: int
    last_action_us: int
    stop: float | None = None
    target: float | None = None
    stop_pending: bool = False


class BracketAccount:
    def __init__(self, *, config: Config = Config(),
                 sweep_teacher_profits: bool = False):
        self.config = config
        self.initial_cash = float(config.initial_cash)
        self.cash = self.initial_cash
        self.profit_bank = 0.
        self.sweep_teacher_profits = sweep_teacher_profits
        self.positions: dict[str, Position] = {}
        self.orders: list[dict] = []
        self.closed: list[dict] = []
        self.fees = 0.
        # Append-order aggregates mirror the ledgers without rescanning all
        # historical executions at every causal account observation.
        self.realized_net = 0.
        self.latest_order_us: int | None = None

    def _fee(self, shares: int, price: float, side: int) -> float:
        return sum(charges(shares, price, side, self.config).values())

    def _record(self, *, action: str, ticker: str, clock: int,
                requested: int, filled: int, price: float | None,
                fee: float, reason: str | None = None) -> None:
        self.orders.append({'action': action, 'ticker': ticker,
            'bucket_end_us': clock, 'requested_shares': requested,
            'filled_shares': filled, 'price': price, 'fee': fee,
            'status': ('unfilled' if filled == 0 else
                       'filled' if filled == requested else 'partial'),
            'reason': reason,
            'fill_scenario': 'optimistic_displayed_liquidity_upper_bound'})
        self.latest_order_us = clock if self.latest_order_us is None else max(self.latest_order_us, clock)

    def enter_long(self, ticker: str, *, decision_us: int,
                   decision_close: float, budget: float,
                   quote: Quote) -> int:
        """Submit at a later fresh ask, capped by displayed ask and v2 share rule."""
        if (not ticker or ticker in self.positions or
                not math.isfinite(decision_close) or decision_close <= 0 or
                not math.isfinite(budget) or budget < 0 or
                type(decision_us) is not int or
                quote.bucket_end_us <= decision_us):
            raise ValueError('Invalid new long intent or decision clock')
        if not quote.fresh():
            self._record(action='enter_long', ticker=ticker,
                clock=quote.bucket_end_us, requested=0, filled=0,
                price=None, fee=0., reason='missing_or_stale_quote')
            return 0
        budget = min(budget, self.cash)
        cap = min(math.floor(quote.ask_size), share_cap(decision_close),
                  share_cap(quote.ask), math.floor(budget/quote.ask))
        low, high = 0, cap
        while low < high:
            middle = (low+high+1)//2
            affordable = middle*quote.ask+self._fee(middle, quote.ask, 1)
            if affordable <= budget + 1e-9:
                low = middle
            else:
                high = middle-1
        shares = low
        fee = self._fee(shares, quote.ask, 1) if shares else 0.
        if shares:
            self.cash -= shares*quote.ask+fee
            self.fees += fee
            self.positions[ticker] = Position(ticker, shares, quote.ask, fee,
                quote.bucket_end_us, quote.bucket_end_us)
        self._record(action='enter_long', ticker=ticker,
            clock=quote.bucket_end_us, requested=cap, filled=shares,
            price=quote.ask if shares else None, fee=fee,
            reason=None if shares else 'cash_or_displayed_size_below_one_share')
        return shares

    def set_stop(self, ticker: str, *, price: float, clock_us: int) -> None:
        position = self.positions[ticker]
        if (position.stop is not None or position.stop_pending or
                clock_us < position.last_action_us or clock_us <= position.entry_us or not math.isfinite(price) or
                not 0 < price < position.entry_price):
            raise ValueError('Stop can be set once, only after a confirmed fill')
        position.stop = price
        position.last_action_us = clock_us
        self._record(action='set_stop', ticker=ticker, clock=clock_us,
                     requested=position.shares, filled=0, price=price,
                     fee=0., reason='child_order_armed_no_fill')

    def set_target(self, ticker: str, *, price: float, clock_us: int) -> None:
        position = self.positions[ticker]
        if (position.target is not None or clock_us < position.last_action_us or clock_us <= position.entry_us or
                not math.isfinite(price) or price <= position.entry_price):
            raise ValueError('Target can be set once, only after a confirmed fill')
        position.target = price
        position.last_action_us = clock_us
        self._record(action='set_target', ticker=ticker, clock=clock_us,
                     requested=position.shares, filled=0, price=price,
                     fee=0., reason='child_order_armed_no_fill')

    def _sell(self, ticker: str, shares: int, price: float, clock_us: int,
              action: str) -> int:
        position = self.positions[ticker]
        requested = position.shares
        quantity = min(shares, requested)
        if quantity <= 0:
            self._record(action=action, ticker=ticker, clock=clock_us,
                         requested=position.shares, filled=0, price=None,
                         fee=0., reason='no_certified_liquidity')
            return 0
        fee = self._fee(quantity, price, -1)
        entry_fee = position.entry_fee_remaining*quantity/position.shares
        gross = quantity*(price-position.entry_price)
        net = gross-entry_fee-fee
        self.cash += quantity*price-fee
        self.fees += fee
        position.shares -= quantity
        position.entry_fee_remaining -= entry_fee
        position.last_action_us = clock_us
        self.closed.append({'ticker': ticker, 'entry_us': position.entry_us,
            'exit_us': clock_us, 'shares': quantity,
            'entry_price': position.entry_price, 'exit_price': price,
            'entry_fee': entry_fee, 'exit_fee': fee,
            'gross_pnl': gross, 'net_pnl': net, 'exit_action': action})
        self.realized_net += net
        self._record(action=action, ticker=ticker, clock=clock_us,
                     requested=requested, filled=quantity, price=price, fee=fee)
        if position.shares == 0:
            del self.positions[ticker]
        if self.sweep_teacher_profits:
            invested = sum(p.shares*p.entry_price+p.entry_fee_remaining
                           for p in self.positions.values())
            sweep = max(0., self.cash+invested-self.initial_cash)
            self.cash -= sweep
            self.profit_bank += sweep
        return quantity

    def target_bucket(self, ticker: str, *, clock_us: int,
                      price_level_volume_cap: float,
                      stop_touched: bool = False) -> int:
        """Optimistic possible limit fill; stop collision takes precedence."""
        position = self.positions[ticker]
        if (position.target is None or position.stop_pending or
                clock_us <= position.last_action_us or
                not math.isfinite(price_level_volume_cap) or
                price_level_volume_cap < 0):
            raise ValueError('Invalid target bucket evidence')
        if stop_touched:
            position.stop_pending = True
            self._record(action='target_collision', ticker=ticker,
                clock=clock_us, requested=position.shares, filled=0,
                price=None, fee=0., reason='same_bucket_stop_target_ambiguous')
            return 0
        return self._sell(ticker, math.floor(price_level_volume_cap),
                          position.target, clock_us, 'target_fill_upper_bound')

    def stop_trigger(self, ticker: str, *, clock_us: int) -> None:
        position = self.positions[ticker]
        if position.stop is None or clock_us <= position.last_action_us:
            raise ValueError('Stop trigger precedes armed child or entry fill')
        position.stop_pending = True
        position.last_action_us = clock_us
        self._record(action='stop_trigger', ticker=ticker, clock=clock_us,
                     requested=position.shares, filled=0, price=None, fee=0.,
                     reason='sell_at_next_fresh_bid_not_same_bucket')

    def exit_long(self, ticker: str, *, decision_us: int,
                  quote: Quote, action: str = 'exit_long') -> int:
        """Manual or triggered full exit at a later fresh bid, possibly partial."""
        position = self.positions[ticker]
        if (action not in ('exit_long', 'stop_market') or
                decision_us < position.last_action_us or
                quote.bucket_end_us <= decision_us):
            raise ValueError('Exit must follow a held position and decision')
        if not quote.fresh():
            self._record(action=action, ticker=ticker,
                clock=quote.bucket_end_us, requested=position.shares,
                filled=0, price=None, fee=0., reason='missing_or_stale_quote')
            return 0
        quantity = min(position.shares, math.floor(quote.bid_size))
        sold = self._sell(ticker, quantity, quote.bid, quote.bucket_end_us,
                          action)
        # Once a manual or stop exit begins, no child target remains active.
        if ticker in self.positions:
            self.positions[ticker].target = None
            self.positions[ticker].stop_pending = True
        return sold

    def marked_equity(self, marks: dict[str, float]) -> float:
        if any(ticker not in marks or not math.isfinite(marks[ticker]) or
               marks[ticker] <= 0 for ticker in self.positions):
            raise ValueError('Missing or invalid causal mark for held listing')
        return (self.cash+self.profit_bank+
                sum(position.shares*marks[ticker]
                    for ticker, position in self.positions.items()))
