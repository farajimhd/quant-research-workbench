from AlgorithmImports import *

from market_tools import MarketTools
from state import MomentumState


class EntryLogicMixin:

    def try_enter(self, symbol, state, bar):
        if not self.can_enter_symbol(state):
            self.debugger.c_log("RJ", symbol, "entry_throttle")
            state.state = MomentumState.LEADER_WATCH
            return

        if not self.is_entry_allowed_now(symbol):
            self.debugger.c_log("RJ", symbol, "entry_market_closed")
            state.state = MomentumState.LEADER_WATCH
            return

        if not self.has_marketable_quote(symbol):
            self.debugger.c_log("RJ", symbol, "entry_not_marketable")
            state.state = MomentumState.LEADER_WATCH
            return

        if not self.is_explosive_breakout_candidate(state, bar):
            self.debugger.c_log("RJ", symbol, "not_explosive_now")
            state.state = MomentumState.LEADER_WATCH
            return

        entry = float(bar.Close)
        rv = self.estimate_relative_volume(state)
        spread = MarketTools.spread_pct(self.algorithm, symbol, entry)
        max_spread = self.allowed_spread_pct(entry, rv)

        if spread > max_spread:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"spread|sp={spread * 100:.2f}|max={max_spread * 100:.2f}|rv={rv:.1f}|p={entry:.2f}",
            )
            state.state = MomentumState.LEADER_WATCH
            return

        stop = self.calculate_dynamic_chart_stop(state, entry)

        if stop is None:
            recent_low = MarketTools.lowest_low(
                state,
                self.stop_lookback_bars,
                exclude_current=False,
            )

            stop_distance_pct = 0.0

            if recent_low > 0 and entry > 0:
                buffered = recent_low * (1.0 - self.stop_buffer_pct)
                stop_distance_pct = (entry - buffered) / entry

            self.debugger.c_log(
                "RJ",
                symbol,
                f"stop_none|p={entry:.2f}|low={recent_low:.2f}|sd={stop_distance_pct * 100:.2f}|max={self.hard_max_stop_pct * 100:.2f}",
            )
            state.state = MomentumState.LEADER_WATCH
            return

        risk = entry - stop
        risk_pct = risk / entry if entry > 0 else 0.0

        if risk <= 0:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"bad_risk|p={entry:.2f}|sl={stop:.2f}|risk={risk:.4f}",
            )
            state.state = MomentumState.LEADER_WATCH
            return

        quantity = self.risk.calculate_quantity(entry, stop)

        if quantity <= 0:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"qty_zero|p={entry:.2f}|sl={stop:.2f}|risk={risk:.2f}|cash={self.algorithm.Portfolio.Cash:.0f}",
            )
            state.state = MomentumState.LEADER_WATCH
            return

        breakout_high = MarketTools.highest_high(
            state,
            self.leader_breakout_lookback_bars,
            exclude_current=True,
        )

        self.debugger.log_entry(
            symbol=symbol,
            entry=entry,
            stop=stop,
            risk=risk,
            quantity=quantity,
            cash=float(self.algorithm.Portfolio.Cash),
            breakout_high=breakout_high,
        )

        order_submitted = self.submit_entry_order(symbol, quantity)

        if not order_submitted:
            self.debugger.c_log("RJ", symbol, "entry_order_rejected")
            state.state = MomentumState.LEADER_WATCH
            return

        state.entry_price = entry
        state.initial_stop_price = stop
        state.stop_price = stop
        state.quantity = quantity

        state.highest_since_entry = entry
        state.lowest_since_entry = entry

        state.scout_reduced = False
        state.slow_reduced = False
        state.soft_failure_reduced = False
        state.stop_moved_to_breakeven = False

        state.entry_time = self.algorithm.Time
        state.last_entry_time = self.algorithm.Time
        state.state = MomentumState.IN_POSITION

        self.debugger.c_log(
            "OK",
            symbol,
            f"entered|p={entry:.2f}|sl={stop:.2f}|risk={risk_pct * 100:.2f}%|q={quantity}",
        )

    def calculate_dynamic_chart_stop(self, state, entry):
        if len(state.bars) < self.stop_lookback_bars:
            return None

        recent_low = MarketTools.lowest_low(
            state,
            self.stop_lookback_bars,
            exclude_current=False,
        )

        if recent_low <= 0:
            return None

        stop = recent_low * (1.0 - self.stop_buffer_pct)
        stop_distance_pct = (entry - stop) / entry

        if stop_distance_pct < self.min_stop_pct:
            stop = entry * (1.0 - self.min_stop_pct)
            stop_distance_pct = self.min_stop_pct

        if stop_distance_pct > self.hard_max_stop_pct:
            return None

        return stop

    def can_enter_symbol(self, state):
        if state.failed_trade_count >= self.max_failed_trades_before_symbol_cooldown:
            if state.last_failed_trade_time is None:
                return False

            elapsed_failure = self.algorithm.Time - state.last_failed_trade_time

            if elapsed_failure.total_seconds() < self.failed_symbol_cooldown_minutes * 60:
                return False

            state.failed_trade_count = 0
            state.last_failed_trade_time = None

        if state.last_entry_time is None:
            return True

        elapsed = self.algorithm.Time - state.last_entry_time

        return elapsed.total_seconds() >= self.min_minutes_between_same_symbol_entries * 60

    def is_entry_allowed_now(self, symbol):
        if self.allow_extended_hours_entries:
            return MarketTools.is_market_open(
                self.algorithm,
                symbol,
                extended_market_hours=True,
            )

        return MarketTools.is_market_open(
            self.algorithm,
            symbol,
            extended_market_hours=False,
        )

    def is_exit_allowed_now(self, symbol):
        if self.allow_extended_hours_exits:
            return MarketTools.is_market_open(
                self.algorithm,
                symbol,
                extended_market_hours=True,
            )

        return MarketTools.is_market_open(
            self.algorithm,
            symbol,
            extended_market_hours=False,
        )

    def has_marketable_quote(self, symbol):
        security = self.algorithm.Securities[symbol]

        if not security.HasData:
            return False

        bid, ask = MarketTools.bid_ask(self.algorithm, symbol)

        return bid is not None and ask is not None

    def submit_entry_order(self, symbol, quantity):
        tag = "leader explosive breakout entry"

        if MarketTools.is_regular_market_open(self.algorithm, symbol):
            self.algorithm.MarketOrder(symbol, quantity, tag=tag)
            return True

        bid, ask = MarketTools.bid_ask(self.algorithm, symbol)

        if bid is None or ask is None:
            return False

        limit_price = ask * (1.0 + self.extended_hours_limit_buffer_pct)

        self.algorithm.LimitOrder(
            symbol,
            quantity,
            limit_price,
            tag=f"{tag}|ext_limit",
        )

        return True
