from AlgorithmImports import *
from indicator_tools import FiveMinuteIndicatorMixin
from order_tags import OrderTagMixin


class OpeningRangeBreakoutCore(FiveMinuteIndicatorMixin, OrderTagMixin):

    def __init__(self, algorithm, debugger, risk_manager):
        self.algorithm = algorithm
        self.debugger = debugger
        self.risk = risk_manager

        self.min_price = 5.0
        self.min_avg_daily_volume = 1_000_000
        self.min_atr = 0.50
        self.relative_volume_daily_share = 0.02
        self.min_opening_relative_volume = 0.75
        self.min_setup_score = 45.0
        self.min_live_score = 55.0
        self.watchlist_size = 100
        self.max_active_positions = 5
        self.replacement_score_buffer = 10.0
        self.minimum_hold_minutes = 10
        self.entry_buffer_pct = 0.0005
        self.entry_stage_proximity_pct = 0.01
        self.stop_box_pullback_fraction = 0.50
        self.min_risk_pct = 0.0025
        self.max_risk_pct = 0.0075
        self.min_gap_up_pct = 0.005
        self.min_close_location = 0.60
        self.min_body_to_range = 0.20
        self.min_orb_range_atr_fraction = 0.05
        self.max_orb_range_atr_fraction = 0.80
        self.tema_entry_atr_buffer = 0.005
        self.tema_exit_atr_buffer = 0.005
        self.entry_cutoff_minutes = 15 * 60 + 30
        self.exit_minutes_before_close = 5
        self.current_rank_date = None
        self.watchlist_log_date = None
        self.watchlist_symbols = []
        self.active_symbols = set()
        self.active_tickers = set()
        self.pending_entry_tickers = set()
        self.second_resolution_tickers = set()

        self.configure_indicators()

    def on_data_start(self):
        current_date = self.algorithm.Time.date()

        if self.current_rank_date != current_date:
            self.current_rank_date = current_date
            self.watchlist_symbols = []
            self.active_symbols.clear()
            self.active_tickers.clear()
            self.pending_entry_tickers.clear()
            self.second_resolution_tickers.clear()

    def process_symbol(self, symbol, state, bar):
        self.ensure_orb_day(state)
        self.update_last_price(state, bar)
        self.update_position_peak(symbol, state)
        self.update_five_minute_indicators(state, bar)

        if self.is_opening_range_bar():
            self.update_opening_range(state, bar)

        self.manage_position(symbol, state)

    def after_on_data(self, symbol_states):
        for state in symbol_states.values():
            self.ensure_orb_day(state)

        if self.algorithm.Time.second != 0:
            return

        if self.should_rank_now() and not any(state.orb_ranked for state in symbol_states.values()):
            self.watchlist_symbols = self.build_watchlist(symbol_states)

        if self.minutes_since_midnight() <= self.rank_minute():
            return

        self.hard_end_of_day_liquidation(symbol_states)
        self.log_watchlist_at_end_of_day()
        self.cancel_invalid_entry_orders(symbol_states)
        self.rotate_portfolio(symbol_states)

    def ensure_orb_day(self, state):
        current_date = self.algorithm.Time.date()

        if state.orb_date != current_date:
            state.reset_orb_day(current_date)

    def is_opening_range_bar(self):
        minutes = self.minutes_since_midnight()
        return 9 * 60 + 31 <= minutes <= self.rank_minute()

    def rank_minute(self):
        return 9 * 60 + 35

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

    def build_watchlist(self, symbol_states):
        candidates = []
        seen_tickers = set()

        for symbol, state in symbol_states.items():
            state.orb_ranked = True
            ticker = symbol.Value

            if ticker in seen_tickers:
                continue

            if not self.is_valid_setup(state):
                continue

            state.orb_direction = "LONG"
            state.orb_score = self.setup_score(state)

            if state.orb_score < self.min_setup_score:
                self.count_orb_reject("quality")
                continue

            candidates.append((symbol, state))
            seen_tickers.add(ticker)

        candidates.sort(key=lambda item: item[1].orb_score, reverse=True)
        selected = candidates[: self.watchlist_size]

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
        top_score = max(live_candidates[0][0], 1.0)
        open_slots = self.max_active_positions - self.occupied_slot_count()

        if open_slots > 0:
            for live_rank, (score, rank, symbol, state) in enumerate(live_candidates, start=1):
                if self.occupied_slot_count() >= self.max_active_positions:
                    return

                if self.is_symbol_busy(symbol, state):
                    continue

                self.submit_entry_order(
                    symbol,
                    state,
                    rank,
                    live_rank,
                    score,
                    self.score_quality(score, top_score),
                    len(live_candidates),
                    "LIVE_SIGNAL",
                    scanner_top,
                    len(live_candidates),
                )

        while self.occupied_slot_count() >= self.max_active_positions:
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

    def occupied_slot_count(self):
        return len(self.active_tickers) + len(self.pending_entry_tickers)

    def score_quality(self, score, top_score):
        if top_score <= 0:
            return 0.0

        return max(0.0, min(score / top_score, 1.0))

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
        ticker = symbol.Value

        if ticker in self.active_tickers or ticker in self.pending_entry_tickers:
            return True

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

        if state.last_price < self.protective_stop_price(state):
            return False

        if state.last_price > self.entry_trigger(state):
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
            and state.tema20 + self.tema_exit_buffer(state) > state.tema9
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

    def submit_entry_order(
        self,
        symbol,
        state,
        rank,
        live_rank,
        score,
        score_quality,
        live_candidate_count,
        reason,
        scanner_top="",
        scanner_count=0,
    ):
        entry = self.entry_trigger(state)
        stop = self.protective_stop_price(state)
        risk_pct = self.risk_pct_for_score(score_quality)
        quantity = self.calculate_quantity(
            entry,
            stop,
            score_quality,
            risk_pct,
            live_candidate_count,
        )

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
            tag=self.entry_tag(
                reason,
                rank,
                live_rank,
                quantity,
                entry,
                stop,
                score,
                score_quality,
                risk_pct,
                state,
                scanner_top,
                scanner_count,
            ),
        )

        if ticket is None:
            self.debugger.count_reject("order")
            return False

        self.ensure_second_resolution(symbol)
        state.orb_entry_order_id = ticket.OrderId
        state.orb_entry_price = entry
        state.orb_stop_price = stop
        state.orb_quantity = quantity
        state.orb_rank = rank
        state.orb_entry_live_rank = live_rank
        state.orb_live_score = score
        state.orb_entry_score_quality = score_quality
        state.orb_entry_risk_pct = risk_pct
        state.orb_entry_submitted_time = self.algorithm.Time
        state.breakout_armed = False
        self.pending_entry_tickers.add(symbol.Value)
        self.debugger.count("entry_submit", symbol)
        self.debugger.c_log(
            "E",
            symbol,
            (
                f"STOP|trg={entry:.2f}|stp={stop:.2f}|n={quantity}"
                f"|rk={rank}|lr={live_rank}|sc={score:.1f}|rv={state.orb_relative_volume:.1f}"
                f"|t9={state.tema9:.2f}|t20={state.tema20:.2f}"
            ),
        )
        return True

    def risk_pct_for_score(self, score_quality):
        return self.min_risk_pct + (
            (self.max_risk_pct - self.min_risk_pct) * score_quality
        )

    def calculate_quantity(self, entry, stop, score_quality, risk_pct, live_candidate_count):
        risk_per_share = abs(entry - stop)

        if risk_per_share <= 0 or entry <= 0:
            return 0

        total_equity = float(self.algorithm.Portfolio.TotalPortfolioValue)
        cash = float(self.algorithm.Portfolio.Cash)
        deployable_cash = max(0.0, cash - (total_equity * self.risk.cash_reserve_pct))
        open_slots = max(1, self.max_active_positions - self.occupied_slot_count())
        allocation_slots = max(1, min(open_slots, live_candidate_count))
        base_capital_budget = deployable_cash / allocation_slots
        capital_multiplier = 0.75 + (0.50 * score_quality)
        capital_budget = min(deployable_cash, base_capital_budget * capital_multiplier)
        risk_budget = total_equity * risk_pct
        quantity_by_risk = int(risk_budget / risk_per_share)
        quantity_by_cash = int(capital_budget / entry)

        return max(0, min(quantity_by_risk, quantity_by_cash, int(deployable_cash / entry)))

    def has_minimum_trade_economics(self, quantity, entry, stop):
        return True

    def ensure_second_resolution(self, symbol):
        ticker = symbol.Value

        if ticker in self.second_resolution_tickers:
            return

        security = self.algorithm.AddEquity(ticker, Resolution.Second)
        security.SetDataNormalizationMode(DataNormalizationMode.Raw)
        self.second_resolution_tickers.add(ticker)

    def update_position_peak(self, symbol, state):
        if state.last_price is None or state.orb_entry_price is None:
            return

        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return

        if state.max_price_since_entry is None:
            state.max_price_since_entry = state.orb_entry_price

        state.max_price_since_entry = max(state.max_price_since_entry, state.last_price)
        max_profit_per_share = max(0.0, state.max_price_since_entry - state.orb_entry_price)
        state.max_unrealized_profit = max_profit_per_share * abs(quantity)

        risk_per_share = abs(state.orb_entry_price - self.protective_stop_price(state))

        if risk_per_share > 0:
            state.max_r_multiple = max_profit_per_share / risk_per_share

    def manage_position(self, symbol, state):
        if state.orb_exit_submitted:
            return

        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return

        if state.last_price is not None and state.last_price < self.protective_stop_price(state):
            self.exit_position(symbol, state, "BREAKOUT_FAIL")
            return

        if self.is_tema_closed(state):
            self.exit_position(symbol, state, "TEMA_CLOSE")

    def cancel_invalid_entry_orders(self, symbol_states):
        for symbol, state in symbol_states.items():
            if state.orb_entry_order_id is None:
                continue

            if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
                continue

            reason = None

            if self.minutes_since_midnight() >= self.entry_cutoff_minutes:
                reason = "entry_cutoff"
            elif state.last_price is not None and state.last_price < self.protective_stop_price(state):
                reason = "lost_breakout_zone"
            elif state.last_price is not None and state.last_price > self.entry_trigger(state) * (1.0 + self.entry_stage_proximity_pct):
                reason = "missed_breakout"
            elif not self.is_macd_open(state):
                reason = "macd_closed"
            elif not self.is_tema_open(state):
                reason = "tema_closed"

            if reason is None:
                continue

            self.cancel_entry_order(symbol, state, reason)

    def hard_end_of_day_liquidation(self, symbol_states):
        if self.minutes_since_midnight() < 16 * 60 - self.exit_minutes_before_close:
            return

        for symbol, state in symbol_states.items():
            if state.orb_entry_order_id is not None:
                self.cancel_entry_order(symbol, state, "eod")

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
        if order_event.Status in [OrderStatus.Canceled, OrderStatus.Invalid]:
            if order_event.OrderId == state.orb_entry_order_id:
                state.orb_entry_order_id = None
                state.orb_entry_submitted_time = None
                self.pending_entry_tickers.discard(symbol.Value)
                state.breakout_armed = True
                return

            if order_event.OrderId == state.orb_stop_order_id:
                state.orb_stop_order_id = None
                return

        if order_event.Status != OrderStatus.Filled:
            return

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
        self.pending_entry_tickers.discard(symbol.Value)
        self.active_symbols.add(symbol)
        self.active_tickers.add(symbol.Value)
        state.orb_entry_price = float(order_event.FillPrice)
        state.orb_entry_time = self.algorithm.Time
        state.orb_stop_price = self.protective_stop_price(state)
        state.max_price_since_entry = state.orb_entry_price
        state.max_unrealized_profit = 0.0
        state.max_r_multiple = 0.0

        stop_quantity = -fill_quantity
        ticket = self.algorithm.StopMarketOrder(
            symbol,
            stop_quantity,
            state.orb_stop_price,
            tag=(
                f"STOP_LOSS|rule=BREAKOUT_HOLD_INVALIDATION|stop={state.orb_stop_price:.2f}"
                f"|entry={state.orb_entry_price:.2f}|box_high={state.orb_high:.2f}"
                f"|box_mid={self.box_mid(state):.2f}|box_low={state.orb_low:.2f}"
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
        state.orb_entry_submitted_time = None
        state.max_price_since_entry = None
        state.max_unrealized_profit = 0.0
        state.max_r_multiple = 0.0
        self.pending_entry_tickers.discard(symbol.Value)
        self.active_symbols.discard(symbol)
        self.active_tickers.discard(symbol.Value)

    def cancel_entry_order(self, symbol, state, reason):
        self.cancel_order(
            state.orb_entry_order_id,
            f"CANCEL_ENTRY|reason={reason}|trigger={self.entry_trigger(state):.2f}",
        )
        state.orb_entry_order_id = None
        state.orb_entry_submitted_time = None
        self.pending_entry_tickers.discard(symbol.Value)

    def box_range(self, state):
        if state.orb_high is None or state.orb_low is None:
            return 0.0

        return state.orb_high - state.orb_low

    def box_mid(self, state):
        if state.orb_high is None or state.orb_low is None:
            return 0.0

        return (state.orb_high + state.orb_low) / 2.0

    def protective_stop_price(self, state):
        if state.orb_high is None:
            return 0.0

        return state.orb_high - (
            self.stop_box_pullback_fraction * (state.orb_high - self.box_mid(state))
        )

    def range_atr(self, state):
        if state.atr_14 is None or state.atr_14 <= 0:
            return 0.0

        return self.box_range(state) / state.atr_14

    def close_location(self, state):
        opening_range = self.box_range(state)

        if opening_range <= 0 or state.orb_close is None:
            return 0.0

        return (state.orb_close - state.orb_low) / opening_range

    def body_to_range(self, state):
        opening_range = self.box_range(state)

        if opening_range <= 0 or state.orb_close is None or state.orb_open is None:
            return 0.0

        return abs(state.orb_close - state.orb_open) / opening_range

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
