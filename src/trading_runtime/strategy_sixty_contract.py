"""Immutable declared first/current momentum contract."""
from .numbered_fixed_strategy import NumberedFixedStrategyContract
def strategy_sixty_contract():
    return NumberedFixedStrategyContract(60)
