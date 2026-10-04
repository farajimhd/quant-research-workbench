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


def restored_trailing_policy_payload() -> dict:
    """Strategy 7 restores the completed-low trailing branch with a frozen target."""
    return {"initial_stop": "completed_30s_bar_low", "completed_30s_low_trailing": True,
            "three_resistance_step_stop": True, "scope": "restore_subsequent_30s_low_trailing_only"}


def target_policy_payload() -> dict:
    """Strategy 6 retains the initial full-position profit target."""
    return {"initial_target": "ordinal_resistance_target", "target_escalation": False,
            "scope": "retain_initial_target_only"}


def entry_price_policy_payload() -> dict:
    """Strategy 8 caps acquisition at the proposal ask without changing persistence."""
    return {"maximum_buy_price": "proposal_reference_ask", "scope": "entry_and_reentry_only",
            "persist_until_cancelled": True, "partial_fill_policy": "complete_remainder"}


def followthrough_policy_payload() -> dict:
    """Strategy 9 consumes a complete post-fill five-second failure witness."""
    return {"resolution_ms": 5000, "loss_fraction_of_initial_stop_distance": 0.5,
            "entry_reference": "original_proposal_ask", "momentum": "macd_line < macd_signal",
            "price": "completed_close_and_fresh_bid <= (reference_ask + initial_stop) / 2",
            "first_bucket": "whole_bar_after_first_held_boundary", "maximum_quote_age_us": 1_000_000,
            "missing_input": "skip_current_proposal", "scope": "held_position_exit",
            "fill": "later_certified_liquidity_only"}


@dataclass(frozen=True, slots=True)
class NumberedFixedStrategyContract:
    strategy_number: int
    strategy_id: str = STRATEGY_ID
    execution_interval: str = "100ms"

    @property
    def allows_session_exit(self) -> bool:
        return self.strategy_number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)

    @property
    def allows_adds(self) -> bool:
        return self.strategy_number not in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)

    @property
    def allows_completed_30s_trailing(self) -> bool:
        return self.strategy_number not in (5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)

    @property
    def allows_target_escalation(self) -> bool:
        return self.strategy_number not in (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)

    @property
    def caps_entry_at_reference_ask(self) -> bool:
        return self.strategy_number in (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)

    @property
    def allows_followthrough_failure_exit(self) -> bool:
        return self.strategy_number in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)

    def entry_allowed(self, boundary_ms: int) -> bool:
        return (self.strategy_number == 1 or 0 < boundary_ms < 19_500_000
                or 43_200_000 < boundary_ms < 57_000_000)

    def activation_allowed(self, boundary_ms: int, episode_start_ms: int) -> bool:
        """Strategy 3 requires an episode born in this extended session."""
        if self.strategy_number not in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42):
            return True
        return (0 < episode_start_ms <= boundary_ms < 19_500_000
                or 43_200_000 < episode_start_ms <= boundary_ms < 57_000_000)

    def acquisition_cutoff(self, boundary_ms: int) -> bool:
        return self.strategy_number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42) and (
            19_500_000 <= boundary_ms <= 19_800_000 or 57_000_000 <= boundary_ms <= 57_600_000)

    def liquidation_due(self, boundary_ms: int) -> bool:
        return self.strategy_number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42) and (
            19_740_000 <= boundary_ms <= 19_800_000 or 57_300_000 <= boundary_ms <= 57_600_000)


def numbered_fixed_strategy(number: int) -> NumberedFixedStrategyContract:
    if type(number) is not int or number not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42):
        raise ValueError("No installed numbered fixed Backtest contract")
    return NumberedFixedStrategyContract(number)


def resolve_numbered_fixed_strategy(strategy_id: str, revision: int) -> NumberedFixedStrategyContract:
    if strategy_id != STRATEGY_ID:
        raise ValueError("Numbered fixed Backtest strategy identity differs")
    return numbered_fixed_strategy(revision)


def is_numbered_fixed_strategy(strategy_id: str, revision: int) -> bool:
    return strategy_id == STRATEGY_ID and type(revision) is int and revision in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)


_SESSION_EXIT_REASONS = MappingProxyType({
    2: "strategy_two_session_exit", 3: "strategy_three_session_exit",
    4: "strategy_four_session_exit", 5: "strategy_five_session_exit",
    6: "strategy_six_session_exit", 7: "strategy_seven_session_exit",
    8: "strategy_eight_session_exit", 9: "strategy_nine_session_exit",
    10: "strategy_ten_session_exit", 11: "strategy_eleven_session_exit",
    12: "strategy_twelve_session_exit", 13: "strategy_thirteen_session_exit",
    14: "strategy_fourteen_session_exit", 15: "strategy_fifteen_session_exit",
    16: "strategy_sixteen_session_exit", 17: "strategy_seventeen_session_exit",
    18: "strategy_eighteen_session_exit",
    19: "strategy_nineteen_session_exit",
    20: "strategy_twenty_session_exit",
    21: "strategy_twenty_one_session_exit",
    22: "strategy_twenty_two_session_exit",
    23: "strategy_twenty_three_session_exit",
    24: "strategy_twenty_four_session_exit",
    25: "strategy_twenty_five_session_exit",
    26: "strategy_twenty_six_session_exit",
    27: "strategy_twenty_seven_session_exit",
    28: "strategy_twenty_eight_session_exit",
    29: "strategy_twenty_nine_session_exit",
    30: "strategy_thirty_session_exit",
    31: "strategy_thirty_one_session_exit",
    32: "strategy_thirty_two_session_exit",
    33: "strategy_thirty_three_session_exit",
    34: "strategy_thirty_four_session_exit",
    35: "strategy_thirty_five_session_exit",
    36: "strategy_thirty_six_session_exit",
    37: "strategy_thirty_seven_session_exit",
    38: "strategy_thirty_eight_session_exit",
    39: "strategy_thirty_nine_session_exit",
    40: "strategy_forty_session_exit", 41: "strategy_forty_one_session_exit", 42: "strategy_forty_two_session_exit",
})


def numbered_session_exit_reason(strategy_number: int) -> str:
    """One exact reason for proposal, runtime admission and typed persistence.

    The installed contract remains the admission authority. A reason mapping
    cannot admit a future number or turn Strategy 1 into a session-exit policy.
    """
    contract = numbered_fixed_strategy(strategy_number)
    if not contract.allows_session_exit:
        raise ValueError("Numbered strategy has no session-exit reason")
    return _SESSION_EXIT_REASONS[strategy_number]
