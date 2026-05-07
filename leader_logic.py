from AlgorithmImports import *
from market_tools import MarketTools
from state import MomentumState


class LeaderLogicMixin:

    def handle_quiet(self, symbol, state, bar):
        if not self.has_acceptable_price_volume(state, bar):
            return

        price = float(bar.Close)
        rv = self.estimate_relative_volume(state)
        spread = MarketTools.spread_pct(self.algorithm, symbol, price)

        if spread > self.allowed_spread_pct(price, rv):
            return

        move = self.detect_abnormal_expansion(state)

        if move is None or rv < self.required_relative_volume:
            return

        self.mark_leader(symbol, state, bar, move, rv, spread)

    def detect_abnormal_expansion(self, state):
        best = 0.0

        for window in self.expansion_windows:
            best = max(best, state.price_move(window))

        return best if best >= self.min_expansion_move else None

    def mark_leader(self, symbol, state, bar, move, rv, spread):
        state.state = MomentumState.PULLBACK_FORMING

        state.expansion_time = self.algorithm.Time
        state.expansion_high = float(bar.High)
        state.expansion_low = float(bar.Low)
        state.expansion_base = float(bar.Low)

        state.leader_high = float(bar.High)
        state.leader_low = float(bar.Low)

        state.last_material_high = float(bar.High)
        state.last_material_high_time = self.algorithm.Time
        state.bars_since_material_high = 0

        state.last_breakout_high = float(bar.High)
        state.last_pullback_low = float(bar.Low)
        state.pullback_low = None
        state.pullback_high = float(bar.High)
        state.pullback_start_time = None
        state.pullback_ready_time = None

        self.debugger.log_abnormal_expansion(
            symbol=symbol,
            price=float(bar.Close),
            move=move,
            rel_volume=rv,
            spread=spread,
            volume=float(bar.Volume),
            high=float(bar.High),
        )

    def handle_pullback_forming(self, symbol, state, bar):
        price = float(bar.Close)

        if self.is_leader_dead(state, bar):
            self.debugger.log_dead_leader(symbol, "dead_before_pullback", price)
            self.reset_to_quiet(state)
            return

        self.update_leader_structure(symbol, state, bar)

        if self.is_leader_stale(state):
            state.state = MomentumState.STALE
            return

        self.update_pullback_tracking(state, bar)

        if self.is_valid_pullback_setup(state, bar):
            state.state = MomentumState.PULLBACK_READY
            state.pullback_ready_time = self.algorithm.Time

    def handle_pullback_ready(self, symbol, state, bar):
        price = float(bar.Close)

        if self.is_leader_dead(state, bar):
            self.debugger.log_dead_leader(symbol, "dead_pullback_ready", price)
            self.reset_to_quiet(state)
            return

        self.update_leader_structure(symbol, state, bar)
        self.update_pullback_tracking(state, bar)

        if self.is_pullback_too_deep(state):
            self.debugger.log_dead_leader(symbol, "pullback_deep", price)
            self.reset_to_quiet(state)
            return

        if state.pullback_ready_time is None:
            state.pullback_ready_time = self.algorithm.Time
            return

        elapsed = self.algorithm.Time - state.pullback_ready_time

        if elapsed.total_seconds() > self.pullback_max_bars * 60:
            self.debugger.log_dead_leader(symbol, "pullback_stale", price)
            self.reset_to_quiet(state)

    def update_pullback_tracking(self, state, bar):
        high = float(bar.High)
        low = float(bar.Low)

        if state.leader_high is None:
            return

        if low < state.leader_high:
            if state.pullback_start_time is None:
                state.pullback_start_time = self.algorithm.Time
                state.pullback_high = state.leader_high

            state.pullback_low = low if state.pullback_low is None else min(state.pullback_low, low)
            state.last_pullback_low = state.pullback_low

        if state.pullback_high is None:
            state.pullback_high = high

    def is_valid_pullback_setup(self, state, bar):
        if state.leader_high is None or state.pullback_low is None:
            return False

        if self.is_pullback_too_deep(state):
            return False

        pullback_depth = (state.leader_high - state.pullback_low) / state.leader_high

        if pullback_depth < self.min_pullback_depth_pct:
            return False

        if not self.is_pullback_holding_support(state, bar):
            return False

        return self.has_pullback_volume_contraction(state)

    def is_pullback_too_deep(self, state):
        if state.leader_high is None or state.pullback_low is None:
            return False

        return (state.leader_high - state.pullback_low) / state.leader_high > self.max_pullback_depth_pct

    def is_pullback_holding_support(self, state, bar):
        close = float(bar.Close)
        support = self.pullback_support_level(state)

        if support is None:
            return close >= state.expansion_base

        return close >= support * (1.0 - self.pullback_support_buffer_pct)

    def pullback_support_level(self, state):
        candidates = []

        if state.vwap is not None and state.vwap > 0:
            candidates.append(state.vwap)

        if state.tema20 is not None and state.tema20 > 0:
            candidates.append(state.tema20)

        if state.expansion_base is not None and state.expansion_base > 0:
            candidates.append(state.expansion_base)

        if len(candidates) == 0:
            return None

        return max(candidates)

    def has_pullback_volume_contraction(self, state):
        if len(state.volumes) < 8:
            return False

        recent = list(state.volumes)
        pullback_volume = sum(recent[-3:]) / 3.0
        impulse_volume = sum(recent[-8:-3]) / 5.0

        return impulse_volume > 0 and pullback_volume <= impulse_volume

    def handle_leader_watch(self, symbol, state, bar):
        price = float(bar.Close)

        if self.is_leader_dead(state, bar):
            self.debugger.log_dead_leader(symbol, "dead", price)
            self.reset_to_quiet(state)
            return

        self.update_leader_structure(symbol, state, bar)

        if self.is_leader_stale(state):
            state.state = MomentumState.STALE
            return

        if self.is_explosive_breakout_candidate(state, bar):
            level = MarketTools.highest_high(
                state,
                self.leader_breakout_lookback_bars,
                exclude_current=True,
            )

            self.debugger.log_breakout_ready(symbol, price, level)
            self.try_enter(symbol, state, bar)

    def handle_stale_leader(self, symbol, state, bar):
        price = float(bar.Close)

        if self.is_leader_dead(state, bar):
            self.debugger.log_dead_leader(symbol, "stale_dead", price)
            self.reset_to_quiet(state)
            return

        self.update_leader_structure(symbol, state, bar)

        if self.is_material_new_high(state, float(bar.High)):
            state.state = MomentumState.LEADER_WATCH

    def update_leader_structure(self, symbol, state, bar):
        high = float(bar.High)
        low = float(bar.Low)

        if state.leader_high is None or high > state.leader_high:
            state.leader_high = high
            self.debugger.log_leader_high(symbol, float(bar.Close), high)

        state.leader_low = low if state.leader_low is None else min(state.leader_low, low)

        if self.is_material_new_high(state, high):
            state.last_material_high = high
            state.last_material_high_time = self.algorithm.Time
            state.bars_since_material_high = 0
        else:
            state.bars_since_material_high += 1

        if MarketTools.is_tight_consolidation(
            state,
            self.consolidation_lookback_bars,
            self.max_consolidation_range_pct,
        ):
            state.last_consolidation_high = MarketTools.highest_high(
                state,
                self.consolidation_lookback_bars,
                exclude_current=False,
            )
            state.last_consolidation_low = MarketTools.lowest_low(
                state,
                self.consolidation_lookback_bars,
                exclude_current=False,
            )

        state.last_pullback_low = MarketTools.lowest_low(
            state,
            self.stop_lookback_bars,
            exclude_current=False,
        )

    def is_material_new_high(self, state, high):
        if state.last_material_high is None:
            return True

        return high >= state.last_material_high * (1.0 + self.material_new_high_pct)

    def is_leader_stale(self, state):
        return state.bars_since_material_high >= self.max_bars_without_material_new_high

    def is_leader_dead(self, state, bar):
        price = float(bar.Close)

        if state.expansion_base is not None and price < state.expansion_base:
            return True

        if state.recent_volume(self.leader_dies_volume_bars) < self.leader_dies_min_recent_volume:
            return True

        return False

    def is_explosive_breakout_candidate(self, state, bar):
        return self.breakout_reject_reason(state, bar, float(bar.Close), True) is None

    def is_quote_breakout_setup(self, state, bar, trigger_price):
        return self.breakout_reject_reason(state, bar, trigger_price, False) is None

    def breakout_reject_reason(
        self,
        state,
        bar,
        trigger_price,
        require_bar_break,
        extension_level=None,
    ):
        if not self.has_acceptable_price_volume(state, bar):
            return "setup"

        if not self.has_minimum_leader_watch_time(state):
            return "setup"

        if not MarketTools.is_pullback_holding_structure(state, bar):
            return "setup"

        if not self.has_breakout_volume_expansion(state):
            return "setup"

        if self.is_breakout_too_extended(state, bar, trigger_price, extension_level):
            return "extended"

        if require_bar_break:
            if not MarketTools.is_explosive_high_break(
                state=state,
                bar=bar,
                lookback_bars=self.leader_breakout_lookback_bars,
                min_breakout_margin_pct=self.min_breakout_margin_pct,
                min_body_pct=self.min_breakout_body_pct,
                min_close_location=self.min_close_location,
            ):
                return "no_break"

        return None

    def has_minimum_leader_watch_time(self, state):
        if state.expansion_time is None:
            return True

        elapsed = self.algorithm.Time - state.expansion_time

        return elapsed.total_seconds() >= self.min_leader_watch_minutes * 60

    def is_breakout_too_extended(self, state, bar, trigger_price=None, extension_level=None):
        previous_high = extension_level

        if previous_high is None:
            previous_high = MarketTools.highest_high(
                state,
                self.leader_breakout_lookback_bars,
                exclude_current=True,
            )

        if previous_high <= 0:
            return False

        close = float(bar.Close) if trigger_price is None else float(trigger_price)

        return close > previous_high * (1.0 + self.max_breakout_extension_pct)

    def has_breakout_volume_expansion(self, state):
        if len(state.volumes) < 10:
            return False

        current = float(state.volumes[-1])
        baseline = sum(list(state.volumes)[-6:-1]) / 5.0

        return baseline > 0 and current >= baseline * self.breakout_volume_multiplier

    def has_acceptable_price_volume(self, state, bar):
        price = float(bar.Close)

        if price < self.min_price or price > self.max_price:
            return False

        if float(bar.Volume) < self.min_bar_volume:
            return False

        recent_volume = state.recent_volume(5)

        if recent_volume < self.min_recent_5min_volume:
            return False

        if recent_volume * price < self.min_recent_5min_dollar_volume:
            return False

        return True

    def allowed_spread_pct(self, price, rel_volume=None):
        if price < 2.0:
            base = self.low_price_spread_pct
        elif price < 10.0:
            base = self.mid_price_spread_pct
        else:
            base = self.high_price_spread_pct

        if rel_volume is None:
            return base

        multiplier = max(
            1.0,
            min(self.max_spread_momentum_multiplier, rel_volume / self.required_relative_volume),
        )

        return base * multiplier

    def estimate_relative_volume(self, state):
        if len(state.volumes) < 20:
            return 0.0

        recent = list(state.volumes)
        current_forming_volume = float(state.intrabar_volume or 0.0)
        if current_forming_volume > 0:
            recent_5 = sum(recent[-4:]) + current_forming_volume
        else:
            recent_5 = sum(recent[-5:])
        baseline = sum(recent[-20:-5]) / 3.0

        if baseline <= 0:
            return 0.0

        return recent_5 / baseline
