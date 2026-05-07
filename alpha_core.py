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
        self.reentry_extra_margin_pct = 0.004
        self.max_failed_reentry_r = -0.50
        self.max_failed_trades_before_symbol_cooldown = 2
        self.failed_symbol_cooldown_minutes = 60

        # =============================================================================
        # Stop Configuration
        # =============================================================================
        self.stop_lookback_bars = 5
        self.stop_buffer_pct = 0.0025
        self.min_stop_pct = 0.004
        self.hard_max_stop_pct = 0.10

        # =============================================================================
        # Clean No-Progress Exit
        # =============================================================================
        self.enable_progress_exit = True
        self.progress_check_minutes = 8
        self.min_progress_r = 0.10
        self.min_progress_pct = 0.0

        # =============================================================================
        # Profit Protection
        # =============================================================================
        self.move_stop_to_be_at_r = 0.75
        self.protect_after_mfe_r = 0.65
        self.protect_after_mfe_pct = 0.02
        self.protected_giveback_pct = 0.30
        self.min_locked_profit_r = 0.35

        # =============================================================================
        # Simplification For Testing Core Edge
        # =============================================================================
        self.enable_pyramiding = False
        self.enable_partial_exits = False

        # =============================================================================
        # Re-entry Configuration
        # =============================================================================
        self.max_reentries = 2
        self.cooldown_minutes = 20

        # =============================================================================
        # Execution Controls
        # =============================================================================
        self.extended_hours_limit_buffer_pct = 0.002

    # =============================================================================
    # Main Per-Symbol Processor
    # =============================================================================

    def process_symbol(self, symbol, state, bar):
        if self.is_in_cooldown(state):
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
