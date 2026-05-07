from AlgorithmImports import *


class OpeningRangeBreakoutCore:

    def __init__(self, algorithm, debugger, risk_manager):
        self.algorithm = algorithm
        self.debugger = debugger
        self.risk = risk_manager

        self.min_price = 5.0
        self.min_avg_daily_volume = 1_000_000
        self.min_atr = 0.50
        self.relative_volume_daily_share = 0.02
        self.min_opening_relative_volume = 1.0
        self.max_candidates = 10
        self.min_candidate_score = 55.0
        self.max_active_positions = 3
        self.entry_buffer_pct = 0.0005
        self.min_gap_up_pct = 0.005
        self.min_close_location = 0.75
        self.min_body_to_range = 0.35
        self.min_orb_range_atr_fraction = 0.05
        self.max_orb_range_atr_fraction = 0.50
        self.min_position_value = 500.0
        self.min_planned_risk_dollars = 12.0
        self.entry_cutoff_minutes = 15 * 60 + 30
        self.exit_minutes_before_close = 5
        self.current_rank_date = None
        self.ranked_symbols = []
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
            self.ranked_symbols = []
            self.active_symbols.clear()

    def process_symbol(self, symbol, state, bar):
        self.ensure_orb_day(state)
        self.update_last_price(state, bar)
        self.update_five_minute_macd(state, bar)

        if self.is_opening_range_bar():
            self.update_opening_range(state, bar)

        self.manage_position(symbol, state)
        self.manage_open_entry(symbol, state)
        self.manage_end_of_day(symbol, state)

    def after_on_data(self, symbol_states):
        for state in symbol_states.values():
            self.ensure_orb_day(state)

        if self.should_rank_now() and not any(state.orb_ranked for state in symbol_states.values()):
            self.ranked_symbols = self.rank_opening_range_candidates(symbol_states)

        if self.minutes_since_midnight() <= 9 * 60 + 35:
            return

        self.cancel_late_entry_orders(symbol_states)
        self.hard_end_of_day_liquidation(symbol_states)
        self.try_submit_top_candidates(symbol_states)

    def ensure_orb_day(self, state):
        current_date = self.algorithm.Time.date()

        if state.orb_date != current_date:
            state.reset_orb_day(current_date)

    def is_opening_range_bar(self):
        minutes = self.minutes_since_midnight()
        return 9 * 60 + 31 <= minutes <= 9 * 60 + 35

    def should_rank_now(self):
        return self.minutes_since_midnight() == 9 * 60 + 35

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

    def update_five_minute_macd(self, state, bar):
        minutes = self.minutes_since_midnight()

        if minutes < 9 * 60 + 30 or minutes >= 16 * 60:
            return

        bucket = minutes // 5
        close = float(bar.Close)

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

    def rank_opening_range_candidates(self, symbol_states):
        candidates = []

        for symbol, state in symbol_states.items():
            state.orb_ranked = True

            if not self.is_valid_candidate(symbol, state):
                continue

            state.orb_direction = "LONG"
            state.orb_score = self.score_candidate(state)
            if state.orb_score < self.min_candidate_score:
                self.count_orb_reject("quality")
                continue
            candidates.append((symbol, state))

        candidates.sort(key=lambda item: item[1].orb_score, reverse=True)
        selected = candidates[: self.max_candidates]

        self.debugger.c_log(
            "S",
            None,
            f"orb|cand={len(candidates)}|sel={len(selected)}",
        )

        for rank, (symbol, state) in enumerate(selected[:5], start=1):
            self.debugger.c_log(
                "S",
                None,
                f"top|rk={rank}|s={symbol.Value}|sc={state.orb_score:.1f}|rv={state.orb_relative_volume:.1f}",
            )

        return [
            (rank, symbol, state)
            for rank, (symbol, state) in enumerate(selected, start=1)
        ]

    def is_valid_candidate(self, symbol, state):
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

    def score_candidate(self, state):
        opening_range = self.box_range(state)
        close_location = (state.orb_close - state.orb_low) / opening_range
        gap_pct = (state.orb_open / state.previous_close) - 1.0
        range_atr_fraction = opening_range / state.atr_14
        ideal_range_score = max(0.0, 1.0 - abs(range_atr_fraction - 0.25) / 0.25)
        rv_score = min(state.orb_relative_volume, 10.0) / 10.0
        gap_score = min(max(gap_pct, 0.0), 0.10) / 0.10
        liquidity_score = min(state.avg_daily_volume_14 / 10_000_000.0, 1.0)

        return (
            40.0 * rv_score
            + 20.0 * close_location
            + 15.0 * gap_score
            + 15.0 * ideal_range_score
            + 10.0 * liquidity_score
        )

    def try_submit_top_candidates(self, symbol_states):
        if self.minutes_since_midnight() >= self.entry_cutoff_minutes:
            return

        open_slots = self.max_active_positions - len(self.active_symbols)

        if open_slots <= 0:
            return

        live_candidates = []

        for rank, symbol, state in self.ranked_symbols:
            if symbol not in symbol_states:
                continue

            if self.is_symbol_busy(symbol, state):
                continue

            if not self.is_entry_ready(symbol, state):
                continue

            live_score = state.orb_score + self.macd_score(state)
            live_candidates.append((live_score, rank, symbol, state))

        live_candidates.sort(key=lambda item: item[0], reverse=True)

        for _, rank, symbol, state in live_candidates[:open_slots]:
            self.submit_entry(symbol, state, rank)

            if len(self.active_symbols) >= self.max_active_positions:
                return

    def is_symbol_busy(self, symbol, state):
        if symbol in self.active_symbols:
            return True

        if state.orb_entry_order_id is not None or state.orb_stop_order_id is not None:
            return True

        return int(self.algorithm.Portfolio[symbol].Quantity) != 0

    def is_entry_ready(self, symbol, state):
        if state.orb_direction != "LONG":
            return False

        if state.last_price is None:
            return False

        if not state.breakout_armed:
            return False

        if state.last_price < self.box_mid(state):
            return False

        trigger = self.entry_trigger(state)

        if state.last_price > trigger:
            return False

        return self.is_macd_open(state) and self.is_tema_open(state)

    def is_macd_open(self, state):
        return (
            state.macd_ready
            and state.macd_line is not None
            and state.macd_signal is not None
            and state.macd_line > state.macd_signal
            and state.macd_hist is not None
            and state.macd_hist > 0
        )

    def is_tema_open(self, state):
        return (
            state.tema_ready
            and state.tema9 is not None
            and state.tema20 is not None
            and state.tema9 > state.tema20
        )

    def is_tema_closed(self, state):
        return (
            state.tema_ready
            and state.tema9 is not None
            and state.tema20 is not None
            and state.tema20 > state.tema9
        )

    def macd_score(self, state):
        if not self.is_macd_open(state) or state.last_price is None or state.last_price <= 0:
            return 0.0

        return min(abs(state.macd_hist) / state.last_price * 1000.0, 20.0)

    def submit_entry(self, symbol, state, rank):
        entry = self.entry_trigger(state)
        stop = self.box_mid(state)
        quantity = self.calculate_quantity(entry, stop)

        if quantity == 0:
            self.debugger.count_reject("qty")
            return False

        if not self.has_minimum_trade_economics(quantity, entry, stop):
            self.debugger.count_reject("economics")
            self.count_orb_reject("econ")
            return False

        ticket = self.algorithm.StopMarketOrder(
            symbol,
            quantity,
            entry,
            tag=(
                f"ENTRY_STOP|rule=BOX_BREAK_MACD_TEMA|rank={rank}"
                f"|qty={quantity}|trigger={entry:.2f}|box_high={state.orb_high:.2f}"
                f"|box_mid={stop:.2f}|box_low={state.orb_low:.2f}"
                f"|score={state.orb_score:.1f}|rv={state.orb_relative_volume:.1f}"
                f"|macd={state.macd_line:.4f}|sig={state.macd_signal:.4f}|hist={state.macd_hist:.4f}"
                f"|tema9={state.tema9:.2f}|tema20={state.tema20:.2f}"
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
        state.breakout_armed = False
        self.active_symbols.add(symbol)
        self.debugger.count("entry_submit", symbol)
        self.debugger.c_log(
            "E",
            symbol,
            (
                f"MACD_ORB|p={entry:.2f}|mid={stop:.2f}|n={quantity}"
                f"|rk={rank}|rv={state.orb_relative_volume:.1f}|sc={state.orb_score:.1f}"
                f"|t9={state.tema9:.2f}|t20={state.tema20:.2f}"
            ),
        )
        return True

    def manage_open_entry(self, symbol, state):
        if state.orb_entry_order_id is None:
            return

        if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
            return

        if self.minutes_since_midnight() >= self.entry_cutoff_minutes:
            self.cancel_entry_order(symbol, state, "orb_entry_cutoff")
            return

        if state.last_price is not None and state.last_price < self.box_mid(state):
            self.cancel_entry_order(symbol, state, "orb_entry_box_mid")
            return

        if not self.is_macd_open(state) or not self.is_tema_open(state):
            self.cancel_entry_order(symbol, state, "orb_entry_signal_closed")

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

    def manage_end_of_day(self, symbol, state):
        if state.orb_exit_submitted:
            return

        if self.minutes_since_midnight() < 16 * 60 - self.exit_minutes_before_close:
            return

        if state.orb_entry_order_id is not None:
            self.cancel_entry_order(symbol, state, "orb_eod_entry_cancel")

        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return

        self.exit_position(symbol, state, "EOD")

    def hard_end_of_day_liquidation(self, symbol_states):
        if self.minutes_since_midnight() < 16 * 60 - self.exit_minutes_before_close:
            return

        for symbol, state in symbol_states.items():
            if state.orb_entry_order_id is not None:
                self.cancel_entry_order(symbol, state, "orb_eod_entry_cancel")

            if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
                self.exit_position(symbol, state, "EOD")

    def cancel_late_entry_orders(self, symbol_states):
        if self.minutes_since_midnight() < self.entry_cutoff_minutes:
            return

        for symbol, state in symbol_states.items():
            if state.orb_entry_order_id is not None:
                self.cancel_entry_order(symbol, state, "orb_entry_cutoff")

    def exit_position(self, symbol, state, reason):
        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return

        self.cancel_order(state.orb_stop_order_id, f"CANCEL_STOP|reason={reason}")
        state.orb_stop_order_id = None
        self.algorithm.MarketOrder(
            symbol,
            -quantity,
            tag=self.exit_tag(reason, state),
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
        self.active_symbols.discard(symbol)

    def cancel_entry_order(self, symbol, state, tag):
        self.cancel_order(
            state.orb_entry_order_id,
            f"CANCEL_ENTRY|reason={tag}|trigger={self.entry_trigger(state):.2f}",
        )
        state.orb_entry_order_id = None
        self.active_symbols.discard(symbol)

    def exit_tag(self, reason, state):
        return (
            f"EXIT|reason={reason}|price={self.value_tag(state.last_price)}"
            f"|box_mid={self.box_mid(state):.2f}|box_high={state.orb_high:.2f}"
            f"|macd={self.value_tag(state.macd_line)}|sig={self.value_tag(state.macd_signal)}"
            f"|hist={self.value_tag(state.macd_hist)}"
            f"|tema9={self.value_tag(state.tema9)}|tema20={self.value_tag(state.tema20)}"
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
