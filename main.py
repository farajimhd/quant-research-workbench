from AlgorithmImports import *

from alpha_core import MomentumAlphaCore
from debug_tools import DebugManager
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
        self.max_price = 50.0
        self.max_float_or_shares = 500_000_000
        self.min_daily_dollar_volume = 2_000_000

        self.UniverseSettings.Resolution = Resolution.Minute
        self.UniverseSettings.ExtendedMarketHours = True
        self.AddUniverse(self.UniverseSelection)

        # ---------------------------------------------------------------------
        # Stable helper modules.
        # ---------------------------------------------------------------------
        self.debugger = DebugManager(
            algorithm=self,
            enable_console=True,
            enable_object_store=True,
            object_store_key="momentum_event_logs.json",
            run_label="v-next-reclaim-debug: delay structure trail until MFE, compact pullback reject reasons",
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
        self.core = MomentumAlphaCore(
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

        for symbol, state in list(self.symbol_states.items()):
            self.UpdateQuoteSnapshot(symbol, state, data)

            self.core.process_quote(symbol, state)

            if not data.Bars.ContainsKey(symbol):
                continue

            bar = data.Bars[symbol]
            state.update_intrabar(bar)
            state.update_bar(bar)

            self.core.process_symbol(symbol, state, bar)

    def UpdateQuoteSnapshot(self, symbol, state, data):
        bid = None
        ask = None

        if data.QuoteBars.ContainsKey(symbol):
            quote = data.QuoteBars[symbol]

            if quote.Bid is not None:
                bid = float(quote.Bid.Close)

            if quote.Ask is not None:
                ask = float(quote.Ask.Close)

        if bid is None or ask is None:
            security = self.Securities[symbol]
            bid = float(security.BidPrice)
            ask = float(security.AskPrice)

        state.update_quote(bid, ask, self.Time)

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
