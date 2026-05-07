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

        self.try_enter_at_price(symbol, state, bar, entry, breakout_high, "BAR_HIGH_BREAK")

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
        self.try_enter_at_price(symbol, state, bar, entry, breakout_high, "QUOTE_HIGH_BREAK")
        return True

    def try_enter_on_indicator_quote(self, symbol, state):
        if state.last_ask is None or state.last_ask <= 0:
            return False

        if len(state.bars) < 6:
            return False

        bar = list(state.bars)[-1]
        entry = float(state.last_ask)
        signal_type, trigger_level = self.indicator_quote_signal(state, entry)

        if signal_type is None:
            return False

        if not self.indicator_context_confirms(state):
            self.debugger.count_reject("indicator")
            return False

        if state.state == MomentumState.QUIET:
            self.mark_leader_from_indicator_quote(symbol, state, bar, entry, signal_type)

        self.debugger.log_breakout_ready(symbol, entry, trigger_level)
        self.try_enter_at_price(symbol, state, bar, entry, trigger_level, signal_type)
        return True

    def try_enter_on_pullback_quote(self, symbol, state):
        if state.last_ask is None or state.last_ask <= 0:
            return False

        if len(state.bars) < 8:
            return False

        entry = float(state.last_ask)
        reclaim_level = self.pullback_reclaim_level(state)

        if reclaim_level is None or reclaim_level <= 0:
            return False

        if entry <= reclaim_level * (1.0 + self.pullback_reclaim_buffer_pct):
            return False

        if not self.is_pullback_completion_confirmed(state):
            self.debugger.count_reject("pullback")
            return False

        bar = list(state.bars)[-1]
        self.debugger.log_breakout_ready(symbol, entry, reclaim_level)
        self.try_enter_at_price(
            symbol,
            state,
            bar,
            entry,
            state.leader_high or reclaim_level,
            "PULLBACK_RECLAIM",
        )
        return True

    def pullback_reclaim_level(self, state):
        levels = []

        if state.vwap is not None and state.vwap > 0:
            levels.append(state.vwap)

        if state.tema9 is not None and state.tema9 > 0:
            levels.append(state.tema9)

        if len(levels) == 0:
            return None

        return max(levels)

    def is_pullback_completion_confirmed(self, state):
        if state.pullback_low is None:
            return False

        if self.is_same_failed_pullback(state):
            return False

        if self.estimate_relative_volume(state) <= self.min_pullback_reclaim_relative_volume:
            return False

        if not self.is_macd_turning_up(state):
            return False

        if not self.is_tema_reclaiming(state):
            return False

        return self.has_reclaim_volume_expansion(state)

    def is_macd_turning_up(self, state):
        if state.macd_hist is None or state.previous_macd_hist is None:
            return False

        return state.macd_hist > state.previous_macd_hist

    def is_tema_reclaiming(self, state):
        if state.tema9 is None or state.tema20 is None:
            return False

        reclaiming_tema9 = state.intrabar_close is not None and state.intrabar_close >= state.tema9

        return state.tema9 >= state.tema20 or reclaiming_tema9

    def has_reclaim_volume_expansion(self, state):
        if len(state.volumes) < 6:
            return False

        current = self.current_reclaim_volume(state)

        if current <= 0:
            return False

        pullback_volume = state.pullback_avg_volume

        if pullback_volume is None or pullback_volume <= 0:
            pullback_volume = sum(list(state.volumes)[-4:-1]) / 3.0

        if current < pullback_volume * self.reclaim_vs_pullback_volume_multiplier:
            return False

        runner_reference = max(
            float(state.runner_peak_volume or 0.0),
            float(state.runner_impulse_volume or 0.0),
        )

        if runner_reference > 0:
            return current >= runner_reference * self.reclaim_min_runner_volume_fraction

        baseline = sum(list(state.volumes)[-6:-1]) / 5.0

        return baseline > 0 and current >= baseline * self.breakout_volume_multiplier

    def current_reclaim_volume(self, state):
        if len(state.volumes) == 0:
            return float(state.intrabar_volume or 0.0)

        return max(float(state.volumes[-1]), float(state.intrabar_volume or 0.0))

    def is_same_failed_pullback(self, state):
        if state.failed_pullback_low is None or state.pullback_low is None:
            return False

        if state.pullback_low <= 0:
            return False

        low_distance = abs(state.pullback_low - state.failed_pullback_low) / state.pullback_low

        if low_distance > self.same_failed_pullback_low_tolerance_pct:
            return False

        if state.failed_pullback_high is None or state.leader_high is None:
            return True

        return state.leader_high < state.failed_pullback_high * (1.0 + self.material_new_high_pct)

    def indicator_quote_signal(self, state, entry):
        vwap_signal = self.is_vwap_reclaim_quote(state, entry)
        high_signal = self.is_micro_high_break_quote(state, entry)

        if vwap_signal and high_signal:
            return "VWAP_HIGH_BREAK", max(state.vwap or 0.0, state.recent_high(self.micro_high_lookback_bars))

        if high_signal:
            return "HIGH_BREAK", state.recent_high(self.micro_high_lookback_bars)

        if vwap_signal:
            return "VWAP_RECLAIM", state.vwap

        return None, None

    def is_vwap_reclaim_quote(self, state, entry):
        if state.vwap is None or state.vwap <= 0:
            return False

        if entry <= state.vwap * (1.0 + self.vwap_reclaim_buffer_pct):
            return False

        if len(state.bars) == 0:
            return False

        last_bar = list(state.bars)[-1]

        return float(last_bar.Low) <= state.vwap * (1.0 + self.vwap_reclaim_buffer_pct)

    def is_micro_high_break_quote(self, state, entry):
        recent_high = state.recent_high(self.micro_high_lookback_bars)

        if recent_high <= 0:
            return False

        return entry > recent_high * (1.0 + self.min_breakout_margin_pct)

    def indicator_context_confirms(self, state):
        if not self.has_indicator_volume_expansion(state):
            return False

        if not self.require_indicator_confirmation:
            return True

        return self.is_macd_opening(state) or self.is_tema_bullish(state)

    def has_indicator_volume_expansion(self, state):
        if len(state.volumes) < 8:
            return False

        current = max(float(state.volumes[-1]), float(state.intrabar_volume or 0.0))
        baseline = sum(list(state.volumes)[-6:-1]) / 5.0

        if baseline <= 0:
            return False

        if current < baseline * self.min_indicator_volume_multiplier:
            return False

        return self.estimate_relative_volume(state) >= self.early_required_relative_volume

    def is_macd_opening(self, state):
        if state.macd is None or state.macd_signal is None or state.macd_hist is None:
            return False

        if state.macd <= state.macd_signal:
            return False

        if state.previous_macd_hist is None:
            return state.macd_hist > 0

        return state.macd_hist > 0 and state.macd_hist >= state.previous_macd_hist

    def is_tema_bullish(self, state):
        if state.tema9 is None or state.tema20 is None:
            return False

        return state.tema9 >= state.tema20

    def mark_leader_from_indicator_quote(self, symbol, state, bar, entry, signal_type):
        move = max(self.detect_abnormal_expansion(state) or 0.0, 0.0)
        rv = self.estimate_relative_volume(state)
        spread = MarketTools.spread_pct(self.algorithm, symbol, entry)

        self.mark_leader(symbol, state, bar, move, rv, spread)
        state.armed_entry_type = signal_type
        state.armed_level = entry

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

    def try_enter_at_price(self, symbol, state, bar, entry, breakout_high, entry_type="HIGH_BREAK"):
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

        stop = self.calculate_entry_stop(state, entry, entry_type)

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

        quality = self.score_entry_setup(symbol, state, bar, entry, stop, spread, breakout_high)

        if not self.should_trade_quality(symbol, state, quality):
            self.debugger.count_reject("quality")
            state.state = self.entry_watch_state(state)
            return

        quantity = self.risk.calculate_quantity(
            entry,
            stop,
            risk_per_trade_pct=quality["risk_pct"],
            max_capital_per_trade_pct=quality["capital_pct"],
        )
        quantity = self.apply_initial_position_fraction(quantity, quality)

        if quantity <= 0:
            self.debugger.c_log(
                "RJ",
                symbol,
                f"qty_zero|p={entry:.2f}|sl={stop:.2f}|risk={risk:.2f}|cash={self.algorithm.Portfolio.Cash:.0f}",
            )
            self.debugger.count_reject("qty")
            state.state = self.entry_watch_state(state)
            return

        position_value = quantity * entry
        planned_risk_dollars = quantity * risk

        if (
            position_value < self.min_position_value
            or planned_risk_dollars < self.min_planned_risk_dollars
        ):
            self.debugger.c_log(
                "RJ",
                symbol,
                f"econ|n={quantity}|val={position_value:.0f}|pr={planned_risk_dollars:.2f}|q={quality['bucket']}{quality['score']}",
            )
            self.debugger.count_reject("economics")
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
            quality_score=quality["score"],
            quality_bucket=quality["bucket"],
            risk_pct=quality["risk_pct"],
            relative_volume=quality["relative_volume"],
            spread_to_risk=quality["spread_to_risk"],
            stop_pct=quality["stop_pct"],
            extension_pct=quality["extension_pct"],
            reentry_attempts=state.reentry_attempts,
            entry_type=entry_type,
        )

        ticket = self.submit_entry_order(symbol, quantity, quality, state.reentry_attempts, entry_type)

        if ticket is None:
            self.debugger.count_reject("order")
            state.state = self.entry_watch_state(state)
            return

        state.pending_entry_order_id = ticket.OrderId
        state.pending_entry_signal_price = entry
        state.pending_entry_stop_price = stop
        state.pending_entry_quantity = quantity
        state.pending_entry_breakout_high = breakout_high
        state.pending_entry_time = self.algorithm.Time
        state.pending_entry_quality_score = quality["score"]
        state.pending_entry_quality_bucket = quality["bucket"]
        state.pending_entry_risk_pct = quality["risk_pct"]
        state.pending_entry_add_fraction = quality["add_fraction"]
        state.pending_entry_type = entry_type
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

        if state.state in (MomentumState.PULLBACK_FORMING, MomentumState.PULLBACK_READY):
            return state.state

        return MomentumState.LEADER_WATCH

    def score_entry_setup(self, symbol, state, bar, entry, stop, spread, breakout_high):
        risk_pct = (entry - stop) / entry if entry > 0 else 0.0
        extension_pct = 0.0

        if breakout_high > 0:
            extension_pct = max(0.0, (entry - breakout_high) / breakout_high)

        rv = self.estimate_relative_volume(state)
        spread_to_risk = 0.0
        risk = entry - stop

        if risk > 0:
            spread_to_risk = (spread * entry) / risk

        score = 50

        if rv >= 12:
            score += 18
        elif rv >= 7:
            score += 12
        elif rv >= 4:
            score += 6
        elif rv < self.required_relative_volume:
            score -= 10

        if spread_to_risk <= 0.12:
            score += 12
        elif spread_to_risk <= 0.22:
            score += 6
        elif spread_to_risk > 0.32:
            score -= 10

        if risk_pct <= 0.035:
            score += 10
        elif risk_pct <= 0.055:
            score += 4
        elif risk_pct > 0.075:
            score -= 12

        if extension_pct <= 0.006:
            score += 8
        elif extension_pct <= 0.018:
            score += 3
        elif extension_pct > 0.030:
            score -= 10

        if MarketTools.is_tight_consolidation(
            state,
            self.consolidation_lookback_bars,
            self.max_consolidation_range_pct,
        ):
            score += 6

        if state.reentry_attempts > 0:
            score -= min(18, state.reentry_attempts * 7)

        entry_fails = self.entry_fail_count_today(state)

        if entry_fails > 0:
            score -= min(25, entry_fails * 10)

        if not MarketTools.is_regular_market_open(self.algorithm, symbol):
            score -= 6

        score = max(0, min(100, score))
        bucket = self.quality_bucket(score)
        risk_trade_pct = self.quality_risk_pct(bucket)

        if entry_fails > 0:
            risk_trade_pct = min(risk_trade_pct, self.same_day_entry_fail_risk_cap)

        return {
            "score": score,
            "bucket": bucket,
            "risk_pct": risk_trade_pct,
            "capital_pct": self.quality_capital_pct(bucket),
            "starter_fraction": self.quality_starter_fraction(bucket, entry_fails),
            "add_fraction": self.quality_add_fraction(bucket, entry_fails),
            "entry_fails": entry_fails,
            "spread_to_risk": spread_to_risk,
            "stop_pct": risk_pct,
            "relative_volume": rv,
            "extension_pct": extension_pct,
        }

    def should_trade_quality(self, symbol, state, quality):
        entry_fails = quality["entry_fails"]
        daily_entry_fails = (
            self.debugger.counters["exit_ENTRY_FAIL"]
            + self.debugger.counters["exit_EARLY_FAIL"]
        )

        if daily_entry_fails >= self.daily_entry_fail_stop_count:
            return False

        if (
            daily_entry_fails >= self.daily_entry_fail_guard_count
            and quality["bucket"] not in ("AP", "A")
        ):
            return False

        if entry_fails >= self.max_entry_fails_per_symbol_day:
            return False

        if quality["bucket"] == "C" and entry_fails == 0:
            return False

        if (
            quality["bucket"] == "B"
            and entry_fails == 0
            and quality["score"] < self.min_fresh_b_quality_score
        ):
            return False

        if self.is_opening_quality_guard_active(symbol):
            if quality["score"] < self.open_quality_min_score:
                return False

            if quality["spread_to_risk"] > self.open_quality_max_spread_to_risk:
                return False

            if quality["stop_pct"] > self.open_quality_max_stop_pct:
                return False

        return True

    def is_opening_quality_guard_active(self, symbol):
        if not MarketTools.is_regular_market_open(self.algorithm, symbol):
            return False

        now = self.algorithm.Time
        minutes = now.hour * 60 + now.minute
        regular_open_minutes = 9 * 60 + 30

        return regular_open_minutes <= minutes < regular_open_minutes + self.open_quality_guard_minutes

    def entry_fail_count_today(self, state):
        if state.entry_fail_date != self.algorithm.Time.date():
            return 0

        return state.entry_fail_count_today

    def quality_bucket(self, score):
        if score >= 85:
            return "AP"

        if score >= 70:
            return "A"

        if score >= 55:
            return "B"

        return "C"

    def quality_risk_pct(self, bucket):
        if bucket == "AP":
            return self.risk_pct_a_plus

        if bucket == "A":
            return self.risk_pct_a

        if bucket == "B":
            return self.risk_pct_b

        return self.risk_pct_c

    def quality_capital_pct(self, bucket):
        if bucket == "AP":
            return self.capital_pct_a_plus

        if bucket == "A":
            return self.capital_pct_a

        if bucket == "B":
            return self.capital_pct_b

        return self.capital_pct_c

    def quality_starter_fraction(self, bucket, entry_fails):
        if entry_fails > 0:
            return 0.40

        if bucket == "AP":
            return 1.00

        if bucket == "A":
            return 0.90

        if bucket == "B":
            return 0.75

        return 0.50

    def quality_add_fraction(self, bucket, entry_fails):
        return 0.0

    def apply_initial_position_fraction(self, quantity, quality=None):
        if quality is not None:
            fraction = quality["starter_fraction"]
        else:
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

    def calculate_entry_stop(self, state, entry, entry_type):
        if entry_type == "PULLBACK_RECLAIM" and state.pullback_low is not None:
            stop = state.pullback_low * (1.0 - self.stop_buffer_pct)
            stop_distance_pct = (entry - stop) / entry

            if stop_distance_pct < self.min_stop_pct:
                stop = entry * (1.0 - self.min_stop_pct)
                stop_distance_pct = self.min_stop_pct

            if stop_distance_pct <= self.hard_max_stop_pct:
                return stop

        return self.calculate_dynamic_chart_stop(state, entry)

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

    def submit_entry_order(
        self,
        symbol,
        quantity,
        quality=None,
        reentry_attempts=0,
        entry_type="HIGH_BREAK",
    ):
        tag = "entry"

        if quality is not None:
            tag = (
                f"{tag}|t={entry_type}"
                f"|q={quality['bucket']}{quality['score']}"
                f"|rp={quality['risk_pct'] * 100:.2f}"
                f"|rv={quality['relative_volume']:.1f}"
                f"|sr={quality['spread_to_risk']:.2f}"
                f"|st={quality['stop_pct'] * 100:.1f}"
                f"|ex={quality['extension_pct'] * 100:.1f}"
                f"|re={reentry_attempts}"
            )

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
