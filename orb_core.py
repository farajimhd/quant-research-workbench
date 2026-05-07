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
        self.atr_stop_fraction = 0.10
        self.risk_per_trade_pct = 0.0025
        self.max_capital_per_trade_pct = 0.10
        self.entry_buffer_pct = 0.0005
        self.exit_minutes_before_close = 5
        self.cancel_unfilled_minutes_before_close = 10
        self.enable_shorts = True
        self.current_rank_date = None

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
        self.manage_end_of_day(symbol, state)

    def after_on_data(self, symbol_states):
        if not self.should_rank_now():
            return

        for state in symbol_states.values():
            self.ensure_orb_day(state)

        if any(state.orb_ranked for state in symbol_states.values()):
            return

        candidates = self.rank_opening_range_candidates(symbol_states)

        for rank, (symbol, state) in enumerate(candidates, start=1):
            self.submit_entry(symbol, state, rank)

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

        return selected

    def is_valid_candidate(self, symbol, state):
        if state.orb_open is None or state.orb_high is None or state.orb_low is None:
            return False

        if state.orb_close is None or state.orb_close <= 0:
            return False

        if state.orb_close < self.min_price:
            return False

        if state.avg_daily_volume_14 is None or state.avg_daily_volume_14 < self.min_avg_daily_volume:
            return False

        if state.atr_14 is None or state.atr_14 < self.min_atr:
            return False

        if state.orb_relative_volume < self.min_opening_relative_volume:
            return False

        if state.orb_high <= state.orb_low:
            return False

        if state.orb_close > state.orb_open:
            state.orb_direction = "LONG"
            return True

        if self.enable_shorts and state.orb_close < state.orb_open:
            state.orb_direction = "SHORT"
            return True

        return False

    def submit_entry(self, symbol, state, rank):
        if state.orb_entry_order_id is not None:
            return

        if state.orb_direction == "LONG":
            entry = state.orb_high * (1.0 + self.entry_buffer_pct)
            stop = entry - (state.atr_14 * self.atr_stop_fraction)
            quantity = self.calculate_quantity(entry, stop)
        elif state.orb_direction == "SHORT":
            entry = state.orb_low * (1.0 - self.entry_buffer_pct)
            stop = entry + (state.atr_14 * self.atr_stop_fraction)
            quantity = -self.calculate_quantity(entry, stop)
        else:
            return

        if quantity == 0:
            self.debugger.count_reject("qty")
            return

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
            return

        state.orb_entry_order_id = ticket.OrderId
        state.orb_entry_price = entry
        state.orb_stop_price = stop
        state.orb_quantity = quantity
        self.debugger.count("entry_submit", symbol)
        self.debugger.c_log(
            "E",
            symbol,
            (
                f"ORB|d={state.orb_direction}|p={entry:.2f}|sl={stop:.2f}"
                f"|n={quantity}|rk={rank}|rv={state.orb_relative_volume:.1f}|atr={state.atr_14:.2f}"
            ),
        )

    def calculate_quantity(self, entry, stop):
        risk_per_share = abs(entry - stop)

        if risk_per_share <= 0 or entry <= 0:
            return 0

        total_equity = float(self.algorithm.Portfolio.TotalPortfolioValue)
        cash = float(self.algorithm.Portfolio.Cash)
        deployable_cash = max(0.0, cash - (total_equity * self.risk.cash_reserve_pct))
        risk_budget = total_equity * self.risk_per_trade_pct
        capital_budget = total_equity * self.max_capital_per_trade_pct

        return max(
            0,
            min(
                int(risk_budget / risk_per_share),
                int(capital_budget / entry),
                int(deployable_cash / entry),
            ),
        )

    def manage_open_orders(self, symbol, state):
        if state.orb_entry_order_id is None or state.orb_stop_order_id is not None:
            return

        if int(self.algorithm.Portfolio[symbol].Quantity) != 0:
            return

        if self.minutes_since_midnight() >= 16 * 60 - self.cancel_unfilled_minutes_before_close:
            self.cancel_order(state.orb_entry_order_id, "orb_cancel_eod")
            state.orb_entry_order_id = None

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

    def handle_entry_fill(self, symbol, state, order_event):
        fill_quantity = int(order_event.FillQuantity)

        if fill_quantity == 0:
            return

        state.orb_entry_order_id = None

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

    def cancel_order(self, order_id, tag):
        if order_id is None:
            return

        ticket = self.algorithm.Transactions.GetOrderTicket(order_id)

        if ticket is not None:
            ticket.Cancel(tag)

    def minutes_since_midnight(self):
        now = self.algorithm.Time
        return now.hour * 60 + now.minute
