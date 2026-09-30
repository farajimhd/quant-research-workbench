"""Unpublished Strategy 11 rule: failed entry within one minute of first fill.

The original Strategy 9/10 predicate remains immutable. This narrower rule
reuses its normalized scalar witness and adds a causal eligibility bound.
It does not submit exits, compute indicators, or grant runtime admission.
"""
from .strategy_followthrough_failure import (
    FollowThroughFailure,
    FollowThroughFailureInput,
    followthrough_failure,
)


EARLY_FAILURE_WINDOW_MS = 60_000


def early_followthrough_failure(
    value: FollowThroughFailureInput,
) -> FollowThroughFailure | None:
    """Apply the unchanged failure conditions only during the first minute.

    Age starts at the first completed broker bucket containing held quantity,
    not the signal, order submission, or eventual acquisition VWAP. The upper
    bound is inclusive; no exit is proposed solely because time has elapsed.
    Validate the original causal authority even outside the eligibility window.
    Missing evidence and subsequent protection remain governed by the original
    predicate and the shared broker/OMS contracts respectively.
    """
    witness = followthrough_failure(value)
    if (witness is None
            or witness.boundary_ms - witness.first_held_boundary_ms
            > EARLY_FAILURE_WINDOW_MS):
        return None
    return witness
