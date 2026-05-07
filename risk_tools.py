from AlgorithmImports import *


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

    def calculate_quantity(self, entry_price: float, stop_price: float) -> int:
        risk_per_share = entry_price - stop_price

        if risk_per_share <= 0:
            return 0

        total_equity = float(self.algorithm.Portfolio.TotalPortfolioValue)
        available_cash = float(self.algorithm.Portfolio.Cash)

        reserved_cash = total_equity * self.cash_reserve_pct
        deployable_cash = max(0.0, available_cash - reserved_cash)

        risk_budget = total_equity * self.risk_per_trade_pct
        capital_budget = total_equity * self.max_capital_per_trade_pct

        risk_based_quantity = int(risk_budget / risk_per_share)
        capital_based_quantity = int(capital_budget / entry_price)
        cash_based_quantity = int(deployable_cash / entry_price)

        quantity = min(
            risk_based_quantity,
            capital_based_quantity,
            cash_based_quantity,
        )

        return max(quantity, 0)