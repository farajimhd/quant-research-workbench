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


def reselect_recovered_rule_decision(parent, children, records, *, packet, **scope):
    """Use a verified journal replay packet without mutating its derived policy.

    This is an offline verifier. Actual historical OMS observations and native
    financial-image qualification remain required independent caller proofs.
    """
    from copy import deepcopy
    from .program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF, program_policy_fingerprint
    if (type(packet) is not RecoveredProgramPolicy or packet._proof is not _RECOVERY_PROOF
            or packet.run_id != parent["run_id"] or packet.journal_sequence != parent["basis_sequence"]
            or not 1 <= parent["source_batch_sequence"] <= len(packet.source_commit_hashes)
            or packet.source_commit_hashes[parent["source_batch_sequence"] - 1] != parent["source_commit_hash"]
            or program_policy_fingerprint(packet.actor) != packet.state_hash):
        raise ValueError("Rule reselection recovery packet differs from historical basis")
    original = packet.actor
    actor = AssignedProgramStrategy(original.program_release, original.assignments(), original._listings)
    # Selection only reads fills and target policy. Copy the two caches it
    # advances, avoiding a duplicate of the entire historical execution ledger.
    actor.execution_state = original.execution_state
    actor.target_state = original.target_state
    actor.trail_state = deepcopy(original.trail_state)
    actor._selection_clocks = dict(original._selection_clocks)
    selected = reselect_rule_decision(parent, children, records, actor=actor,
        basis_sequence=packet.journal_sequence, **scope)
    if program_policy_fingerprint(original) != packet.state_hash:
        raise ValueError("Rule reselection recovery packet changed during verification")
    return selected


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
