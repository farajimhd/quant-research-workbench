from AlgorithmImports import *
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
    ):
        self.algorithm = algorithm
        self.enable_console = enable_console
        self.enable_object_store = enable_object_store
        self.object_store_key = object_store_key
        self.events = []

    def c_log(self, code: str, symbol: Symbol, message: str):
        ticker = symbol.Value if symbol is not None else "-"
        text = f"{code}|{self.algorithm.Time.strftime('%m-%d %H:%M')}|{ticker}|{message}"

        if self.enable_console:
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

    def log_abnormal_expansion(self, symbol, price, move, rel_volume, spread, volume, high):
        self.c_log(
            "A",
            symbol,
            f"p={price:.2f}|mv={move*100:.1f}|rv={rel_volume:.1f}|sp={spread*100:.2f}|v={int(volume)}|hi={high:.2f}",
        )

    def log_leader_high(self, symbol, price, high):
        self.c_log("H", symbol, f"p={price:.2f}|hi={high:.2f}")

    def log_breakout_ready(self, symbol, price, level):
        self.c_log("B", symbol, f"p={price:.2f}|lvl={level:.2f}")

    def log_entry(self, symbol, entry, stop, risk, quantity, cash, breakout_high):
        self.c_log(
            "E",
            symbol,
            f"p={entry:.2f}|sl={stop:.2f}|r={risk:.2f}|q={quantity}|bh={breakout_high:.2f}|cash={cash:.0f}",
        )

    def log_add(self, symbol, price, quantity, add_count):
        self.c_log("G", symbol, f"add={add_count}|p={price:.2f}|q={quantity}")

    def log_exit(self, symbol, reason, price, r_multiple, extra=""):
        suffix = f"|{extra}" if extra else ""
        self.c_log("X", symbol, f"{reason}|p={price:.2f}|R={r_multiple:.2f}{suffix}")

    def log_reentry_watch(self, symbol, level):
        self.c_log("W", symbol, f"watch_rebreak|lvl={level:.2f}")

    def log_dead_leader(self, symbol, reason, price):
        self.c_log("D", symbol, f"{reason}|p={price:.2f}")

    def log_fill(self, order_event: OrderEvent):
        direction = "B" if order_event.Direction == OrderDirection.Buy else "S"
        self.c_log(
            "F",
            order_event.Symbol,
            f"{direction}|q={order_event.FillQuantity}|px={order_event.FillPrice:.2f}",
        )

    def flush(self):
        if not self.enable_object_store:
            return

        payload = json.dumps(self.events)
        self.algorithm.ObjectStore.Save(self.object_store_key, payload)

        if self.enable_console:
            self.algorithm.Debug(f"SAVED|{self.object_store_key}|n={len(self.events)}")