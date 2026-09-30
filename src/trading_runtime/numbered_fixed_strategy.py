"""Explicit installed capabilities for sealed numbered fixed Backtests.

This registry is not release approval. The configuration reader must verify
the immutable publication before selecting a contract. No live/event route
may use these capabilities.
"""
from dataclasses import dataclass
from types import MappingProxyType

from .strategy_one_contract import STRATEGY_ID


SESSION_POLICY = MappingProxyType({
    "timezone": "America/New_York",
    "windows": (
        MappingProxyType({"start": "04:00", "entry_cutoff": "09:25", "liquidation_start": "09:29", "end": "09:30"}),
        MappingProxyType({"start": "16:00", "entry_cutoff": "19:50", "liquidation_start": "19:55", "end": "20:00"}),
    ),
    "cancel_pending_acquisition_at_cutoff": True,
    "fills": "later_certified_liquidity_only",
    "residual_at_end": "fail",
})


def session_policy_payload() -> dict:
    """Return a mutable JSON projection without exposing installed policy."""
    return {**SESSION_POLICY, "windows": [dict(window) for window in SESSION_POLICY["windows"]]}


@dataclass(frozen=True, slots=True)
class NumberedFixedStrategyContract:
    strategy_number: int
    strategy_id: str = STRATEGY_ID
    execution_interval: str = "100ms"

    @property
    def allows_session_exit(self) -> bool:
        return self.strategy_number == 2

    def entry_allowed(self, boundary_ms: int) -> bool:
        return (self.strategy_number == 1 or 0 < boundary_ms < 19_500_000
                or 43_200_000 < boundary_ms < 57_000_000)

    def acquisition_cutoff(self, boundary_ms: int) -> bool:
        return self.strategy_number == 2 and (
            19_500_000 <= boundary_ms <= 19_800_000 or 57_000_000 <= boundary_ms <= 57_600_000)

    def liquidation_due(self, boundary_ms: int) -> bool:
        return self.strategy_number == 2 and (
            19_740_000 <= boundary_ms <= 19_800_000 or 57_300_000 <= boundary_ms <= 57_600_000)


def numbered_fixed_strategy(number: int) -> NumberedFixedStrategyContract:
    if type(number) is not int or number not in (1, 2):
        raise ValueError("No installed numbered fixed Backtest contract")
    return NumberedFixedStrategyContract(number)


def resolve_numbered_fixed_strategy(strategy_id: str, revision: int) -> NumberedFixedStrategyContract:
    if strategy_id != STRATEGY_ID:
        raise ValueError("Numbered fixed Backtest strategy identity differs")
    return numbered_fixed_strategy(revision)


def is_numbered_fixed_strategy(strategy_id: str, revision: int) -> bool:
    return strategy_id == STRATEGY_ID and type(revision) is int and revision in (1, 2)
