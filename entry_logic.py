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
        state.pending_entry_quality_score = quality["score"]
        state.pending_entry_quality_bucket = quality["bucket"]
        state.pending_entry_risk_pct = quality["risk_pct"]
        state.pending_entry_add_fraction = quality["add_fraction"]
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
        }

    def should_trade_quality(self, symbol, state, quality):
        entry_fails = quality["entry_fails"]

        if entry_fails >= self.max_entry_fails_per_symbol_day:
            return False

        if quality["bucket"] == "C" and entry_fails == 0:
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
