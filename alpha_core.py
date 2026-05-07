from AlgorithmImports import *

from leader_logic import LeaderLogicMixin
from entry_logic import EntryLogicMixin
from position_management import PositionManagementMixin

from state import MomentumState


class MomentumAlphaCore(
    LeaderLogicMixin,
    EntryLogicMixin,
    PositionManagementMixin,
):

    def __init__(self, algorithm, debugger, risk_manager):
        self.algorithm = algorithm
        self.debugger = debugger
        self.risk = risk_manager

        # =============================================================================
        # Abnormal Expansion Detection
        # =============================================================================
        self.expansion_windows = [3, 5, 10]
        self.min_expansion_move = 0.05
        self.required_relative_volume = 5.0

        # =============================================================================
        # Price / Volume Filters
        # =============================================================================
        self.min_price = 0.75
        self.max_price = 50.0
        self.min_bar_volume = 25_000
        self.min_recent_5min_volume = 100_000
        self.min_recent_5min_dollar_volume = 250_000

        # =============================================================================
        # Dynamic Spread Filters
        # =============================================================================
        self.low_price_spread_pct = 0.004
        self.mid_price_spread_pct = 0.006
        self.high_price_spread_pct = 0.008
        self.max_spread_momentum_multiplier = 2.0

        # =============================================================================
        # Extended-Hours Execution Guard
        # =============================================================================
        self.allow_extended_hours_signals = True
        self.allow_extended_hours_entries = True
        self.allow_extended_hours_exits = True

        # =============================================================================
        # Leader-Watch Logic
        # =============================================================================
        self.leader_dies_volume_bars = 20
        self.leader_dies_min_recent_volume = 50_000
        self.leader_breakout_lookback_bars = 6
        self.consolidation_lookback_bars = 5
        self.max_consolidation_range_pct = 0.035

        self.material_new_high_pct = 0.005
        self.max_bars_without_material_new_high = 60

        # =============================================================================
        # Breakout Filters
        # =============================================================================
        self.min_breakout_margin_pct = 0.0025
        self.min_breakout_body_pct = 0.004
        self.min_close_location = 0.72
        self.breakout_volume_multiplier = 1.20
        self.min_leader_watch_minutes = 2
        self.max_breakout_extension_pct = 0.035

        # =============================================================================
        # Re-entry Throttle
        # =============================================================================
        self.min_minutes_between_same_symbol_entries = 8
        self.reentry_requires_new_leader_high = True
        self.reentry_extra_margin_pct = 0.010
        self.max_failed_reentry_r = 0.0
        self.max_failed_trades_before_symbol_cooldown = 2
        self.failed_symbol_cooldown_minutes = 60

        # =============================================================================
        # Stop Configuration
        # =============================================================================
        self.stop_lookback_bars = 5
        self.stop_buffer_pct = 0.0025
        self.min_stop_pct = 0.004
        self.hard_max_stop_pct = 0.08
        self.max_spread_to_risk = 0.35

        # =============================================================================
        # Clean No-Progress Exit
        # =============================================================================
        self.enable_progress_exit = True
        self.progress_check_minutes = 8
        self.min_progress_r = 0.10
        self.min_progress_pct = 0.0

        # =============================================================================
        # Fast Breakout Failure Exit
        # =============================================================================
        self.enable_entry_failure_exit = True
        self.entry_failure_buffer_pct = 0.0030
        self.entry_failure_confirmations_required = 2
        self.entry_failure_fast_quote_max_seconds = 10
        self.entry_failure_confirm_window_seconds = 90

        # =============================================================================
        # Profit Protection
        # =============================================================================
        self.enable_acceleration_pullback_exit = True
        self.acceleration_min_mfe_pct = 0.03
        self.acceleration_min_mfe_r = 1.0
        self.acceleration_pullback_giveback_pct = 0.25
        self.move_stop_to_be_at_r = 1.0
        self.protect_after_mfe_r = 1.0
        self.protect_after_mfe_pct = 0.02
        self.protected_giveback_pct = 0.45
        self.min_locked_profit_r = 0.25

        # =============================================================================
        # Simplification For Testing Core Edge
        # =============================================================================
        self.enable_pyramiding = True
        self.enable_partial_exits = False
        self.initial_position_fraction = 0.70
        self.confirmation_add_fraction = 0.30
        self.add_after_mfe_r = 0.75
        self.add_after_mfe_pct = 0.015
        self.min_minutes_before_add = 1

        # =============================================================================
        # Re-entry Configuration
        # =============================================================================
        self.max_reentries = 1
        self.cooldown_minutes = 20

        # =============================================================================
        # Execution Controls
        # =============================================================================
        self.extended_hours_limit_buffer_pct = 0.002
        self.regular_market_order_close_buffer_minutes = 5

    # =============================================================================
    # Main Per-Symbol Processor
    # =============================================================================

    def process_symbol(self, symbol, state, bar):
        if self.is_in_cooldown(state):
            return

        if state.state in (MomentumState.PENDING_ENTRY, MomentumState.PENDING_EXIT):
            return

        if state.state == MomentumState.QUIET:
            self.handle_quiet(symbol, state, bar)

        elif state.state == MomentumState.LEADER_WATCH:
            self.handle_leader_watch(symbol, state, bar)

        elif state.state == MomentumState.BREAKOUT_READY:
            self.try_enter(symbol, state, bar)

        elif state.state == MomentumState.IN_POSITION:
            self.manage_position(symbol, state, bar)

        elif state.state == MomentumState.PULLBACK_WATCH:
            self.handle_pullback_watch(symbol, state, bar)

        elif state.state == MomentumState.STALE:
            self.handle_stale_leader(symbol, state, bar)

    def process_quote(self, symbol, state):
        if state.state in (MomentumState.PENDING_ENTRY, MomentumState.PENDING_EXIT):
            return

        if state.state in (MomentumState.LEADER_WATCH, MomentumState.PULLBACK_WATCH):
            self.try_enter_on_quote(symbol, state)
            return

        if state.state == MomentumState.IN_POSITION:
            self.manage_position_quote(symbol, state)

    def handle_order_event(self, symbol, state, order_event):
        if order_event.OrderId == state.pending_entry_order_id:
            self.handle_entry_fill(symbol, state, order_event)
            return

        if order_event.OrderId == state.pending_exit_order_id:
            self.handle_exit_fill(symbol, state, order_event)

    def handle_entry_fill(self, symbol, state, order_event):
        self.complete_entry_fill(
            symbol,
            state,
            float(order_event.FillPrice),
            abs(int(order_event.FillQuantity)),
        )

    def handle_entry_ticket_fill(self, symbol, state, ticket):
        fill_price = float(getattr(ticket, "AverageFillPrice", 0.0))
        quantity = abs(int(getattr(ticket, "QuantityFilled", 0)))

        self.complete_entry_fill(symbol, state, fill_price, quantity)

    def complete_entry_fill(self, symbol, state, fill_price, quantity):
        if state.pending_entry_order_id is None:
            return

        if quantity <= 0 or fill_price <= 0:
            state.reset_pending_entry()
            state.state = MomentumState.LEADER_WATCH
            return

        stop = state.pending_entry_stop_price

        if stop is None or stop >= fill_price:
            state.reset_pending_entry()
            state.state = MomentumState.LEADER_WATCH
            self.debugger.c_log("RJ", symbol, f"bad_fill_stop|px={fill_price:.2f}|sl={stop}")
            return

        state.entry_price = fill_price
        state.initial_stop_price = stop
        state.stop_price = stop
        state.quantity = quantity

        state.highest_since_entry = fill_price
        state.lowest_since_entry = fill_price
        state.highest_bid_since_entry = state.last_bid or fill_price
        state.lowest_bid_since_entry = state.last_bid or fill_price

        state.scout_reduced = False
        state.slow_reduced = False
        state.soft_failure_reduced = False
        state.stop_moved_to_breakeven = False

        state.entry_time = self.algorithm.Time
        state.last_entry_time = self.algorithm.Time
        state.reset_pending_entry()
        state.state = MomentumState.IN_POSITION

        return

    def handle_exit_fill(self, symbol, state, order_event):
        self.complete_exit_fill(symbol, state)

    def handle_exit_ticket_fill(self, symbol, state, ticket):
        self.complete_exit_fill(symbol, state)

    def complete_exit_fill(self, symbol, state):
        if state.pending_exit_order_id is None:
            return

        r = state.pending_exit_r
        reason = state.pending_exit_reason

        state.last_exit_time = self.algorithm.Time
        state.last_exit_r = r
        state.last_exit_reason = reason

        if r is not None and r < 0:
            state.failed_trade_count += 1
            state.last_failed_trade_time = self.algorithm.Time
        else:
            state.failed_trade_count = 0
            state.last_failed_trade_time = None

        state.reset_pending_exit()
        state.reset_trade_fields()
        state.reentry_attempts += 1

        if not self.is_reentry_allowed_after_exit(reason, r):
            state.state = MomentumState.COOLDOWN
            return

        if state.reentry_attempts > self.max_reentries:
            state.state = MomentumState.COOLDOWN
            return

        state.state = MomentumState.PULLBACK_WATCH
        self.debugger.log_reentry_watch(symbol, state.last_breakout_high)

    def is_reentry_allowed_after_exit(self, reason, r):
        if reason == "ENTRY_FAIL":
            return False

        if reason == "PROFIT_PULLBACK":
            return True

        return r is not None and r > 0
