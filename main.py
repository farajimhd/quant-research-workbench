from AlgorithmImports import *

from debug_tools import DebugManager
from orb_core import OpeningRangeBreakoutCore
from risk_tools import RiskManager
from state import SymbolState


# =============================================================================
# Main QuantConnect Algorithm
# =============================================================================

class SmallFloatMomentumBreakoutAlgorithm(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2024, 5, 1)
        self.SetEndDate(2024, 6, 1)
        self.SetCash(10000)

        # ---------------------------------------------------------------------
        # Universe configuration.
        # ---------------------------------------------------------------------
        self.min_price = 0.75
        self.max_price = 500.0
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
            run_label="v-orb-box-macd-tema: box break with 5m MACD open and TEMA9>TEMA20, exits on TEMA close or box mid",
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
            if f.Price is None or f.Price < self.min_price or f.Price > self.max_price:
                continue

            if f.DollarVolume is None or f.DollarVolume < self.min_daily_dollar_volume:
                continue

            selected.append(f)

        selected = sorted(selected, key=lambda x: x.DollarVolume, reverse=True)

        return [f.Symbol for f in selected[:500]]

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
