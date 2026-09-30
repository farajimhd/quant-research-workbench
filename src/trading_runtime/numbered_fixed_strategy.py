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


def activation_policy_payload() -> dict:
    """Strategy 3 entry-origin policy; held-position management is unchanged."""
    return {
        "clock": "milliseconds_since_04:00_America/New_York",
        "premarket_open_ms": 0,
        "afterhours_open_ms": 43_200_000,
        "comparison": "current_session_open_ms < episode_start_ms <= boundary_ms",
        "scope": "new_entry",
    }


def add_policy_payload() -> dict:
    """Strategy 4 ablates only additional position purchases."""
    return {"allows_adds": False, "scope": "disable_adds_only"}


def trailing_policy_payload() -> dict:
    """Strategy 5 retains entry protection and structural stop ratchets."""
    return {"initial_stop": "completed_30s_bar_low", "completed_30s_low_trailing": False,
            "three_resistance_step_stop": True, "scope": "disable_subsequent_30s_low_trailing_only"}


@dataclass(frozen=True, slots=True)
class NumberedFixedStrategyContract:
    strategy_number: int
    strategy_id: str = STRATEGY_ID
    execution_interval: str = "100ms"

    @property
    def allows_session_exit(self) -> bool:
        return self.strategy_number in (2, 3, 4, 5)

    @property
    def allows_adds(self) -> bool:
        return self.strategy_number not in (4, 5)

    @property
    def allows_completed_30s_trailing(self) -> bool:
        return self.strategy_number != 5

    def entry_allowed(self, boundary_ms: int) -> bool:
        return (self.strategy_number == 1 or 0 < boundary_ms < 19_500_000
                or 43_200_000 < boundary_ms < 57_000_000)

    def activation_allowed(self, boundary_ms: int, episode_start_ms: int) -> bool:
        """Strategy 3 requires an episode born in this extended session."""
        if self.strategy_number not in (3, 4, 5):
            return True
        return (0 < episode_start_ms <= boundary_ms < 19_500_000
                or 43_200_000 < episode_start_ms <= boundary_ms < 57_000_000)

    def acquisition_cutoff(self, boundary_ms: int) -> bool:
        return self.strategy_number in (2, 3, 4, 5) and (
            19_500_000 <= boundary_ms <= 19_800_000 or 57_000_000 <= boundary_ms <= 57_600_000)

    def liquidation_due(self, boundary_ms: int) -> bool:
        return self.strategy_number in (2, 3, 4, 5) and (
            19_740_000 <= boundary_ms <= 19_800_000 or 57_300_000 <= boundary_ms <= 57_600_000)


def numbered_fixed_strategy(number: int) -> NumberedFixedStrategyContract:
    if type(number) is not int or number not in (1, 2, 3, 4, 5):
        raise ValueError("No installed numbered fixed Backtest contract")
    return NumberedFixedStrategyContract(number)


def resolve_numbered_fixed_strategy(strategy_id: str, revision: int) -> NumberedFixedStrategyContract:
    if strategy_id != STRATEGY_ID:
        raise ValueError("Numbered fixed Backtest strategy identity differs")
    return numbered_fixed_strategy(revision)


def is_numbered_fixed_strategy(strategy_id: str, revision: int) -> bool:
    return strategy_id == STRATEGY_ID and type(revision) is int and revision in (1, 2, 3, 4, 5)
