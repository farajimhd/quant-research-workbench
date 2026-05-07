from AlgorithmImports import *

from market_tools import MarketTools
from state import MomentumState


class EntryLogicMixin:

    def try_enter(self, symbol, state, bar):
        entry = float(bar.Close)
        reject_reason = self.breakout_reject_reason(state, bar, entry, True)

        if reject_reason is not None:
            self.count_entry_reject(reject_reason)
            state.state = self.entry_watch_state(state)
            return

        breakout_high = MarketTools.highest_high(
            state,
            self.leader_breakout_lookback_bars,
            exclude_current=True,
        )

        self.try_enter_at_price(symbol, state, bar, entry, breakout_high)

    def try_enter_on_quote(self, symbol, state):
        if state.last_ask is None or state.last_ask <= 0:
            return

        if len(state.bars) == 0:
            return

        bar = list(state.bars)[-1]
        entry = float(state.last_ask)
        breakout_high = self.quote_breakout_level(state)

        if breakout_high <= 0:
            return

        required_margin = self.quote_breakout_margin(state)

        if entry <= breakout_high * (1.0 + required_margin):
            return

        reject_reason = self.breakout_reject_reason(
            state,
            bar,
            entry,
            False,
            extension_level=breakout_high,
        )

        if reject_reason is not None:
            self.count_entry_reject(reject_reason)
            return

        self.debugger.log_breakout_ready(symbol, entry, breakout_high)
        self.try_enter_at_price(symbol, state, bar, entry, breakout_high)

    def quote_breakout_level(self, state):
        chart_high = MarketTools.highest_high(
            state,
            self.leader_breakout_lookback_bars,
            exclude_current=False,
        )

        if state.state == MomentumState.PULLBACK_WATCH and state.last_breakout_high is not None:
            return max(chart_high, state.last_breakout_high)

        return chart_high

    def quote_breakout_margin(self, state):
        margin = self.min_breakout_margin_pct

        if (
            state.state == MomentumState.PULLBACK_WATCH
            and state.last_exit_r is not None
            and state.last_exit_r <= self.max_failed_reentry_r
        ):
            margin += self.reentry_extra_margin_pct

        return margin

    def try_enter_at_price(self, symbol, state, bar, entry, breakout_high):
        if not self.can_enter_symbol(state):
            self.debugger.count_reject("cool")
            state.state = self.entry_watch_state(state)
            return

        if not self.is_entry_allowed_now(symbol):
            self.debugger.count_reject("market")
            state.state = self.entry_watch_state(state)
            return

        if not self.has_marketable_quote(symbol):
            self.debugger.count_reject("no_quote")
            state.state = self.entry_watch_state(state)
            return

        rv = self.estimate_relative_volume(state)
        spread = MarketTools.spread_pct(self.algorithm, symbol, entry)
        max_spread = self.allowed_spread_pct(entry, rv)

        if spread > max_spread:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"spread|sp={spread * 100:.2f}|max={max_spread * 100:.2f}|rv={rv:.1f}|p={entry:.2f}",
            )
            self.debugger.count_reject("spread")
            state.state = self.entry_watch_state(state)
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
            self.debugger.count_reject("stop")
            state.state = self.entry_watch_state(state)
            return

        risk = entry - stop
        if risk <= 0:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"bad_risk|p={entry:.2f}|sl={stop:.2f}|risk={risk:.4f}",
            )
            self.debugger.count_reject("risk")
            state.state = self.entry_watch_state(state)
            return

        spread_dollars = spread * entry

        if spread_dollars / risk > self.max_spread_to_risk:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"spread_risk|sp={spread_dollars:.2f}|risk={risk:.2f}|p={entry:.2f}",
            )
            self.debugger.count_reject("spread_risk")
            state.state = self.entry_watch_state(state)
            return

        quantity = self.risk.calculate_quantity(entry, stop)
        quantity = self.apply_initial_position_fraction(quantity)

        if quantity <= 0:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"qty_zero|p={entry:.2f}|sl={stop:.2f}|risk={risk:.2f}|cash={self.algorithm.Portfolio.Cash:.0f}",
            )
            self.debugger.count_reject("qty")
            state.state = self.entry_watch_state(state)
            return

        self.debugger.log_entry(
            symbol=symbol,
            entry=entry,
            stop=stop,
            risk=risk,
            quantity=quantity,
            cash=float(self.algorithm.Portfolio.Cash),
            breakout_high=breakout_high,
        )

        ticket = self.submit_entry_order(symbol, quantity)

        if ticket is None:
            self.debugger.count_reject("order")
            state.state = self.entry_watch_state(state)
            return

        state.pending_entry_order_id = ticket.OrderId
        state.pending_entry_signal_price = entry
        state.pending_entry_stop_price = stop
        state.pending_entry_quantity = quantity
        state.pending_entry_time = self.algorithm.Time
        state.state = MomentumState.PENDING_ENTRY

        if ticket.Status == OrderStatus.Filled:
            self.handle_entry_ticket_fill(symbol, state, ticket)

    def count_entry_reject(self, reason):
        if reason == "no_break":
            self.debugger.count_reject("no_break")
        elif reason == "extended":
            self.debugger.count_reject("extended")
        else:
            self.debugger.count_reject("setup")

    def entry_watch_state(self, state):
        if state.state == MomentumState.PULLBACK_WATCH:
            return MomentumState.PULLBACK_WATCH

        return MomentumState.LEADER_WATCH

    def apply_initial_position_fraction(self, quantity):
        fraction = getattr(self, "initial_position_fraction", 1.0)

        if fraction >= 1.0:
            return quantity

        return max(1, int(quantity * fraction))

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

        if MarketTools.is_regular_market_order_safe(
            self.algorithm,
            symbol,
            self.regular_market_order_close_buffer_minutes,
        ):
            return self.algorithm.MarketOrder(symbol, quantity, tag=tag)

        bid, ask = MarketTools.bid_ask(self.algorithm, symbol)

        if bid is None or ask is None:
            return None

        limit_price = ask * (1.0 + self.extended_hours_limit_buffer_pct)

        return self.algorithm.LimitOrder(
            symbol,
            quantity,
            limit_price,
            tag=f"{tag}|ext_limit",
        )
