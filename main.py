from __future__ import annotations

from AlgorithmImports import *
from collections import defaultdict
import json

# =============================================================================
# Compact Debugging and Event Logging
# =============================================================================

class DebugManager:

    def __init__(
        self,
        algorithm: QCAlgorithm,
        enable_console: bool = True,
        enable_object_store: bool = True,
        object_store_key: str = "momentum_event_logs.json",
        run_label: str = "",
    ):
        self.algorithm = algorithm
        self.enable_console = enable_console
        self.enable_object_store = enable_object_store
        self.object_store_key = object_store_key
        self.run_label = run_label
        self.events = []
        self.counters = defaultdict(int)
        self.leader_tickers = set()
        self.entry_tickers = set()
        self.exit_tickers = set()
        self.last_summary_date = None
        self.max_console_events_per_day = {
            "A": 0,
            "B": 0,
            "E": 1,
            "X": 1,
            "RJ": 1,
            "D": 0,
            "W": 0,
        }
        self.daily_code_counts = defaultdict(int)
        self.log_run_header()

    def log_run_header(self):
        if not self.run_label:
            return

        message = f"RUN|{self.run_label}"

        if self.enable_console:
            self.algorithm.Debug(message[:190])

        if self.enable_object_store:
            self.events.append(
                {
                    "time": str(self.algorithm.Time),
                    "code": "RUN",
                    "ticker": "-",
                    "message": self.run_label,
                }
            )

    def c_log(self, code: str, symbol: Symbol, message: str):
        ticker = symbol.Value if symbol is not None else "-"
        text = f"{code}|{ticker}|{message}"
        counter_key = code

        if code == "RJ":
            reject_type = message.split("|", 1)[0]
            counter_key = f"{code}:{reject_type}"

        self.daily_code_counts[counter_key] += 1
        limit = self.max_console_events_per_day.get(code)
        emit_console = limit is None or self.daily_code_counts[counter_key] <= limit

        if self.enable_console and emit_console:
            self.algorithm.Debug(text[:190])

        if self.enable_object_store:
            self.events.append(
                {
                    "time": str(self.algorithm.Time),
                    "code": code,
                    "ticker": ticker,
                    "message": message,
                }
            )

    def count(self, key: str, symbol=None):
        self.counters[key] += 1

        if symbol is None:
            return

        ticker = symbol.Value if hasattr(symbol, "Value") else str(symbol)

        if key.startswith("lead"):
            self.leader_tickers.add(ticker)
        elif key.startswith("entry"):
            self.entry_tickers.add(ticker)
        elif key.startswith("exit"):
            self.exit_tickers.add(ticker)

    def count_reject(self, reason: str):
        self.counters[f"rj_{reason}"] += 1

    def emit_daily_summary_if_needed(self):
        current_date = self.algorithm.Time.date()

        if self.last_summary_date is None:
            self.last_summary_date = current_date
            return

        if current_date == self.last_summary_date:
            return

        self.log_daily_summary()
        self.reset_daily()

    def reset_daily(self):
        self.counters.clear()
        self.leader_tickers.clear()
        self.entry_tickers.clear()
        self.exit_tickers.clear()
        self.daily_code_counts.clear()
        self.last_summary_date = self.algorithm.Time.date()

    def log_daily_summary(self):
        parts = [
            f"l={self.counters['lead']}",
            f"b={self.counters['breakout']}",
            f"e={self.counters['entry_submit']}",
            f"q={self.counters['q_AP']}/{self.counters['q_A']}/{self.counters['q_B']}/{self.counters['q_C']}",
            (
                f"x={self.counters['exit_signal']}/"
                f"{self.counters['exit_ENTRY_FAIL']}/"
                f"{self.counters['exit_EARLY_FAIL']}/"
                f"{self.counters['exit_PROFIT_PULLBACK']}/"
                f"{self.counters['exit_STOP_LOSS']}/"
                f"{self.counters['exit_TRAIL_STOP']}/"
                f"{self.counters['exit_NO_PROGRESS']}/"
                f"{self.counters['exit_MOMENTUM_CLOSE']}/"
                f"{self.counters['exit_EOD']}"
            ),
            (
                f"r={self.counters['rj_spread']}/"
                f"{self.counters['rj_spread_risk']}/"
                f"{self.counters['rj_setup']}/"
                f"{self.counters['rj_quality']}/"
                f"{self.counters['rj_economics']}/"
                f"{self.counters['rj_pullback']}"
            ),
            (
                f"pb={self.counters['pb_rv']}/"
                f"{self.counters['pb_macd']}/"
                f"{self.counters['pb_tema']}/"
                f"{self.counters['pb_vol']}/"
                f"{self.counters['pb_same']}"
            ),
            (
                f"or={self.counters['or_base']}/"
                f"{self.counters['or_liq']}/"
                f"{self.counters['or_atr']}/"
                f"{self.counters['or_rv']}/"
                f"{self.counters['or_gap']}/"
                f"{self.counters['or_shape']}/"
                f"{self.counters['or_range']}/"
                f"{self.counters['or_econ']}"
            ),
            f"d={self.counters['dead']}",
            f"s={self.counters['stale']}",
            f"lt={len(self.leader_tickers)}",
            f"et={len(self.entry_tickers)}",
        ]

        self.c_log("S", None, "|".join(parts))

    def log_abnormal_expansion(self, symbol, price, move, rel_volume, spread, volume, high):
        self.c_log(
            "A",
            symbol,
            f"p={price:.2f}|mv={move*100:.1f}|rv={rel_volume:.1f}|sp={spread*100:.2f}",
        )
        self.count("lead", symbol)

    def log_leader_high(self, symbol, price, high):
        return

    def log_breakout_ready(self, symbol, price, level):
        self.c_log("B", symbol, f"p={price:.2f}|lvl={level:.2f}")
        self.count("breakout", symbol)

    def log_entry(
        self,
        symbol,
        entry,
        stop,
        risk,
        quantity,
        cash,
        breakout_high,
        quality_score=None,
        quality_bucket=None,
        risk_pct=None,
        relative_volume=None,
        spread_to_risk=None,
        stop_pct=None,
        extension_pct=None,
        reentry_attempts=0,
        entry_type="",
    ):
        quality = ""

        if quality_score is not None and quality_bucket is not None and risk_pct is not None:
            quality = f"|q={quality_bucket}{quality_score}|rp={risk_pct * 100:.2f}"

        metrics = ""

        if (
            relative_volume is not None
            and spread_to_risk is not None
            and stop_pct is not None
            and extension_pct is not None
        ):
            metrics = (
                f"|t={entry_type}|rv={relative_volume:.1f}|sr={spread_to_risk:.2f}"
                f"|st={stop_pct * 100:.1f}|ex={extension_pct * 100:.1f}|re={reentry_attempts}"
            )

        self.c_log(
            "E",
            symbol,
            f"p={entry:.2f}|sl={stop:.2f}|n={quantity}|bh={breakout_high:.2f}{quality}{metrics}",
        )
        self.count("entry_submit", symbol)

        if quality_bucket is not None:
            self.count(f"q_{quality_bucket}")

    def log_add(self, symbol, price, quantity, add_count):
        self.c_log("G", symbol, f"add={add_count}|p={price:.2f}|q={quantity}")

    def log_exit(self, symbol, reason, price, r_multiple, extra=""):
        suffix = f"|{extra}" if extra else ""
        self.c_log("X", symbol, f"{reason}|p={price:.2f}|R={r_multiple:.2f}{suffix}")
        self.count("exit_signal", symbol)
        self.count(f"exit_{reason}", symbol)

    def log_reentry_watch(self, symbol, level):
        self.c_log("W", symbol, f"rebreak|lvl={level:.2f}")

    def log_dead_leader(self, symbol, reason, price):
        self.c_log("D", symbol, f"{reason}|p={price:.2f}")
        if "stale" in reason:
            self.count("stale")
        else:
            self.count("dead")

    def log_fill(self, order_event: OrderEvent):
        return

    def flush(self):
        if not self.enable_object_store:
            return

        self.log_daily_summary()

        payload = json.dumps(self.events)
        self.algorithm.ObjectStore.Save(self.object_store_key, payload)

        if self.enable_console:
            self.algorithm.Debug(f"SAVED|{self.object_store_key}|n={len(self.events)}")

# =============================================================================
# Risk, Cash, and Position Sizing
# =============================================================================

class RiskManager:

    def __init__(
        self,
        algorithm: QCAlgorithm,
        risk_per_trade_pct: float = 0.005,
        max_capital_per_trade_pct: float = 0.15,
        cash_reserve_pct: float = 0.05,
    ):
        self.algorithm = algorithm
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_capital_per_trade_pct = max_capital_per_trade_pct
        self.cash_reserve_pct = cash_reserve_pct

    # =========================================================================
    # Position Sizing
    # =========================================================================

    def calculate_quantity(
        self,
        entry_price: float,
        stop_price: float,
        risk_per_trade_pct=None,
        max_capital_per_trade_pct=None,
    ) -> int:
        risk_per_share = entry_price - stop_price

        if risk_per_share <= 0:
            return 0

        total_equity = float(self.algorithm.Portfolio.TotalPortfolioValue)
        available_cash = float(self.algorithm.Portfolio.Cash)

        reserved_cash = total_equity * self.cash_reserve_pct
        deployable_cash = max(0.0, available_cash - reserved_cash)

        risk_pct = self.risk_per_trade_pct if risk_per_trade_pct is None else risk_per_trade_pct
        capital_pct = (
            self.max_capital_per_trade_pct
            if max_capital_per_trade_pct is None
            else max_capital_per_trade_pct
        )

        risk_budget = total_equity * risk_pct
        capital_budget = total_equity * capital_pct

        risk_based_quantity = int(risk_budget / risk_per_share)
        capital_based_quantity = int(capital_budget / entry_price)
        cash_based_quantity = int(deployable_cash / entry_price)

        quantity = min(
            risk_based_quantity,
            capital_based_quantity,
            cash_based_quantity,
        )

        return max(quantity, 0)

class SymbolState:

    def __init__(self, symbol: Symbol):
        self.symbol = symbol

        self.avg_daily_volume_14 = None
        self.atr_14 = None
        self.previous_close = None

        self.orb_date = None
        self.orb_open = None
        self.orb_high = None
        self.orb_low = None
        self.orb_close = None
        self.orb_volume = 0.0
        self.orb_relative_volume = 0.0
        self.orb_direction = None
        self.orb_ranked = False

        self.orb_entry_order_id = None
        self.orb_entry_order_time = None
        self.orb_stop_order_id = None
        self.orb_entry_price = None
        self.orb_initial_stop_price = None
        self.orb_stop_price = None
        self.orb_highest_since_entry = None
        self.orb_breakeven_applied = False
        self.orb_quantity = 0
        self.orb_exit_submitted = False

    def reset_orb_day(self, current_date):
        self.orb_date = current_date
        self.orb_open = None
        self.orb_high = None
        self.orb_low = None
        self.orb_close = None
        self.orb_volume = 0.0
        self.orb_relative_volume = 0.0
        self.orb_direction = None
        self.orb_ranked = False

        self.orb_entry_order_id = None
        self.orb_entry_order_time = None
        self.orb_stop_order_id = None
        self.orb_entry_price = None
        self.orb_initial_stop_price = None
        self.orb_stop_price = None
        self.orb_highest_since_entry = None
        self.orb_breakeven_applied = False
        self.orb_quantity = 0
        self.orb_exit_submitted = False

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

# =============================================================================
# Main QuantConnect Algorithm
# =============================================================================

class Main(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2024, 5, 1)
        self.SetEndDate(2024, 6, 1)
        self.SetCash(10000)

        # ---------------------------------------------------------------------
        # Universe configuration.
        # ---------------------------------------------------------------------
        self.min_price = 0.75
        self.max_price = 50.0
        self.max_float_or_shares = 500_000_000
        self.min_daily_dollar_volume = 2_000_000

        self.UniverseSettings.Resolution = Resolution.Minute
        self.UniverseSettings.ExtendedMarketHours = False
        self.AddUniverse(self.UniverseSelection)

        # ---------------------------------------------------------------------
        # Stable helper modules.
        # ---------------------------------------------------------------------
        self.debugger = DebugManager(
            algorithm=self,
            enable_console=True,
            enable_object_store=True,
            object_store_key="momentum_event_logs.json",
            run_label="v-orb-loose-protect: one full-cash long ORB, BE at 1.5R, trail after 3R keeping 30pct MFE",
        )

        self.risk = RiskManager(
            algorithm=self,
            risk_per_trade_pct=0.005,
            max_capital_per_trade_pct=0.15,
            cash_reserve_pct=0.05,
        )

        # ---------------------------------------------------------------------
        # Core strategy module.
        # ---------------------------------------------------------------------
        self.core = OpeningRangeBreakoutCore(
            algorithm=self,
            debugger=self.debugger,
            risk_manager=self.risk,
        )

        self.symbol_states = {}
        self.SetBenchmark("SPY")

    # =========================================================================
    # Universe Selection
    # =========================================================================

    def UniverseSelection(self, fundamentals):
        selected = []

        for f in fundamentals:
            if not self.IsTradableCommonStock(f):
                continue

            if f.Price is None or f.Price < self.min_price or f.Price > self.max_price:
                continue

            if f.DollarVolume is None or f.DollarVolume < self.min_daily_dollar_volume:
                continue

            shares = self.GetSharesOutstandingProxy(f)

            if shares is None or shares <= 0:
                continue

            if shares > self.max_float_or_shares:
                continue

            selected.append(f)

        selected = sorted(selected, key=lambda x: x.DollarVolume, reverse=True)

        return [f.Symbol for f in selected[:500]]

    def IsTradableCommonStock(self, f) -> bool:
        """
        Keep only real operating-company common stocks as much as possible.

        This version avoids direct access to fields that may not exist in the
        QuantConnect Morningstar SecurityReference object.
        """

        symbol_text = f.Symbol.Value.upper()

        # -------------------------------------------------------------------------
        # Hard symbol denylist for ETF / leveraged ETF / fund-like instruments that
        # repeatedly appeared in our logs.
        # -------------------------------------------------------------------------
        blocked_symbols = {
            "KWEB", "YINN", "CWEB", "NUGT", "JNUG", "GDXU", "AGQ", "SCO",
            "TNA", "URTY", "DRN", "CONL", "ARKG", "MSOS", "URA",
        }

        if symbol_text in blocked_symbols:
            return False

        # -------------------------------------------------------------------------
        # Suffix-based exclusions for warrants, units, preferreds, and rights.
        # -------------------------------------------------------------------------
        blocked_suffixes = [
            ".U", ".WS", ".WT", ".W", ".P", ".PR", ".R",
            "-U", "-WS", "-WT", "-W", "-P", "-PR", "-R",
        ]

        if any(symbol_text.endswith(suffix) for suffix in blocked_suffixes):
            return False

        # -------------------------------------------------------------------------
        # Use attributes only if they actually exist.
        # -------------------------------------------------------------------------
        security_reference = getattr(f, "SecurityReference", None)

        if security_reference is not None:
            security_type = getattr(security_reference, "SecurityType", None)

            if security_type is not None:
                security_type_text = str(security_type).upper()

                # -----------------------------------------------------------------
                # Common stock is commonly represented as ST00000001.
                # If the field exists and says something else, reject it.
                # -----------------------------------------------------------------
                if security_type_text and security_type_text != "ST00000001":
                    return False

            is_depositary_receipt = getattr(
                security_reference,
                "IsDepositaryReceipt",
                False,
            )

            if bool(is_depositary_receipt):
                return False

            is_preferred_stock = getattr(
                security_reference,
                "IsPreferredStock",
                False,
            )

            if bool(is_preferred_stock):
                return False

        return True
    

    def GetSharesOutstandingProxy(self, f):
        """
        QuantConnect may not expose public float directly for every symbol.

        This uses recent basic average shares as a practical proxy. If you later
        add a custom float dataset, replace this method only.
        """

        try:
            shares = f.EarningReports.BasicAverageShares.ThreeMonths

            if shares is not None and shares > 0:
                return float(shares)
        except Exception:
            pass

        try:
            shares = f.EarningReports.BasicAverageShares.TwelveMonths

            if shares is not None and shares > 0:
                return float(shares)
        except Exception:
            pass

        return None

    # =========================================================================
    # Security Lifecycle
    # =========================================================================

    def OnSecuritiesChanged(self, changes):
        for security in changes.AddedSecurities:
            symbol = security.Symbol

            if symbol not in self.symbol_states:
                self.symbol_states[symbol] = SymbolState(symbol)
                self.UpdateDailyStats(symbol, self.symbol_states[symbol])

        for security in changes.RemovedSecurities:
            symbol = security.Symbol

            if self.Portfolio[symbol].Invested:
                self.Liquidate(symbol, "Removed from universe")

            self.symbol_states.pop(symbol, None)

    # =========================================================================
    # Main Data Loop
    # =========================================================================

    def OnData(self, data):
        self.debugger.emit_daily_summary_if_needed()
        self.core.on_data_start()

        for symbol, state in list(self.symbol_states.items()):
            if not data.Bars.ContainsKey(symbol):
                continue

            bar = data.Bars[symbol]
            self.core.process_symbol(symbol, state, bar)

        self.core.after_on_data(self.symbol_states)

    def UpdateDailyStats(self, symbol, state):
        history = self.History(symbol, 20, Resolution.Daily)

        if history is None or history.empty:
            return

        history = history.reset_index()
        columns = {str(column).lower(): column for column in history.columns}

        required_columns = ["high", "low", "close", "volume"]

        if any(column not in columns for column in required_columns):
            self.debugger.c_log("RJ", symbol, "hist_cols")
            return

        highs = []
        lows = []
        closes = []
        volumes = []

        for _, row in history.iterrows():
            try:
                highs.append(float(row[columns["high"]]))
                lows.append(float(row[columns["low"]]))
                closes.append(float(row[columns["close"]]))
                volumes.append(float(row[columns["volume"]]))
            except Exception:
                continue

        if len(volumes) >= 14:
            state.avg_daily_volume_14 = sum(volumes[-14:]) / 14.0

        if len(closes) > 0:
            state.previous_close = closes[-1]

        if len(closes) < 15:
            return

        true_ranges = []

        for i in range(1, len(closes)):
            true_ranges.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

        if len(true_ranges) >= 14:
            state.atr_14 = sum(true_ranges[-14:]) / 14.0

    # =========================================================================
    # Order Event Handler
    # =========================================================================

    def OnOrderEvent(self, order_event):
        if order_event.Status != OrderStatus.Filled:
            return

        self.debugger.log_fill(order_event)

        state = self.symbol_states.get(order_event.Symbol)

        if state is None:
            return

        self.core.handle_order_event(order_event.Symbol, state, order_event)

    # =========================================================================
    # End of Algorithm
    # =========================================================================

    def OnEndOfAlgorithm(self):
        self.debugger.flush()
