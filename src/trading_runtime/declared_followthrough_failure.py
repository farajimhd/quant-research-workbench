"""Pure composition of an inherited failure and an explicitly declared extension."""
from collections.abc import Callable

from .early_original_risk_failure import EarlyOriginalRiskPolicy, early_original_risk_failure
from .strategy_followthrough_failure import FollowThroughFailure, FollowThroughFailureInput


def declared_followthrough_failure(
    value: FollowThroughFailureInput, *,
    inherited: Callable[[FollowThroughFailureInput], FollowThroughFailure | None],
    early_policy: EarlyOriginalRiskPolicy | None = None,
) -> FollowThroughFailure | None:
    """Keep inherited precedence and consume only existing completed producer facts."""
    if not callable(inherited) or (early_policy is not None and type(early_policy) is not EarlyOriginalRiskPolicy):
        raise ValueError('Failure composition requires its explicit inherited rule and policy')
    witness = inherited(value)
    if witness is not None or early_policy is None:
        return witness
    return early_original_risk_failure(value, policy=early_policy)
