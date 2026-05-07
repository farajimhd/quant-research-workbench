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
    PULLBACK_FORMING = 10
    PULLBACK_READY = 11


class SymbolState:

    def __init__(self, symbol: Symbol, max_window: int = 80):
        self.symbol = symbol
        self.state = MomentumState.QUIET

        self.bars = deque(maxlen=max_window)
        self.prices = deque(maxlen=max_window)
        self.volumes = deque(maxlen=max_window)
        self.typical_prices = deque(maxlen=max_window)

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
        self.armed_entry_type = None
        self.armed_level = None
        self.pullback_low = None
        self.pullback_high = None
        self.pullback_start_time = None
        self.pullback_ready_time = None
        self.pullback_avg_volume = None
        self.failed_pullback_low = None
        self.failed_pullback_high = None
        self.failed_pullback_time = None
        self.runner_peak_relative_volume = 0.0
        self.runner_peak_volume = 0.0
        self.runner_impulse_volume = 0.0

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
        self.pending_entry_type = None

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
        self.entry_type = None

        self.vwap_date = None
        self.vwap_volume = 0.0
        self.vwap_value = 0.0
        self.vwap = None

        self.tema9 = None
        self.tema20 = None
        self.tema9_ema1 = None
        self.tema9_ema2 = None
        self.tema9_ema3 = None
        self.tema20_ema1 = None
        self.tema20_ema2 = None
        self.tema20_ema3 = None

        self.macd_fast = None
        self.macd_slow = None
        self.macd = None
        self.macd_signal = None
        self.macd_hist = None
        self.previous_macd_hist = None

        self.intrabar_minute = None
        self.intrabar_volume = 0.0
        self.intrabar_high = None
        self.intrabar_low = None
        self.intrabar_close = None

    def update_intrabar(self, bar):
        minute = bar.EndTime.replace(second=0, microsecond=0)
        high = float(bar.High)
        low = float(bar.Low)
        close = float(bar.Close)
        volume = float(bar.Volume)

        if self.intrabar_minute != minute:
            self.intrabar_minute = minute
            self.intrabar_volume = 0.0
            self.intrabar_high = high
            self.intrabar_low = low

        self.intrabar_volume += volume
        self.intrabar_high = high if self.intrabar_high is None else max(self.intrabar_high, high)
        self.intrabar_low = low if self.intrabar_low is None else min(self.intrabar_low, low)
        self.intrabar_close = close

    def update_bar(self, bar: TradeBar):
        close = float(bar.Close)
        high = float(bar.High)
        low = float(bar.Low)
        volume = float(bar.Volume)
        typical = (high + low + close) / 3.0

        self.bars.append(bar)
        self.prices.append(close)
        self.volumes.append(volume)
        self.typical_prices.append(typical)

        self.update_vwap(bar, typical, volume)
        self.update_indicators(close)

    def update_vwap(self, bar, typical, volume):
        current_date = bar.EndTime.date()

        if self.vwap_date != current_date:
            self.vwap_date = current_date
            self.vwap_volume = 0.0
            self.vwap_value = 0.0

        self.vwap_volume += volume
        self.vwap_value += typical * volume

        if self.vwap_volume > 0:
            self.vwap = self.vwap_value / self.vwap_volume

    def update_indicators(self, close):
        self.tema9_ema1, self.tema9_ema2, self.tema9_ema3, self.tema9 = self.update_tema(
            close,
            9,
            self.tema9_ema1,
            self.tema9_ema2,
            self.tema9_ema3,
        )
        self.tema20_ema1, self.tema20_ema2, self.tema20_ema3, self.tema20 = self.update_tema(
            close,
            20,
            self.tema20_ema1,
            self.tema20_ema2,
            self.tema20_ema3,
        )

        self.macd_fast = self.update_ema(close, 12, self.macd_fast)
        self.macd_slow = self.update_ema(close, 26, self.macd_slow)

        if self.macd_fast is None or self.macd_slow is None:
            return

        self.macd = self.macd_fast - self.macd_slow
        self.macd_signal = self.update_ema(self.macd, 9, self.macd_signal)

        if self.macd_signal is None:
            return

        self.previous_macd_hist = self.macd_hist
        self.macd_hist = self.macd - self.macd_signal

    def update_tema(self, value, period, ema1, ema2, ema3):
        ema1 = self.update_ema(value, period, ema1)
        ema2 = self.update_ema(ema1, period, ema2)
        ema3 = self.update_ema(ema2, period, ema3)

        if ema1 is None or ema2 is None or ema3 is None:
            return ema1, ema2, ema3, None

        return ema1, ema2, ema3, (3.0 * ema1) - (3.0 * ema2) + ema3

    def update_ema(self, value, period, current):
        if value is None:
            return current

        if current is None:
            return value

        alpha = 2.0 / (period + 1.0)
        return (value * alpha) + (current * (1.0 - alpha))

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

    def recent_high(self, bars: int, exclude_current: bool = True) -> float:
        if len(self.bars) == 0:
            return 0.0

        items = list(self.bars)

        if exclude_current:
            items = items[:-1]

        items = items[-bars:]

        if len(items) == 0:
            return 0.0

        return max(float(bar.High) for bar in items)

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
        self.entry_type = None

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
        self.pending_entry_type = None

    def reset_pending_exit(self):
        self.pending_exit_order_id = None
        self.pending_exit_reason = None
        self.pending_exit_r = None
        self.pending_exit_time = None
