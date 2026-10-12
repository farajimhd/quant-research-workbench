"""Historical decision verification using the shared filled-state selector.

The caller supplies an isolated replay actor at the exact financial basis.
This advances that actor's derived policy clocks; discard it after any failure.
No broker, Portfolio, journal writer or runtime admission is performed here.
"""
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.arte_intent_projection import project_strategy_intent
from src.trading_runtime.rule_decision_basis_projection import (
    verify_rule_decision_prepared_source, verify_rule_decision_intent_records,
)
from .qmd_columnar_population import CertifiedPopulationWindow
from .signal_stream_runtime_service import SignalStreamRuntime


def reselect_rule_decision(parent, children, records, *, actor, basis_sequence,
        block, population, source, observation, execution_policy, protection_profile, outside_rth):
    """Reselect and compare exact semantic intents from historical actual facts."""
    if (type(actor) is not AssignedProgramStrategy or type(basis_sequence) is not int
            or basis_sequence != parent["basis_sequence"]
            or type(population) is not CertifiedPopulationWindow
            or type(source) is not SignalStreamRuntime or source._typed_scope is None
            or type(outside_rth) is not bool
            or population.source_revision != source._typed_scope[1]):
        raise ValueError("Rule reselection requires its exact historical policy basis")
    assignment = actor._by_assignment.get(parent["assignment_id"])
    if assignment is None or actor._listings[assignment.assignment_id] != parent["listing_id"]:
        raise ValueError("Rule reselection assignment differs")
    verify_rule_decision_prepared_source(parent, children, release=actor.program_release,
        block=block, assignment=assignment, source=source)
    records = verify_rule_decision_intent_records(parent, children, records)
    boundary = block.boundary_for(actor.program_release, population,
        listing_id=parent["listing_id"], ticker=assignment.ticker, ordinal=parent["observed_ordinal"])
    selected = actor.select_from_orders(boundary, assignment_id=assignment.assignment_id,
        observation=observation, terminal=bool(parent["terminal"]))
    if selected.status != parent["selection_status"] or len(selected.proposals) != len(records):
        raise ValueError("Rule reselection status or intent coverage differs")
    for proposal, child, record in zip(selected.proposals, children, records, strict=True):
        expected = proposal.to_intent(execution_policy=execution_policy,
            protection_profile=protection_profile if proposal.action in {"enter_long", "add_long"} else None,
            outside_rth=outside_rth)
        if (proposal.stage != child["stage"] or proposal.intent_id != child["strategy_intent_id"]
                or project_strategy_intent(expected) != project_strategy_intent(record.intent)):
            raise ValueError("Rule reselection semantic intent differs from committed execution")
    return selected
