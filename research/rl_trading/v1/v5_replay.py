"""Causal, cash-compounding execution account for V5 model decisions.

Decisions use a completed second and orders arrive at the next second's
observed opening price. This is a price-only research scenario: spread, queue
position, routing, and actual fills are unobserved. Every submitted order is
retained, including partial and unfilled orders. No teacher fixed-bankroll
or profit-bank rule is applied to this model replay.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np

from research.rl_trading.v2.config import Config, share_cap
from research.rl_trading.v2.fees import charges
from research.rl_trading.v2.estimated_luld import buffered_bounds, regular


@dataclass(frozen=True)
class ReplayGrid:
    tickers: tuple[str, ...]
    close: np.ndarray      # [N,T], last causally completed close carried forward
    next_open: np.ndarray  # [N,T], actual observed bar open at execution second
    volume: np.ndarray     # [N,T], actual one-second share volume
    fresh: np.ndarray      # [N,T], certified executable bar at that second
    prior_close: np.ndarray  # [N], certified prior regular-session close
    estimated_reference: np.ndarray  # [N,T], causal completed-second proxy

    def __post_init__(self):
        n = len(self.tickers)
        if (n < 1 or len(set(self.tickers)) != n or self.close.ndim != 2 or
                self.close.shape[0] != n or self.close.shape[1] < 2 or
                any(array.shape != self.close.shape for array in
                    (self.next_open, self.volume, self.fresh, self.estimated_reference)) or
                self.prior_close.shape != (n,) or
                np.any(~np.isfinite(self.close)) or np.any(self.close < 0) or
                np.any(~np.isfinite(self.next_open)) or np.any(self.next_open < 0) or
                np.any(~np.isfinite(self.volume)) or np.any(self.volume < 0) or
                np.any(~np.isfinite(self.prior_close)) or
                np.any(~np.isfinite(self.estimated_reference)) or
                self.fresh.dtype != np.bool_ or
                np.any(self.fresh & ((self.next_open <= 0) | (self.volume <= 0)))):
            raise ValueError('Invalid certified V5 replay execution grid')

    @property
    def seconds(self) -> int:
        return self.close.shape[1]


@dataclass(frozen=True)
class Intent:
    listing: int
    side: int  # +1 BUY or -1 full-position SELL
    cash_fraction: float = 0.  # fraction of remaining cash for BUY
    kind: str = 'model'  # model or terminal liquidation


@dataclass
class Position:
    listing: int
    shares: int
    entry_price: float
    entry_fee_remaining: float
    entry_second: int


class ReplayAccount:
    """One session of ordered next-second IOC executions and marked P&L."""

    def __init__(self, grid: ReplayGrid, config: Config):
        self.grid = grid
        self.config = config
        self.initial = float(config.initial_cash)
        self.cash = self.initial
        self.second = 0
        self.positions: dict[int, Position] = {}
        self.peak = self.initial
        self.max_drawdown = 0.
        self.max_open_positions = 0
        self.fees = self.traded_notional = self.slippage_dollars = 0.
        self.order_trace: list[dict] = []
        self.position_ledger: list[dict] = []
        self.equity_path = [self.initial]
        self.recent = deque()  # (fill_second, listing, filled_shares)
        self.recent_shares = np.zeros(len(grid.tickers), dtype=np.float64)

    def equity(self, second: int) -> float:
        if any(self.grid.close[listing, second] <= 0 for listing in self.positions):
            raise ValueError('Held listing has no causal completed mark')
        marked = sum(position.shares*float(self.grid.close[listing, second])
                     for listing, position in self.positions.items())
        value = self.cash+marked
        if not math.isfinite(value) or value <= 0:
            raise ValueError('Replay marked equity is nonpositive or nonfinite')
        return value

    def _slippage(self, listing: int, quantity: int, decision: int) -> float:
        price = self.grid.close[listing, max(0, decision-60):decision+1]
        usable = price[price > 0]
        changes = np.diff(np.log(usable))
        volatility = float(np.std(changes)) if len(changes) > 1 else 0.
        # A rolling slice is cheap per submitted order and avoids allocating a
        # second whole-market [N,T] matrix for a 57,601-second session.
        volume = max(float(np.sum(self.grid.volume[
            listing, max(0, decision-59):decision+1], dtype=np.float64)), 1.)
        participation = (self.recent_shares[listing]+quantity)/volume
        c = self.config
        ratio = (c.base_slippage_ratio + c.impact_ratio*math.sqrt(participation)
                 + c.volatility_slippage_ratio*volatility)
        if not math.isfinite(ratio) or ratio >= 1:
            raise ValueError('Replay slippage scenario left its valid domain')
        return ratio

    def _cost(self, listing: int, quantity: int, side: int,
              decision: int, arrival: int) -> tuple[float, float, float, dict]:
        ratio = self._slippage(listing, quantity, decision)
        price = float(self.grid.next_open[listing, arrival])*(1+side*ratio)
        pieces = charges(quantity, price, side, self.config)
        return price, float(sum(pieces.values())), ratio, pieces

    def _trace_unfilled(self, intent: Intent, decision: int, arrival: int,
                        requested: int, reason: str):
        self.order_trace.append(dict(ticker=self.grid.tickers[intent.listing],
            side='buy' if intent.side == 1 else 'sell', decision_second=decision,
            arrival_second=arrival, requested_shares=requested, filled_shares=0,
            fill_price=None, fee=0., cash_before=self.cash,
            slippage_ratio=None, status='unfilled', kind=intent.kind,
            reason=reason))

    def _execute(self, intent: Intent, decision: int, arrival: int):
        listing = intent.listing
        if intent.side == 1 and listing in self.positions:
            self._trace_unfilled(intent, decision, arrival, 0,
                                 'position_still_open_after_sale_attempt')
            return
        available = (bool(self.grid.fresh[listing, arrival]) and
                     self.grid.volume[listing, arrival] > 0)
        if intent.side == -1:
            position = self.positions[listing]
            requested = position.shares
        else:
            budget = self.cash*intent.cash_fraction
            previous = float(self.grid.close[listing, decision])
            requested = math.floor(budget/previous) if previous > 0 else 0
        if not available:
            self._trace_unfilled(intent, decision, arrival, requested,
                                 'no_certified_arrival_bar')
            return
        previous = float(self.grid.close[listing, decision])
        opening = float(self.grid.next_open[listing, arrival])
        if previous <= 0:
            self._trace_unfilled(intent, decision, arrival, requested,
                                 'no_completed_decision_price')
            return
        if intent.side == 1 and regular(arrival):
            reference = float(self.grid.estimated_reference[listing, decision])
            if self.grid.prior_close[listing] <= 0 or buffered_bounds(reference) is None:
                self._trace_unfilled(intent, decision, arrival, requested,
                                     'estimated_band_unavailable')
                return
        cap = min(share_cap(previous), share_cap(opening),
                  math.floor(self.config.max_volume_participation*
                             float(self.grid.volume[listing, arrival])))
        quantity = min(requested, cap)
        if intent.side == 1 and quantity > 0:
            budget = self.cash*intent.cash_fraction
            low, high = 0, quantity
            while low < high:
                mid = (low+high+1)//2
                price, fee, _, _ = self._cost(listing, mid, 1, decision, arrival)
                if mid*price+fee <= budget+1e-9:
                    low = mid
                else:
                    high = mid-1
            quantity = low
        if quantity <= 0:
            self._trace_unfilled(intent, decision, arrival, requested,
                                 'size_or_cash_below_one_share')
            return
        price, fee, slip, pieces = self._cost(listing, quantity, intent.side,
                                              decision, arrival)
        if intent.side == 1 and regular(arrival):
            lower, upper = buffered_bounds(float(
                self.grid.estimated_reference[listing, decision]))
            if not lower < price < upper:
                self._trace_unfilled(intent, decision, arrival, requested,
                                     'estimated_band_blocks_fill')
                return
        cash_before = self.cash
        if intent.side == 1:
            self.cash -= quantity*price+fee
            self.positions[listing] = Position(listing, quantity, price, fee, arrival)
        else:
            position = self.positions[listing]
            entry_fee = position.entry_fee_remaining*quantity/position.shares
            pnl = quantity*(price-position.entry_price)-entry_fee-fee
            self.cash += quantity*price-fee
            position.shares -= quantity
            position.entry_fee_remaining -= entry_fee
            self.position_ledger.append(dict(ticker=self.grid.tickers[listing],
                entry_second=position.entry_second, exit_second=arrival,
                holding_seconds=arrival-position.entry_second,
                shares=quantity, entry_price=position.entry_price,
                exit_price=price, entry_fee=entry_fee, exit_fee=fee,
                gross_pnl=quantity*(price-position.entry_price), net_pnl=pnl,
                exit_kind=('terminal' if intent.kind == 'terminal' else 'model_sell')))
            if position.shares == 0:
                del self.positions[listing]
        self.fees += fee
        self.traded_notional += quantity*opening
        self.slippage_dollars += quantity*opening*slip
        self.recent.append((arrival, listing, quantity))
        self.recent_shares[listing] += quantity
        self.order_trace.append(dict(ticker=self.grid.tickers[listing],
            side='buy' if intent.side == 1 else 'sell', decision_second=decision,
            arrival_second=arrival, requested_shares=requested,
            filled_shares=quantity, fill_price=price, fee=fee,
            fee_components=pieces, cash_before=cash_before,
            slippage_ratio=slip, kind=intent.kind,
            status='filled' if quantity == requested else 'partial', reason=None))

    def advance(self, intents: list[Intent]):
        """Execute ordered decisions from current close at next-second open."""
        if self.second >= self.grid.seconds-1:
            raise ValueError('Replay session has ended')
        seen = set()
        sold = set()
        for intent in intents:
            if (intent.side not in (-1, 1) or
                    intent.kind not in ('model', 'terminal') or
                    (intent.side == 1 and intent.kind != 'model') or
                    not 0 <= intent.listing < len(self.grid.tickers) or
                    not math.isfinite(intent.cash_fraction) or
                    not 0 <= intent.cash_fraction <= 1 or
                    (intent.side, intent.listing) in seen or
                    (intent.side == -1 and intent.listing not in self.positions) or
                    (intent.side == 1 and intent.listing in self.positions and
                     intent.listing not in sold)):
                raise ValueError('Invalid or duplicate model order')
            seen.add((intent.side, intent.listing))
            if intent.side == -1:
                sold.add(intent.listing)
        if any(intent.side == 1 for intent in intents) and any(
                intent.side == -1 for intent in intents):
            first_buy = next(i for i, intent in enumerate(intents) if intent.side == 1)
            if any(intent.side == -1 for intent in intents[first_buy:]):
                raise ValueError('Model sales must precede purchases')
        arrival = self.second+1
        while self.recent and self.recent[0][0] <= arrival-60:
            _, listing, quantity = self.recent.popleft()
            self.recent_shares[listing] -= quantity
        for intent in intents:
            self._execute(intent, self.second, arrival)
        self.second = arrival
        equity = self.equity(arrival)
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, (self.peak-equity)/self.peak)
        self.max_open_positions = max(self.max_open_positions, len(self.positions))
        self.equity_path.append(equity)
        if self.cash < -1e-6:
            raise ValueError('Model replay spent more than available cash')

    def summary(self) -> dict:
        """Separate realized positions, open marked value, and period results."""
        equity = self.equity(self.second)
        realized = sum(row['net_pnl'] for row in self.position_ledger)
        open_pnl = equity-self.initial-realized
        weights = sum(row['shares'] for row in self.position_ledger)
        periods = []
        for name, lo, hi in (('premarket', 0, 19_801),
                             ('regular', 19_801, 43_201),
                             ('after_hours', 43_201, self.grid.seconds)):
            if lo > self.second:
                continue
            end = min(self.second, hi-1)
            starting = (self.initial if lo == 0 else self.equity_path[lo-1])
            ending = self.equity_path[end]
            periods.append(dict(period=name, start_second=lo, end_second=end,
                                marked_net_profit=ending-starting,
                                complete=end == hi-1))
        return dict(net_profit=equity-self.initial, equity=equity, cash=self.cash,
            realized_position_pnl=realized, open_marked_pnl=open_pnl,
            fees=self.fees, slippage_dollars=self.slippage_dollars,
            max_drawdown=self.max_drawdown, traded_notional=self.traded_notional,
            turnover=self.traded_notional/self.initial,
            buy_fills=sum(row['side'] == 'buy' and row['filled_shares'] > 0
                          for row in self.order_trace),
            sell_fills=sum(row['side'] == 'sell' and row['filled_shares'] > 0
                           for row in self.order_trace),
            partial_orders=sum(row['status'] == 'partial' for row in self.order_trace),
            unfilled_orders=sum(row['status'] == 'unfilled' for row in self.order_trace),
            completed_position_rows=len(self.position_ledger),
            terminal_sell_fills=sum(row['side'] == 'sell' and
                row['kind'] == 'terminal' and row['filled_shares'] > 0
                for row in self.order_trace),
            max_open_positions=self.max_open_positions,
            open_positions=len(self.positions),
            share_weighted_holding_seconds=(sum(row['shares']*row['holding_seconds']
                for row in self.position_ledger)/weights if weights else None),
            periods=periods, modeled_execution='next_second_open_price_only',
            valid_terminal=self.second == self.grid.seconds-1 and not self.positions)
