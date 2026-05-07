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
        self.min_expansion_move = 0.04
        self.required_relative_volume = 5.0
        self.early_required_relative_volume = 1.5
        self.vwap_reclaim_buffer_pct = 0.001
        self.micro_high_lookback_bars = 5
        self.min_indicator_volume_multiplier = 1.6
        self.min_pullback_reclaim_relative_volume = 5.0
        self.min_pullback_depth_pct = 0.008
        self.max_pullback_depth_pct = 0.12
        self.pullback_support_buffer_pct = 0.006
        self.pullback_reclaim_buffer_pct = 0.001
        self.pullback_max_bars = 30
        self.reclaim_vs_pullback_volume_multiplier = 1.35
        self.reclaim_min_runner_volume_fraction = 0.35
        self.same_failed_pullback_low_tolerance_pct = 0.003

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
        self.require_indicator_confirmation = True

        # =============================================================================
        # Re-entry Throttle
        # =============================================================================
        self.min_minutes_between_same_symbol_entries = 8
        self.reentry_requires_new_leader_high = True
        self.reentry_extra_margin_pct = 0.010
        self.max_failed_reentry_r = 0.0
        self.max_failed_trades_before_symbol_cooldown = 99
        self.failed_symbol_cooldown_minutes = 60

        # =============================================================================
        # Quality-Weighted Risk
        # =============================================================================
        self.risk_pct_a_plus = 0.0045
        self.risk_pct_a = 0.0040
        self.risk_pct_b = 0.0018
        self.risk_pct_c = 0.0010
        self.same_day_entry_fail_risk_cap = 0.0015
        self.max_entry_fails_per_symbol_day = 2
        self.min_fresh_b_quality_score = 62
        self.daily_entry_fail_guard_count = 5
        self.daily_entry_fail_stop_count = 8
        self.open_quality_guard_minutes = 5
        self.open_quality_min_score = 88
        self.open_quality_max_spread_to_risk = 0.12
        self.open_quality_max_stop_pct = 0.045
        self.capital_pct_a_plus = 0.16
        self.capital_pct_a = 0.14
        self.capital_pct_b = 0.10
        self.capital_pct_c = 0.05
        self.min_position_value = 250.0
        self.min_planned_risk_dollars = 8.0

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
        self.early_failure_seconds = 90
        self.early_failure_break_level_buffer_pct = 0.0005
        self.early_failure_confirmations_required = 2

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
        self.entry_failure_min_mfe_r = 0.20
        self.momentum_exit_min_mfe_r = 0.80
        self.structure_trail_min_mfe_r = 0.50
        self.structure_trail_min_mfe_pct = 0.01

        # =============================================================================
        # Simplification For Testing Core Edge
        # =============================================================================
        self.enable_pyramiding = False
        self.enable_partial_exits = False
        self.initial_position_fraction = 0.80
        self.confirmation_add_fraction = 0.20
        self.add_after_mfe_r = 1.25
        self.add_after_mfe_pct = 0.025
        self.min_minutes_before_add = 1

        # =============================================================================
        # Re-entry Configuration
        # =============================================================================
        self.max_reentries = 2
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

        elif state.state == MomentumState.PULLBACK_FORMING:
            self.handle_pullback_forming(symbol, state, bar)

        elif state.state == MomentumState.PULLBACK_READY:
            self.handle_pullback_ready(symbol, state, bar)

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

        if state.state == MomentumState.PULLBACK_READY:
            self.try_enter_on_pullback_quote(symbol, state)
            return

        if state.state == MomentumState.PULLBACK_WATCH:
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
        state.entry_quality_score = state.pending_entry_quality_score
        state.entry_quality_bucket = state.pending_entry_quality_bucket
        state.entry_risk_pct = state.pending_entry_risk_pct
        state.entry_add_fraction = state.pending_entry_add_fraction
        state.entry_breakout_high = state.pending_entry_breakout_high
        state.entry_type = state.pending_entry_type

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
        entry_type = state.entry_type
        failed_pullback_low = state.pullback_low
        failed_pullback_high = state.leader_high

        state.last_exit_time = self.algorithm.Time
        state.last_exit_r = r
        state.last_exit_reason = reason

        if reason in ("ENTRY_FAIL", "EARLY_FAIL"):
            self.record_entry_fail(state)

        if r is not None and r < 0:
            state.failed_trade_count += 1
            state.last_failed_trade_time = self.algorithm.Time
        else:
            state.failed_trade_count = 0
            state.last_failed_trade_time = None

        state.reset_pending_exit()
        state.reset_trade_fields()
        state.reentry_attempts += 1

        if self.should_block_failed_reclaim(entry_type, reason):
            state.failed_pullback_low = failed_pullback_low
            state.failed_pullback_high = failed_pullback_high
            state.failed_pullback_time = self.algorithm.Time

        state.pullback_low = None
        state.pullback_high = state.last_breakout_high
        state.pullback_start_time = None
        state.pullback_ready_time = None
        state.pullback_avg_volume = None

        if state.reentry_attempts > self.max_reentries:
            state.state = MomentumState.COOLDOWN
            return

        state.state = MomentumState.PULLBACK_FORMING
        self.debugger.log_reentry_watch(symbol, state.last_breakout_high)

    def is_reentry_allowed_after_exit(self, reason, r):
        return True

    def should_block_failed_reclaim(self, entry_type, reason):
        if entry_type != "PULLBACK_RECLAIM":
            return False

        return reason in ("ENTRY_FAIL", "EARLY_FAIL", "NO_PROGRESS", "STRUCT_FAIL")

    def record_entry_fail(self, state):
        today = self.algorithm.Time.date()

        if state.entry_fail_date != today:
            state.entry_fail_date = today
            state.entry_fail_count_today = 0

        state.entry_fail_count_today += 1
