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
        state.state = MomentumState.LEADER_WATCH

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

        self.debugger.log_abnormal_expansion(
            symbol=symbol,
            price=float(bar.Close),
            move=move,
            rel_volume=rv,
            spread=spread,
            volume=float(bar.Volume),
            high=float(bar.High),
        )

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

        recent_5 = sum(list(state.volumes)[-5:])
        baseline = sum(list(state.volumes)[-20:-5]) / 3.0

        if baseline <= 0:
            return 0.0

        return recent_5 / baseline
