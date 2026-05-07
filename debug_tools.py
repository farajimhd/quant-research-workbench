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
