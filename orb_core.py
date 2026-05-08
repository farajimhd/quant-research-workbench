from AlgorithmImports import *


class OpeningRangeBreakoutCore:

    def __init__(self, algorithm, debugger, risk_manager):
        self.algorithm = algorithm
        self.debugger = debugger
        self.risk = risk_manager

        self.min_price = 5.0
        self.min_avg_daily_volume = 1_000_000
        self.min_atr = 0.50
        self.relative_volume_daily_share = 0.06
        self.min_opening_relative_volume = 0.75
        self.min_setup_score = 45.0
        self.min_live_score = 55.0
        self.max_active_positions = 3
        self.replacement_score_buffer = 10.0
        self.minimum_hold_minutes = 10
        self.entry_buffer_pct = 0.0005
        self.min_gap_up_pct = 0.005
        self.min_close_location = 0.60
        self.min_body_to_range = 0.20
        self.min_orb_range_atr_fraction = 0.05
        self.max_orb_range_atr_fraction = 0.80
        self.tema_entry_atr_buffer = 0.005
        self.tema_exit_atr_buffer = 0.02
        self.min_position_value = 500.0
        self.min_planned_risk_dollars = 12.0
        self.entry_cutoff_minutes = 15 * 60 + 30
        self.exit_minutes_before_close = 5
        self.current_rank_date = None
        self.watchlist_log_date = None
        self.watchlist_symbols = []
        self.active_symbols = set()

        self.macd_fast_period = 12
        self.macd_slow_period = 26
        self.macd_signal_period = 9
        self.macd_fast_alpha = 2.0 / (self.macd_fast_period + 1.0)
        self.macd_slow_alpha = 2.0 / (self.macd_slow_period + 1.0)
        self.macd_signal_alpha = 2.0 / (self.macd_signal_period + 1.0)
        self.tema9_period = 9
        self.tema20_period = 20
        self.tema9_alpha = 2.0 / (self.tema9_period + 1.0)
        self.tema20_alpha = 2.0 / (self.tema20_period + 1.0)

    def on_data_start(self):
        current_date = self.algorithm.Time.date()

        if self.current_rank_date != current_date:
            self.current_rank_date = current_date
            self.watchlist_symbols = []
            self.active_symbols.clear()

    def process_symbol(self, symbol, state, bar):
        self.ensure_orb_day(state)
        self.update_last_price(state, bar)
        self.update_five_minute_indicators(state, bar)

        if self.is_opening_range_bar():
            self.update_opening_range(state, bar)

        self.manage_position(symbol, state)

    def after_on_data(self, symbol_states):
        for state in symbol_states.values():
            self.ensure_orb_day(state)

        if self.should_rank_now() and not any(state.orb_ranked for state in symbol_states.values()):
            self.watchlist_symbols = self.build_watchlist(symbol_states)

        if self.minutes_since_midnight() <= self.rank_minute():
            return

        self.hard_end_of_day_liquidation(symbol_states)
        self.log_watchlist_at_end_of_day()
        self.rotate_portfolio(symbol_states)

    def ensure_orb_day(self, state):
        current_date = self.algorithm.Time.date()

        if state.orb_date != current_date:
            state.reset_orb_day(current_date)

    def is_opening_range_bar(self):
        minutes = self.minutes_since_midnight()
        return 9 * 60 + 31 <= minutes <= self.rank_minute()

    def rank_minute(self):
        return 9 * 60 + 45

    def should_rank_now(self):
        return self.minutes_since_midnight() == self.rank_minute()

    def update_last_price(self, state, bar):
        state.previous_price = state.last_price
        state.last_price = float(bar.Close)
        state.last_high = float(bar.High)
        state.last_low = float(bar.Low)

        if state.orb_high is not None and state.last_price <= self.entry_trigger(state):
            state.breakout_armed = True

    def update_opening_range(self, state, bar):
        open_price = float(bar.Open)
        high = float(bar.High)
        low = float(bar.Low)
        close = float(bar.Close)
        volume = float(bar.Volume)

        if state.orb_open is None:
            state.orb_open = open_price
            state.orb_high = high
            state.orb_low = low
        else:
            state.orb_high = max(state.orb_high, high)
            state.orb_low = min(state.orb_low, low)

        state.orb_close = close
        state.orb_volume += volume

        if state.avg_daily_volume_14 is not None and state.avg_daily_volume_14 > 0:
            expected_opening_volume = (
                state.avg_daily_volume_14 * self.relative_volume_daily_share
            )
            state.orb_relative_volume = state.orb_volume / expected_opening_volume

    def update_five_minute_indicators(self, state, bar):
        minutes = self.minutes_since_midnight()

        if minutes < 9 * 60 + 30 or minutes >= 16 * 60:
            return

        self.update_five_minute_indicators_at(state, float(bar.Close), minutes)

    def update_five_minute_indicators_at(self, state, close, minutes):
        bucket = minutes // 5

        if state.macd_bucket is None:
            state.macd_bucket = bucket
            state.macd_bucket_close = close
            return

        if bucket == state.macd_bucket:
            state.macd_bucket_close = close
            return

        self.update_macd_from_close(state, state.macd_bucket_close)
        self.update_tema_from_close(state, state.macd_bucket_close)
        state.macd_bucket = bucket
        state.macd_bucket_close = close

    def warm_up_indicators(self, symbol, state):
        if state.macd_ready and state.tema_ready:
            return

        history = self.algorithm.History(symbol, 220, Resolution.Minute)

        if history is None or history.empty:
            return

        history = history.reset_index()
        columns = {str(column).lower(): column for column in history.columns}

        if "close" not in columns:
            return

        time_column = None

        for candidate in ["time", "endtime"]:
            if candidate in columns:
                time_column = columns[candidate]
                break

        if time_column is None:
            return

        for _, row in history.iterrows():
            try:
                bar_time = row[time_column]
                minutes = bar_time.hour * 60 + bar_time.minute
                close = float(row[columns["close"]])
            except Exception:
                continue

            if minutes < 9 * 60 + 30 or minutes >= 16 * 60:
                continue

            self.update_five_minute_indicators_at(state, close, minutes)

    def update_macd_from_close(self, state, close):
        if state.macd_fast_ema is None:
            state.macd_fast_ema = close
            state.macd_slow_ema = close
            state.macd_fast_count = 1
            state.macd_slow_count = 1
            return

        state.macd_fast_ema = (
            self.macd_fast_alpha * close
            + (1.0 - self.macd_fast_alpha) * state.macd_fast_ema
        )
        state.macd_slow_ema = (
            self.macd_slow_alpha * close
            + (1.0 - self.macd_slow_alpha) * state.macd_slow_ema
        )
        state.macd_fast_count += 1
        state.macd_slow_count += 1

        if state.macd_slow_count < self.macd_slow_period:
            return

        macd_line = state.macd_fast_ema - state.macd_slow_ema
        state.prev_macd_line = state.macd_line
        state.prev_macd_signal = state.macd_signal
        state.macd_line = macd_line

        if state.macd_signal is None:
            state.macd_signal = macd_line
            state.macd_signal_count = 1
        else:
            state.macd_signal = (
                self.macd_signal_alpha * macd_line
                + (1.0 - self.macd_signal_alpha) * state.macd_signal
            )
            state.macd_signal_count += 1

        state.macd_hist = state.macd_line - state.macd_signal
        state.macd_ready = state.macd_signal_count >= self.macd_signal_period

    def update_tema_from_close(self, state, close):
        state.tema9_ema1, state.tema9_ema2, state.tema9_ema3, state.tema9 = (
            self.update_tema_values(
                close,
                self.tema9_alpha,
                state.tema9_ema1,
                state.tema9_ema2,
                state.tema9_ema3,
            )
        )
        state.tema20_ema1, state.tema20_ema2, state.tema20_ema3, state.tema20 = (
            self.update_tema_values(
                close,
                self.tema20_alpha,
                state.tema20_ema1,
                state.tema20_ema2,
                state.tema20_ema3,
            )
        )
        state.tema9_count += 1
        state.tema20_count += 1
        state.tema_ready = (
            state.tema9_count >= self.tema9_period
            and state.tema20_count >= self.tema20_period
        )

    def update_tema_values(self, close, alpha, ema1, ema2, ema3):
        if ema1 is None:
            ema1 = close
            ema2 = close
            ema3 = close
        else:
            ema1 = alpha * close + (1.0 - alpha) * ema1
            ema2 = alpha * ema1 + (1.0 - alpha) * ema2
            ema3 = alpha * ema2 + (1.0 - alpha) * ema3

        tema = (3.0 * ema1) - (3.0 * ema2) + ema3
        return ema1, ema2, ema3, tema

    def build_watchlist(self, symbol_states):
        candidates = []

        for symbol, state in symbol_states.items():
            state.orb_ranked = True

            if not self.is_valid_setup(state):
                continue

            state.orb_direction = "LONG"
            state.orb_score = self.setup_score(state)

            if state.orb_score < self.min_setup_score:
                self.count_orb_reject("quality")
                continue

            candidates.append((symbol, state))

        candidates.sort(key=lambda item: item[1].orb_score, reverse=True)

        self.debugger.c_log(
            "S",
            None,
            f"orb|cand={len(candidates)}|sel={len(candidates)}",
        )

        for rank, (symbol, state) in enumerate(candidates[:5], start=1):
            self.debugger.c_log(
                "S",
                None,
                f"top|rk={rank}|s={symbol.Value}|sc={state.orb_score:.1f}|rv={state.orb_relative_volume:.1f}",
            )

        return [
            (rank, symbol, state)
            for rank, (symbol, state) in enumerate(candidates, start=1)
        ]

    def log_watchlist_at_end_of_day(self):
        if self.minutes_since_midnight() < 16 * 60 - self.exit_minutes_before_close:
            return

        current_date = self.algorithm.Time.date()

        if self.watchlist_log_date == current_date:
            return

        self.watchlist_log_date = current_date
        chunk_size = 20
        total = len(self.watchlist_symbols)

        if total == 0:
            self.debugger.c_log("WL", None, "watch|n=0")
            return

        for index in range(0, total, chunk_size):
            chunk = self.watchlist_symbols[index:index + chunk_size]
            items = []

            for rank, symbol, state in chunk:
                items.append(
                    (
                        f"{rank}:{symbol.Value}:sc{state.orb_score:.1f}:rv"
                        f"{state.orb_relative_volume:.1f}:h{state.orb_high:.2f}:m"
                        f"{self.box_mid(state):.2f}"
                    )
                )

            self.debugger.c_log(
                "WL",
                None,
                (
                    f"watch|part={index // chunk_size + 1}|n={total}|"
                    + ";".join(items)
                ),
            )

    def is_valid_setup(self, state):
        if state.orb_open is None or state.orb_high is None or state.orb_low is None:
            self.count_orb_reject("base")
            return False

        if state.orb_close is None or state.orb_close <= 0:
            self.count_orb_reject("base")
            return False

        if state.orb_close < self.min_price:
            self.count_orb_reject("base")
            return False

        if state.avg_daily_volume_14 is None or state.avg_daily_volume_14 < self.min_avg_daily_volume:
            self.count_orb_reject("liq")
            return False

        if state.atr_14 is None or state.atr_14 < self.min_atr:
            self.count_orb_reject("atr")
            return False

        if state.orb_relative_volume < self.min_opening_relative_volume:
            self.count_orb_reject("rv")
            return False

        if state.orb_high <= state.orb_low:
            self.count_orb_reject("base")
            return False

        if not self.has_required_gap(state):
            self.count_orb_reject("gap")
            return False

        if not self.has_quality_opening_range(state):
            return False

        return True

    def count_orb_reject(self, reason):
        self.debugger.count(f"or_{reason}")

    def has_required_gap(self, state):
        if state.previous_close is None or state.previous_close <= 0:
            return False

        return state.orb_open >= state.previous_close * (1.0 + self.min_gap_up_pct)

    def has_quality_opening_range(self, state):
        if state.orb_close <= state.orb_open:
            self.count_orb_reject("shape")
            return False

        opening_range = self.box_range(state)

        if opening_range <= 0:
            self.count_orb_reject("range")
            return False

        range_atr_fraction = opening_range / state.atr_14

        if range_atr_fraction < self.min_orb_range_atr_fraction:
            self.count_orb_reject("range")
            return False

        if range_atr_fraction > self.max_orb_range_atr_fraction:
            self.count_orb_reject("range")
            return False

        close_location = (state.orb_close - state.orb_low) / opening_range

        if close_location < self.min_close_location:
            self.count_orb_reject("shape")
            return False

        body_to_range = abs(state.orb_close - state.orb_open) / opening_range

        if body_to_range < self.min_body_to_range:
            self.count_orb_reject("shape")
            return False

        return True

    def setup_score(self, state):
        opening_range = self.box_range(state)
        close_location = (state.orb_close - state.orb_low) / opening_range
        gap_pct = (state.orb_open / state.previous_close) - 1.0
        range_atr_fraction = opening_range / state.atr_14
        ideal_range_score = max(0.0, 1.0 - abs(range_atr_fraction - 0.30) / 0.30)
        rv_score = min(state.orb_relative_volume, 10.0) / 10.0
        gap_score = min(max(gap_pct, 0.0), 0.10) / 0.10
        liquidity_score = min(state.avg_daily_volume_14 / 10_000_000.0, 1.0)

        return (
            35.0 * rv_score
            + 20.0 * close_location
            + 15.0 * gap_score
            + 15.0 * ideal_range_score
            + 15.0 * liquidity_score
        )

    def rotate_portfolio(self, symbol_states):
        if self.minutes_since_midnight() >= self.entry_cutoff_minutes:
            return

        live_candidates = self.get_live_candidates(symbol_states)

        if len(live_candidates) == 0:
            return

        scanner_top = self.scanner_top_tag(live_candidates)
        open_slots = self.max_active_positions - len(self.active_symbols)

        if open_slots > 0:
            for score, rank, symbol, state in live_candidates:
                if len(self.active_symbols) >= self.max_active_positions:
                    return

                if self.is_symbol_busy(symbol, state):
                    continue

                self.enter_position(
                    symbol,
                    state,
                    rank,
                    score,
                    "LIVE_SIGNAL",
                    scanner_top,
                    len(live_candidates),
                )

        while len(self.active_symbols) >= self.max_active_positions:
            replacement = self.find_replacement(live_candidates)

            if replacement is None:
                return

            new_score, _, new_symbol, _, old_symbol, old_state = replacement
            self.exit_position(old_symbol, old_state, "ROTATE_OUT", new_symbol, new_score)
            return

    def get_live_candidates(self, symbol_states):
        candidates = []

        for rank, symbol, state in self.watchlist_symbols:
            if symbol not in symbol_states:
                continue

            if self.is_symbol_busy(symbol, state):
                continue

            if not self.is_live_signal(state):
                continue

            live_score = self.live_score(state)

            if live_score < self.min_live_score:
                continue

            candidates.append((live_score, rank, symbol, state))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates

    def scanner_top_tag(self, live_candidates):
        parts = []

        for score, rank, symbol, _ in live_candidates[:5]:
            parts.append(f"{rank}:{symbol.Value}:{score:.1f}")

        return ",".join(parts)

    def find_replacement(self, live_candidates):
        weakest = self.find_weakest_replaceable_position()

        if weakest is None:
            return None

        old_score, old_symbol, old_state = weakest

        for new_score, rank, new_symbol, new_state in live_candidates:
            if self.is_symbol_busy(new_symbol, new_state):
                continue

            if new_score <= old_score + self.replacement_score_buffer:
                continue

            return new_score, rank, new_symbol, new_state, old_symbol, old_state

        return None

    def find_weakest_replaceable_position(self):
        weakest = None

        for symbol in list(self.active_symbols):
            state = self.algorithm.symbol_states.get(symbol)

            if state is None:
                self.active_symbols.discard(symbol)
                continue

            if int(self.algorithm.Portfolio[symbol].Quantity) == 0:
                self.active_symbols.discard(symbol)
                continue

            if not self.can_replace_position(state):
                continue

            score = self.live_score(state) if self.is_live_signal(state) else state.orb_score

            if weakest is None or score < weakest[0]:
                weakest = (score, symbol, state)

        return weakest

    def can_replace_position(self, state):
        if state.orb_entry_time is None:
            return False

        held_minutes = (self.algorithm.Time - state.orb_entry_time).total_seconds() / 60.0
        return held_minutes >= self.minimum_hold_minutes

    def is_symbol_busy(self, symbol, state):
        if symbol in self.active_symbols:
            return True

        if state.orb_entry_order_id is not None or state.orb_stop_order_id is not None:
            return True

        return int(self.algorithm.Portfolio[symbol].Quantity) != 0

    def is_live_signal(self, state):
        if state.orb_direction != "LONG":
            return False

        if state.last_price is None:
            return False

        if not state.breakout_armed:
            return False

        if state.last_price < self.box_mid(state):
            return False

        if state.last_price <= self.entry_trigger(state):
            return False

        return self.is_macd_open(state) and self.is_tema_open(state)

    def is_macd_open(self, state):
        return (
            state.macd_ready
            and state.macd_line is not None
            and state.macd_signal is not None
            and state.macd_line > state.macd_signal
            and state.macd_line > 0
            and state.macd_hist is not None
            and state.macd_hist > 0
        )

    def is_tema_open(self, state):
        return (
            state.tema_ready
            and state.tema9 is not None
            and state.tema20 is not None
            and state.atr_14 is not None
            and state.tema9 > state.tema20 + self.tema_entry_buffer(state)
        )

    def is_tema_closed(self, state):
        return (
            state.tema_ready
            and state.tema9 is not None
            and state.tema20 is not None
            and state.atr_14 is not None
            and state.tema20 > state.tema9 + self.tema_exit_buffer(state)
        )

    def tema_exit_buffer(self, state):
        if state.atr_14 is None:
            return 0.0

        return state.atr_14 * self.tema_exit_atr_buffer

    def tema_entry_buffer(self, state):
        if state.atr_14 is None:
            return 0.0

        return state.atr_14 * self.tema_entry_atr_buffer

    def live_score(self, state):
        if state.last_price is None or state.last_price <= 0:
            return state.orb_score

        macd_strength = min(state.macd_hist / state.last_price * 1000.0, 20.0)
        tema_spread = max(0.0, state.tema9 - state.tema20)
        tema_strength = min(tema_spread / state.last_price * 1000.0, 20.0)
        extension = max(0.0, (state.last_price / self.entry_trigger(state)) - 1.0)
        extension_score = min(extension / 0.05, 1.0) * 10.0

        return state.orb_score + macd_strength + tema_strength + extension_score

    def enter_position(self, symbol, state, rank, score, reason, scanner_top="", scanner_count=0):
        entry = state.last_price
        stop = self.box_mid(state)
        quantity = self.calculate_quantity(entry, stop)

        if quantity == 0:
            self.debugger.count_reject("qty")
            return False

        if not self.has_minimum_trade_economics(quantity, entry, stop):
            self.debugger.count_reject("economics")
            self.count_orb_reject("econ")
            return False

        ticket = self.algorithm.MarketOrder(
            symbol,
            quantity,
            tag=self.entry_tag(
                reason,
                rank,
                quantity,
                entry,
                stop,
                score,
                state,
                scanner_top,
                scanner_count,
            ),
        )

        if ticket is None:
            self.debugger.count_reject("order")
            return False

        state.orb_entry_order_id = ticket.OrderId
        state.orb_entry_price = entry
        state.orb_stop_price = stop
        state.orb_quantity = quantity
        state.orb_rank = rank
        state.orb_live_score = score
        state.breakout_armed = False
        self.active_symbols.add(symbol)
        self.debugger.count("entry_submit", symbol)
        self.debugger.c_log(
            "E",
            symbol,
            (
                f"ROT|p={entry:.2f}|mid={stop:.2f}|n={quantity}"
                f"|rk={rank}|sc={score:.1f}|rv={state.orb_relative_volume:.1f}"
                f"|t9={state.tema9:.2f}|t20={state.tema20:.2f}"
            ),
        )
        return True

    def calculate_quantity(self, entry, stop):
        risk_per_share = abs(entry - stop)

        if risk_per_share <= 0 or entry <= 0:
            return 0

        total_equity = float(self.algorithm.Portfolio.TotalPortfolioValue)
        cash = float(self.algorithm.Portfolio.Cash)
        deployable_cash = max(0.0, cash - (total_equity * self.risk.cash_reserve_pct))
        open_slots = max(1, self.max_active_positions - len(self.active_symbols))
        capital_budget = deployable_cash / open_slots

        return max(0, min(int(capital_budget / entry), int(deployable_cash / entry)))

    def has_minimum_trade_economics(self, quantity, entry, stop):
        risk_per_share = abs(entry - stop)
        position_value = quantity * entry
        planned_risk = quantity * risk_per_share

        if position_value < self.min_position_value:
            return False

        return planned_risk >= self.min_planned_risk_dollars

    def manage_position(self, symbol, state):
        if state.orb_exit_submitted:
            return

        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return

        if state.last_price is not None and state.last_price < self.box_mid(state):
            self.exit_position(symbol, state, "BOX_MID")
            return

        if self.is_tema_closed(state):
            self.exit_position(symbol, state, "TEMA_CLOSE")

    def hard_end_of_day_liquidation(self, symbol_states):
        if self.minutes_since_midnight() < 16 * 60 - self.exit_minutes_before_close:
            return

        for symbol, state in symbol_states.items():
            if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
                self.exit_position(symbol, state, "EOD")

    def exit_position(self, symbol, state, reason, replacement_symbol=None, replacement_score=None):
        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return

        self.cancel_order(state.orb_stop_order_id, f"CANCEL_STOP|reason={reason}")
        state.orb_stop_order_id = None
        self.algorithm.MarketOrder(
            symbol,
            -quantity,
            tag=self.exit_tag(reason, state, replacement_symbol, replacement_score),
        )
        state.orb_exit_submitted = True
        self.debugger.count("exit_signal", symbol)
        self.debugger.count(f"exit_{reason}", symbol)

    def handle_order_event(self, symbol, state, order_event):
        if order_event.OrderId == state.orb_entry_order_id:
            self.handle_entry_fill(symbol, state, order_event)
            return

        if order_event.OrderId == state.orb_stop_order_id:
            state.orb_stop_order_id = None
            self.debugger.count("exit_signal", symbol)
            self.debugger.count("exit_STOP_LOSS", symbol)
            self.clear_symbol_if_flat(symbol, state)
            return

        if int(self.algorithm.Portfolio[symbol].Quantity) == 0:
            self.clear_symbol_if_flat(symbol, state)

    def handle_entry_fill(self, symbol, state, order_event):
        fill_quantity = int(order_event.FillQuantity)

        if fill_quantity == 0:
            return

        state.orb_entry_order_id = None
        state.orb_entry_price = float(order_event.FillPrice)
        state.orb_entry_time = self.algorithm.Time
        state.orb_stop_price = self.box_mid(state)

        stop_quantity = -fill_quantity
        ticket = self.algorithm.StopMarketOrder(
            symbol,
            stop_quantity,
            state.orb_stop_price,
            tag=(
                f"STOP_LOSS|rule=BOX_MID_INVALIDATION|stop={state.orb_stop_price:.2f}"
                f"|entry={state.orb_entry_price:.2f}|box_high={state.orb_high:.2f}"
                f"|box_low={state.orb_low:.2f}"
            ),
        )

        if ticket is not None:
            state.orb_stop_order_id = ticket.OrderId

    def clear_symbol_if_flat(self, symbol, state):
        if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
            return

        state.orb_entry_order_id = None
        state.orb_stop_order_id = None
        state.orb_exit_submitted = False
        state.orb_entry_time = None
        self.active_symbols.discard(symbol)

    def entry_tag(
        self,
        reason,
        rank,
        quantity,
        entry,
        stop,
        score,
        state,
        scanner_top,
        scanner_count,
    ):
        return (
            f"ENTRY|reason={reason}|rule=LIVE_BOX_MACD_TEMA|rank={rank}"
            f"|qty={quantity}|price={entry:.2f}|box_high={state.orb_high:.2f}"
            f"|box_mid={stop:.2f}|box_low={state.orb_low:.2f}"
            f"|setup={state.orb_score:.1f}|live={score:.1f}|rv={state.orb_relative_volume:.1f}"
            f"|macd={state.macd_line:.4f}|sig={state.macd_signal:.4f}|hist={state.macd_hist:.4f}"
            f"|tema9={state.tema9:.4f}|tema20={state.tema20:.4f}"
            f"|tbuf={self.tema_entry_buffer(state):.4f}"
            f"|scan={scanner_count}|top5={scanner_top}"
        )

    def exit_tag(self, reason, state, replacement_symbol=None, replacement_score=None):
        rotation = ""

        if replacement_symbol is not None:
            rotation = f"|rotate_to={replacement_symbol.Value}|new_score={replacement_score:.1f}"

        return (
            f"EXIT|reason={reason}|price={self.value_tag(state.last_price)}"
            f"|box_mid={self.box_mid(state):.2f}|box_high={state.orb_high:.2f}"
            f"|live={self.value_tag(state.orb_live_score)}"
            f"|macd={self.value_tag(state.macd_line)}|sig={self.value_tag(state.macd_signal)}"
            f"|hist={self.value_tag(state.macd_hist)}"
            f"|tema9={self.value_tag(state.tema9)}|tema20={self.value_tag(state.tema20)}"
            f"|buf={self.tema_exit_buffer(state):.4f}{rotation}"
        )

    def value_tag(self, value):
        if value is None:
            return "na"

        return f"{value:.4f}"

    def box_range(self, state):
        if state.orb_high is None or state.orb_low is None:
            return 0.0

        return state.orb_high - state.orb_low

    def box_mid(self, state):
        if state.orb_high is None or state.orb_low is None:
            return 0.0

        return (state.orb_high + state.orb_low) / 2.0

    def entry_trigger(self, state):
        return state.orb_high * (1.0 + self.entry_buffer_pct)

    def cancel_order(self, order_id, tag):
        if order_id is None:
            return

        ticket = self.algorithm.Transactions.GetOrderTicket(order_id)

        if ticket is not None:
            ticket.Cancel(tag)

    def minutes_since_midnight(self):
        now = self.algorithm.Time
        return now.hour * 60 + now.minute
