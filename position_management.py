from AlgorithmImports import *

from market_tools import MarketTools
from state import MomentumState


class PositionManagementMixin:

    def manage_position(self, symbol, state, bar):
        if state.pending_exit_order_id is not None:
            return

        price = float(bar.Close)
        high = float(bar.High)
        low = float(bar.Low)

        state.highest_since_entry = max(state.highest_since_entry, high)
        state.lowest_since_entry = min(state.lowest_since_entry, low)

        risk = state.entry_price - state.initial_stop_price

        if risk <= 0:
            return

        r = (price - state.entry_price) / risk
        mfe_r = (state.highest_since_entry - state.entry_price) / risk
        mfe_pct = (state.highest_since_entry - state.entry_price) / state.entry_price

        if self.should_exit_for_acceleration_pullback(state, price, mfe_r, mfe_pct):
            self.exit_to_pullback_watch(
                symbol,
                state,
                price,
                r,
                "PROFIT_PULLBACK",
                force_limit=True,
            )
            return

        if self.should_exit_for_entry_failure(state, low, mfe_r, mfe_pct):
            self.exit_to_pullback_watch(symbol, state, price, r, "ENTRY_FAIL", force_limit=True)
            return

        self.update_profit_protection_stop(state, r, mfe_r, mfe_pct)

        if price <= state.stop_price:
            self.exit_to_pullback_watch(symbol, state, price, r, "STOP")
            return

        if self.should_exit_for_no_progress(state, price, r, mfe_r):
            self.exit_to_pullback_watch(symbol, state, price, r, "NO_PROGRESS")
            return

        if self.is_true_structure_failure(state, bar):
            self.exit_to_pullback_watch(symbol, state, price, r, "STRUCT_FAIL")
            return

        trailing_stop = self.calculate_structure_trailing_stop(state, r)

        if trailing_stop is not None:
            state.stop_price = max(state.stop_price, trailing_stop)

    def update_profit_protection_stop(self, state, r, mfe_r, mfe_pct):
        if mfe_r >= self.move_stop_to_be_at_r:
            state.stop_price = max(state.stop_price, state.entry_price)
            state.stop_moved_to_breakeven = True

        if mfe_r < self.protect_after_mfe_r and mfe_pct < self.protect_after_mfe_pct:
            return

        locked_r = max(
            self.min_locked_profit_r,
            mfe_r * (1.0 - self.protected_giveback_pct),
        )

        protected_stop = state.entry_price + locked_r * (
            state.entry_price - state.initial_stop_price
        )

        state.stop_price = max(state.stop_price, protected_stop)

    def should_exit_for_no_progress(self, state, price, r, mfe_r):
        if not getattr(self, "enable_progress_exit", True):
            return False

        if state.entry_time is None or state.entry_price is None:
            return False

        elapsed = self.algorithm.Time - state.entry_time
        minutes = elapsed.total_seconds() / 60.0

        if minutes < self.progress_check_minutes:
            return False

        if mfe_r >= self.protect_after_mfe_r:
            return False

        pnl_pct = (price - state.entry_price) / state.entry_price

        return r < self.min_progress_r and pnl_pct < self.min_progress_pct

    def should_exit_for_entry_failure(self, state, low, mfe_r, mfe_pct):
        if not getattr(self, "enable_entry_failure_exit", True):
            return False

        if state.entry_price is None:
            return False

        if mfe_pct >= self.acceleration_min_mfe_pct or mfe_r >= self.acceleration_min_mfe_r:
            return False

        failure_level = state.entry_price * (1.0 - self.entry_failure_buffer_pct)

        return low <= failure_level

    def should_exit_for_acceleration_pullback(self, state, price, mfe_r, mfe_pct):
        if not getattr(self, "enable_acceleration_pullback_exit", True):
            return False

        if state.entry_price is None or state.highest_since_entry is None:
            return False

        if mfe_pct < self.acceleration_min_mfe_pct and mfe_r < self.acceleration_min_mfe_r:
            return False

        move = state.highest_since_entry - state.entry_price

        if move <= 0:
            return False

        pullback_level = state.highest_since_entry - (
            move * self.acceleration_pullback_giveback_pct
        )

        return price <= pullback_level

    def is_true_structure_failure(self, state, bar):
        if state.last_pullback_low is None:
            return False

        close = float(bar.Close)

        if state.expansion_base is not None and close < state.expansion_base:
            return True

        if not MarketTools.is_structural_failure(state, bar):
            return False

        if close >= state.last_pullback_low:
            return False

        return True

    def calculate_structure_trailing_stop(self, state, r):
        if state.last_pullback_low is None:
            return None

        stop = state.last_pullback_low * (1.0 - self.stop_buffer_pct)

        if r >= self.move_stop_to_be_at_r:
            stop = max(stop, state.entry_price)

        return stop

    def close_position_now(self, symbol, reason, force_limit=False):
        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return None

        if not self.is_exit_allowed_now(symbol):
            self.debugger.c_log("RX", symbol, f"skip_exit_market_closed|reason={reason}")
            return None

        if not self.has_marketable_quote(symbol):
            self.debugger.c_log("RX", symbol, f"skip_exit_no_quote|reason={reason}")
            return None

        if not force_limit and MarketTools.is_regular_market_order_safe(
            self.algorithm,
            symbol,
            self.regular_market_order_close_buffer_minutes,
        ):
            return self.algorithm.MarketOrder(
                symbol,
                -quantity,
                tag=reason,
            )

        bid, ask = MarketTools.bid_ask(self.algorithm, symbol)

        if bid is None or ask is None:
            self.debugger.c_log("RX", symbol, f"skip_exit_no_quote|reason={reason}")
            return None

        limit_price = bid * (1.0 - self.extended_hours_limit_buffer_pct)

        return self.algorithm.LimitOrder(
            symbol,
            -quantity,
            limit_price,
            tag=f"{reason}|ext_limit",
        )

    def exit_to_pullback_watch(self, symbol, state, price, r, reason, force_limit=False):
        state.last_breakout_high = state.highest_since_entry

        state.last_pullback_low = MarketTools.lowest_low(
            state,
            self.stop_lookback_bars,
            exclude_current=False,
        )

        self.debugger.log_exit(
            symbol=symbol,
            reason=reason,
            price=price,
            r_multiple=r,
            extra=f"rebreak={state.last_breakout_high:.2f}",
        )

        ticket = self.close_position_now(symbol, reason, force_limit=force_limit)

        if ticket is None:
            return

        state.pending_exit_order_id = ticket.OrderId
        state.pending_exit_reason = reason
        state.pending_exit_r = r
        state.pending_exit_time = self.algorithm.Time
        state.state = MomentumState.PENDING_EXIT

        if ticket.Status == OrderStatus.Filled:
            self.handle_exit_ticket_fill(symbol, state, ticket)

    def handle_pullback_watch(self, symbol, state, bar):
        price = float(bar.Close)

        if self.is_leader_dead(state, bar):
            self.debugger.log_dead_leader(symbol, "dead_after_exit", price)
            self.reset_to_quiet(state)
            return

        self.update_leader_structure(symbol, state, bar)

        if not self.can_enter_symbol(state):
            return

        if self.reentry_requires_new_leader_high:
            if state.leader_high is None or state.last_breakout_high is None:
                return

            required_margin = self.min_breakout_margin_pct

            if (
                state.last_exit_r is not None
                and state.last_exit_r <= self.max_failed_reentry_r
            ):
                required_margin += self.reentry_extra_margin_pct

            if price <= state.last_breakout_high * (1.0 + required_margin):
                return

        if not self.is_explosive_breakout_candidate(state, bar):
            return

        self.debugger.log_breakout_ready(
            symbol,
            price,
            state.last_breakout_high or state.leader_high,
        )

        self.try_enter(symbol, state, bar)

    def reset_to_quiet(self, state):
        state.state = MomentumState.QUIET

        state.expansion_time = None
        state.expansion_high = None
        state.expansion_low = None
        state.expansion_base = None

        state.leader_high = None
        state.leader_low = None

        state.last_material_high = None
        state.last_material_high_time = None
        state.bars_since_material_high = 0

        state.last_breakout_high = None
        state.last_pullback_low = None
        state.last_consolidation_high = None
        state.last_consolidation_low = None

        state.reentry_attempts = 0
        state.reset_pending_entry()
        state.reset_pending_exit()
        state.reset_trade_fields()

    def is_in_cooldown(self, state):
        if state.state != MomentumState.COOLDOWN:
            return False

        if state.last_exit_time is None:
            return False

        elapsed = self.algorithm.Time - state.last_exit_time

        if elapsed.total_seconds() >= self.cooldown_minutes * 60:
            state.state = MomentumState.LEADER_WATCH
            return False

        return True
