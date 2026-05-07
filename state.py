from AlgorithmImports import *
from collections import deque
from enum import Enum


class MomentumState(Enum):
    QUIET = 1
    LEADER_WATCH = 2
    BREAKOUT_READY = 3
    IN_POSITION = 4
    PULLBACK_WATCH = 5
    STALE = 6
    COOLDOWN = 7
    PENDING_ENTRY = 8
    PENDING_EXIT = 9


class SymbolState:

    def __init__(self, symbol: Symbol, max_window: int = 80):
        self.symbol = symbol
        self.state = MomentumState.QUIET

        self.bars = deque(maxlen=max_window)
        self.prices = deque(maxlen=max_window)
        self.volumes = deque(maxlen=max_window)

        self.expansion_time = None
        self.expansion_high = None
        self.expansion_low = None
        self.expansion_base = None

        self.leader_high = None
        self.leader_low = None

        self.last_material_high = None
        self.last_material_high_time = None
        self.bars_since_material_high = 0

        self.last_breakout_high = None
        self.last_pullback_low = None
        self.last_consolidation_high = None
        self.last_consolidation_low = None

        self.entry_price = None
        self.initial_stop_price = None
        self.stop_price = None
        self.quantity = 0

        self.highest_since_entry = None
        self.lowest_since_entry = None

        self.scout_reduced = False
        self.slow_reduced = False
        self.soft_failure_reduced = False
        self.stop_moved_to_breakeven = False

        self.add_count = 0
        self.reentry_attempts = 0

        self.entry_time = None
        self.last_exit_time = None
        self.last_entry_time = None
        self.last_exit_r = None
        self.last_exit_reason = None
        self.failed_trade_count = 0
        self.last_failed_trade_time = None
        self.entry_fail_date = None
        self.entry_fail_count_today = 0

        self.pending_entry_order_id = None
        self.pending_entry_signal_price = None
        self.pending_entry_stop_price = None
        self.pending_entry_quantity = 0
        self.pending_entry_breakout_high = None
        self.pending_entry_time = None
        self.pending_entry_quality_score = None
        self.pending_entry_quality_bucket = None
        self.pending_entry_risk_pct = None
        self.pending_entry_add_fraction = 0.0

        self.pending_exit_order_id = None
        self.pending_exit_reason = None
        self.pending_exit_r = None
        self.pending_exit_time = None

        self.last_bid = None
        self.last_ask = None
        self.last_spread_pct = None
        self.last_quote_time = None
        self.previous_quote_time = None
        self.highest_bid_since_entry = None
        self.lowest_bid_since_entry = None
        self.entry_failure_quote_count = 0
        self.entry_failure_last_time = None
        self.early_failure_quote_count = 0
        self.early_failure_last_time = None
        self.entry_quality_score = None
        self.entry_quality_bucket = None
        self.entry_risk_pct = None
        self.entry_add_fraction = 0.0
        self.entry_breakout_high = None

    def update_bar(self, bar: TradeBar):
        self.bars.append(bar)
        self.prices.append(float(bar.Close))
        self.volumes.append(float(bar.Volume))

    def update_quote(self, bid: float, ask: float, time):
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return

        self.last_bid = float(bid)
        self.last_ask = float(ask)
        self.previous_quote_time = self.last_quote_time
        self.last_quote_time = time

        midpoint = (self.last_bid + self.last_ask) / 2.0

        if midpoint > 0:
            self.last_spread_pct = (self.last_ask - self.last_bid) / midpoint

        if self.entry_price is not None:
            if self.highest_bid_since_entry is None:
                self.highest_bid_since_entry = self.last_bid
            else:
                self.highest_bid_since_entry = max(self.highest_bid_since_entry, self.last_bid)

            if self.lowest_bid_since_entry is None:
                self.lowest_bid_since_entry = self.last_bid
            else:
                self.lowest_bid_since_entry = min(self.lowest_bid_since_entry, self.last_bid)

    def has_window(self, bars: int) -> bool:
        return len(self.prices) >= bars

    def price_move(self, bars: int) -> float:
        if not self.has_window(bars + 1):
            return 0.0

        old_price = self.prices[-bars - 1]
        new_price = self.prices[-1]

        if old_price <= 0:
            return 0.0

        return (new_price - old_price) / old_price

    def recent_volume(self, bars: int = 5) -> float:
        if len(self.volumes) == 0:
            return 0.0

        return float(sum(list(self.volumes)[-bars:]))

    def reset_trade_fields(self):
        self.entry_price = None
        self.initial_stop_price = None
        self.stop_price = None
        self.quantity = 0

        self.highest_since_entry = None
        self.lowest_since_entry = None
        self.highest_bid_since_entry = None
        self.lowest_bid_since_entry = None
        self.entry_failure_quote_count = 0
        self.entry_failure_last_time = None
        self.early_failure_quote_count = 0
        self.early_failure_last_time = None
        self.entry_quality_score = None
        self.entry_quality_bucket = None
        self.entry_risk_pct = None
        self.entry_add_fraction = 0.0
        self.entry_breakout_high = None

        self.scout_reduced = False
        self.slow_reduced = False
        self.soft_failure_reduced = False
        self.stop_moved_to_breakeven = False

        self.add_count = 0
        self.entry_time = None

    def reset_pending_entry(self):
        self.pending_entry_order_id = None
        self.pending_entry_signal_price = None
        self.pending_entry_stop_price = None
        self.pending_entry_quantity = 0
        self.pending_entry_breakout_high = None
        self.pending_entry_time = None
        self.pending_entry_quality_score = None
        self.pending_entry_quality_bucket = None
        self.pending_entry_risk_pct = None
        self.pending_entry_add_fraction = 0.0

    def reset_pending_exit(self):
        self.pending_exit_order_id = None
        self.pending_exit_reason = None
        self.pending_exit_r = None
        self.pending_exit_time = None
