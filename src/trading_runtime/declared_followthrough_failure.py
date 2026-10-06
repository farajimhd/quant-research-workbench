"""Pure composition of an inherited failure and an explicitly declared extension."""
from collections.abc import Callable

from .early_original_risk_failure import EarlyOriginalRiskPolicy, early_original_risk_failure
from .all_held_original_risk_failure import AllHeldOriginalRiskPolicy, all_held_original_risk_failure
from .strategy_followthrough_failure import FollowThroughFailure, FollowThroughFailureInput


def declared_followthrough_failure(
    value: FollowThroughFailureInput, *,
    inherited: Callable[[FollowThroughFailureInput], FollowThroughFailure | None],
    early_policy: EarlyOriginalRiskPolicy | None = None,
    all_held_policy: AllHeldOriginalRiskPolicy | None = None,
) -> FollowThroughFailure | None:
    """Keep inherited precedence and consume only existing completed producer facts."""
    if not callable(inherited) or (early_policy is not None and type(early_policy) is not EarlyOriginalRiskPolicy):
        raise ValueError('Failure composition requires its explicit inherited rule and policy')
    if all_held_policy is not None and type(all_held_policy) is not AllHeldOriginalRiskPolicy:
        raise ValueError('Failure composition requires its exact all-held policy')
    witness = inherited(value)
    if witness is not None:
        return witness
    if early_policy is not None:
        witness = early_original_risk_failure(value, policy=early_policy)
        if witness is not None:
            return witness
    return (all_held_original_risk_failure(value, policy=all_held_policy)
            if all_held_policy is not None else None)
