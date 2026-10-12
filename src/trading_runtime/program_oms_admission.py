"""Pure program admission reconstruction from shared normalized facts.

The caller supplies a proposal reselected from certified causal inputs and
actual historical position/OMS state. This helper proves semantic equality and
Portfolio attribution; it neither supplies that historical state nor admits a
run. No opaque persistence or strategy-specific tables are introduced.
"""
from dataclasses import replace
from decimal import Decimal

from .arte_intent_projection import RecoveredIntent
from .columnar_program_coordinator import validate_program_proposal
from .portfolio import _intent_correlation


def restore_program_admitted_intent(release, proposal, source, *, run_id,
        account_id, assignment_id, reservation, decision,
        execution_policy, protection_profile, outside_rth):
    validate_program_proposal(release, proposal)
    expected = proposal.to_intent(execution_policy=execution_policy,
        protection_profile=protection_profile, outside_rth=outside_rth)
    if (type(source) is not RecoveredIntent or source.intent != expected
            or source.account_id != account_id or source.intent.metadata
            or not run_id or not assignment_id
            or reservation.get("run_id") != run_id or decision.get("run_id") != run_id
            or reservation.get("account_id") != account_id or decision.get("account_id") != account_id
            or reservation.get("strategy_id") != release.header["strategy_id"]
            or reservation.get("assignment_id") != assignment_id
            or reservation.get("intent_id") != expected.intent_id
            or reservation.get("ticker") != expected.ticker
            or reservation.get("action") != expected.action
            or reservation.get("status") != "reserved"
            or not reservation.get("account_key") or not reservation.get("reservation_id")
            or reservation.get("decision_id") != decision.get("decision_id")
            or reservation.get("reservation_id") != decision.get("reservation_id")
            or reservation.get("account_key") != decision.get("account_key")
            or decision.get("status") not in {"approved", "resized"}
            or not decision.get("policy_id")
            or type(decision.get("policy_revision")) is not int or decision["policy_revision"] < 1):
        raise ValueError("Program OMS source or normalized Portfolio admission differs")
    quantities = tuple(Decimal(str(value)) for value in
        (reservation["quantity"], decision["approved_quantity"], decision["requested_quantity"]))
    approved, decision_approved, requested = quantities
    if (any(not value.is_finite() or value <= 0 for value in quantities)
            or approved != decision_approved or approved > requested):
        raise ValueError("Program OMS admission quantity differs")
    metadata = dict(assignment_id=assignment_id,
        portfolio_account_key=reservation["account_key"],
        portfolio_decision_id=decision["decision_id"],
        unprotected_backtest_authorized=False,
        portfolio_policy=f"{decision['policy_id']}@{decision['policy_revision']}",
        portfolio_reservation_id=reservation["reservation_id"],
        requested_quantity=float(requested), portfolio_fx_to_base=1.,
        correlation_id=_intent_correlation(run_id, expected),
        causation_id=decision["decision_id"])
    return replace(expected, quantity=float(approved), metadata=metadata)
