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
        self.min_opening_relative_volume = 2.0
        self.max_candidates = 5
        self.atr_stop_fraction = 0.20
        self.risk_per_trade_pct = 0.0025
        self.max_capital_per_trade_pct = 0.10
        self.entry_buffer_pct = 0.0005
        self.min_gap_up_pct = 0.005
        self.min_close_location = 0.75
        self.min_body_to_range = 0.35
        self.min_orb_range_atr_fraction = 0.25
        self.max_orb_range_atr_fraction = 0.50
        self.min_position_value = 500.0
        self.min_planned_risk_dollars = 12.0
        self.max_full_cash_risk_pct = 0.02
        self.entry_order_timeout_minutes = 30
        self.breakeven_after_r = 1.50
        self.trail_after_r = 3.00
        self.trail_mfe_keep_fraction = 0.30
        self.min_stop_update_pct = 0.005
        self.stop_update_close_buffer_pct = 0.002
        self.exit_minutes_before_close = 5
        self.cancel_unfilled_minutes_before_close = 10
        self.current_rank_date = None
        self.pending_candidates = []
        self.active_symbol = None

    def on_data_start(self):
        current_date = self.algorithm.Time.date()

        if self.current_rank_date != current_date:
            self.current_rank_date = current_date

    def process_symbol(self, symbol, state, bar):
        self.ensure_orb_day(state)

        if self.is_opening_range_bar():
            self.update_opening_range(state, bar)
            return

        if self.should_rank_now():
            return

        self.manage_open_orders(symbol, state)
        self.manage_position(symbol, state, bar)
        self.manage_end_of_day(symbol, state)
        self.try_submit_next_candidate()

    def after_on_data(self, symbol_states):
        if not self.should_rank_now():
            return

        for state in symbol_states.values():
            self.ensure_orb_day(state)

        if any(state.orb_ranked for state in symbol_states.values()):
            return

        self.pending_candidates = self.rank_opening_range_candidates(symbol_states)
        self.try_submit_next_candidate()

    def try_submit_next_candidate(self):
        if not self.can_submit_new_entry_now():
            return

        if self.has_active_trade():
            return

        while len(self.pending_candidates) > 0:
            rank, symbol, state = self.pending_candidates.pop(0)

            if self.submit_entry(symbol, state, rank):
                return

    def ensure_orb_day(self, state):
        current_date = self.algorithm.Time.date()

        if state.orb_date != current_date:
            state.reset_orb_day(current_date)

    def is_opening_range_bar(self):
        minutes = self.minutes_since_midnight()
        return 9 * 60 + 31 <= minutes <= 9 * 60 + 35

    def should_rank_now(self):
        return self.minutes_since_midnight() == 9 * 60 + 35

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

    def rank_opening_range_candidates(self, symbol_states):
        candidates = []

        for symbol, state in symbol_states.items():
            state.orb_ranked = True

            if not self.is_valid_candidate(symbol, state):
                continue

            candidates.append((symbol, state))

        candidates.sort(key=lambda item: item[1].orb_relative_volume, reverse=True)
        selected = candidates[: self.max_candidates]

        self.debugger.c_log(
            "S",
            None,
            f"orb|cand={len(candidates)}|sel={len(selected)}",
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

        state.orb_direction = "LONG"
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

        opening_range = state.orb_high - state.orb_low

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

    def submit_entry(self, symbol, state, rank):
        if state.orb_entry_order_id is not None:
            return False

        if state.orb_direction != "LONG":
            return False

        if self.has_active_trade():
            return False

        entry = state.orb_high * (1.0 + self.entry_buffer_pct)
        stop = entry - (state.atr_14 * self.atr_stop_fraction)
        quantity = self.calculate_quantity(entry, stop)

        if quantity == 0:
            self.debugger.count_reject("qty")
            return False

        if not self.has_minimum_trade_economics(quantity, entry, stop):
            self.debugger.count_reject("economics")
            self.count_orb_reject("econ")
            return False

        if self.exceeds_full_cash_risk_cap(quantity, entry, stop):
            self.debugger.count_reject("risk")
            self.count_orb_reject("econ")
            return False

        ticket = self.algorithm.StopMarketOrder(
            symbol,
            quantity,
            entry,
            tag=(
                f"orb_entry|rk={rank}|d={state.orb_direction}|rv={state.orb_relative_volume:.1f}"
                f"|atr={state.atr_14:.2f}|or={state.orb_low:.2f}-{state.orb_high:.2f}"
            ),
        )

        if ticket is None:
            self.debugger.count_reject("order")
            return False

        state.orb_entry_order_id = ticket.OrderId
        state.orb_entry_order_time = self.algorithm.Time
        state.orb_entry_price = entry
        state.orb_initial_stop_price = stop
        state.orb_stop_price = stop
        state.orb_highest_since_entry = None
        state.orb_breakeven_applied = False
        state.orb_quantity = quantity
        self.active_symbol = symbol
        self.debugger.count("entry_submit", symbol)
        self.debugger.c_log(
            "E",
            symbol,
            (
                f"ORB|d={state.orb_direction}|p={entry:.2f}|sl={stop:.2f}"
                f"|n={quantity}|rk={rank}|rv={state.orb_relative_volume:.1f}|atr={state.atr_14:.2f}"
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
        capital_budget = deployable_cash

        return max(
            0,
            min(
                int(capital_budget / entry),
                int(deployable_cash / entry),
            ),
        )

    def has_minimum_trade_economics(self, quantity, entry, stop):
        risk_per_share = abs(entry - stop)
        position_value = quantity * entry
        planned_risk = quantity * risk_per_share

        if position_value < self.min_position_value:
            return False

        return planned_risk >= self.min_planned_risk_dollars

    def exceeds_full_cash_risk_cap(self, quantity, entry, stop):
        risk_per_share = abs(entry - stop)
        planned_risk = quantity * risk_per_share
        total_equity = float(self.algorithm.Portfolio.TotalPortfolioValue)

        if total_equity <= 0:
            return True

        return planned_risk > total_equity * self.max_full_cash_risk_pct

    def manage_open_orders(self, symbol, state):
        if state.orb_entry_order_id is None or state.orb_stop_order_id is not None:
            return

        if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
            return

        if self.is_entry_order_stale(state):
            self.cancel_order(state.orb_entry_order_id, "orb_entry_timeout")
            state.orb_entry_order_id = None
            state.orb_entry_order_time = None
            self.debugger.count_reject("timeout")
            self.clear_active_symbol_if_done(symbol, state)
            return

        if self.minutes_since_midnight() >= 16 * 60 - self.cancel_unfilled_minutes_before_close:
            self.cancel_order(state.orb_entry_order_id, "orb_cancel_eod")
            state.orb_entry_order_id = None
            state.orb_entry_order_time = None
            self.clear_active_symbol_if_done(symbol, state)

    def manage_position(self, symbol, state, bar):
        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity <= 0:
            return

        if state.orb_entry_price is None or state.orb_initial_stop_price is None:
            return

        high = float(bar.High)

        if state.orb_highest_since_entry is None:
            state.orb_highest_since_entry = high
        else:
            state.orb_highest_since_entry = max(state.orb_highest_since_entry, high)

        new_stop = self.protective_stop_price(state)

        if new_stop is None:
            return

        close = float(bar.Close)
        max_stop = close * (1.0 - self.stop_update_close_buffer_pct)
        new_stop = min(new_stop, max_stop)

        if state.orb_stop_price is not None and new_stop <= state.orb_stop_price:
            return

        if state.orb_stop_price is not None:
            min_step = state.orb_stop_price * self.min_stop_update_pct

            if new_stop - state.orb_stop_price < min_step:
                return

        self.replace_stop_order(symbol, state, quantity, new_stop)

    def protective_stop_price(self, state):
        risk = state.orb_entry_price - state.orb_initial_stop_price

        if risk <= 0 or state.orb_highest_since_entry is None:
            return None

        mfe = state.orb_highest_since_entry - state.orb_entry_price
        mfe_r = mfe / risk

        if mfe_r < self.breakeven_after_r:
            return None

        stop = state.orb_entry_price

        if mfe_r >= self.trail_after_r:
            stop = state.orb_entry_price + (mfe * self.trail_mfe_keep_fraction)

        return stop

    def replace_stop_order(self, symbol, state, quantity, stop_price):
        self.cancel_order(state.orb_stop_order_id, "orb_stop_update")

        ticket = self.algorithm.StopMarketOrder(
            symbol,
            -quantity,
            stop_price,
            tag="orb_stop_protect",
        )

        if ticket is None:
            return

        state.orb_stop_order_id = ticket.OrderId
        state.orb_stop_price = stop_price

        if stop_price >= state.orb_entry_price:
            state.orb_breakeven_applied = True

    def is_entry_order_stale(self, state):
        if state.orb_entry_order_time is None:
            return False

        elapsed = self.algorithm.Time - state.orb_entry_order_time

        return elapsed.total_seconds() >= self.entry_order_timeout_minutes * 60

    def manage_end_of_day(self, symbol, state):
        if state.orb_exit_submitted:
            return

        if self.minutes_since_midnight() < 16 * 60 - self.exit_minutes_before_close:
            return

        quantity = int(self.algorithm.Portfolio[symbol].Quantity)

        if quantity == 0:
            return

        self.cancel_order(state.orb_stop_order_id, "orb_eod_exit")
        self.algorithm.MarketOrder(symbol, -quantity, tag="orb_eod_exit")
        state.orb_exit_submitted = True
        self.debugger.count("exit_signal", symbol)
        self.debugger.count("exit_EOD", symbol)

    def handle_order_event(self, symbol, state, order_event):
        if order_event.OrderId == state.orb_entry_order_id:
            self.handle_entry_fill(symbol, state, order_event)
            return

        if order_event.OrderId == state.orb_stop_order_id:
            state.orb_stop_order_id = None
            self.debugger.count("exit_signal", symbol)
            self.debugger.count("exit_STOP_LOSS", symbol)
            self.clear_active_symbol_if_done(symbol, state)
            return

        if (
            state.orb_exit_submitted
            and int(self.algorithm.Portfolio[symbol].Quantity) == 0
        ):
            state.orb_stop_order_id = None
            self.clear_active_symbol_if_done(symbol, state)

    def handle_entry_fill(self, symbol, state, order_event):
        fill_quantity = int(order_event.FillQuantity)

        if fill_quantity == 0:
            return

        state.orb_entry_order_id = None
        state.orb_entry_order_time = None

        if state.orb_stop_price is None:
            return

        stop_quantity = -fill_quantity
        ticket = self.algorithm.StopMarketOrder(
            symbol,
            stop_quantity,
            state.orb_stop_price,
            tag="orb_stop",
        )

        if ticket is not None:
            state.orb_stop_order_id = ticket.OrderId
            state.orb_quantity = fill_quantity
            state.orb_highest_since_entry = float(order_event.FillPrice)

    def has_active_trade(self):
        if self.active_symbol is None:
            return False

        state = self.algorithm.symbol_states.get(self.active_symbol)

        if state is None:
            self.active_symbol = None
            return False

        if state.orb_entry_order_id is not None or state.orb_stop_order_id is not None:
            return True

        return int(self.algorithm.Portfolio[self.active_symbol].Quantity) != 0

    def should_process_next_after_exit(self):
        return self.minutes_since_midnight() < 16 * 60 - self.cancel_unfilled_minutes_before_close

    def can_submit_new_entry_now(self):
        return self.should_process_next_after_exit()

    def clear_active_symbol_if_done(self, symbol, state):
        if self.active_symbol != symbol:
            return

        if state.orb_entry_order_id is not None or state.orb_stop_order_id is not None:
            return

        if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
            return

        self.active_symbol = None

        if self.should_process_next_after_exit():
            self.try_submit_next_candidate()

    def cancel_order(self, order_id, tag):
        if order_id is None:
            return

        ticket = self.algorithm.Transactions.GetOrderTicket(order_id)

        if ticket is not None:
            ticket.Cancel(tag)

    def minutes_since_midnight(self):
        now = self.algorithm.Time
        return now.hour * 60 + now.minute
