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
from dataclasses import dataclass, field
from src.trading_runtime.rule_decision_basis_projection import rule_decision_semantic_hash


_NATIVE_DECISION_PROOF = object()


@dataclass(frozen=True, slots=True)
class _NativeDecisionEvidence:
    run_id: str
    basis_sequence: int
    semantic_hash: str
    token: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class QualifiedRuleDecision:
    run_id: str
    basis_sequence: int
    semantic_hash: str
    selection: object
    _proof: object = field(repr=False)


async def qualify_rule_decision_runtime(parent, children, records, *, runtime,
        cold, prefix, broker, oms_image, packet, **scope):
    """Join native historical finances/OMS to offline reselection, without admission."""
    import asyncio
    from datetime import datetime
    from .program_financial_recovery import qualify_program_financial_runtime
    from .program_policy_recovery import RecoveredProgramPolicy, _RECOVERY_PROOF, verify_program_filled_geometry
    from .qmd_columnar_history_transport import _owned_worker
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
    at = datetime.fromisoformat(parent["boundary_at"])
    basis = parent["basis_sequence"]
    def require_scope():
        if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
                or type(runtime.broker) is not SimulatedBrokerAdapter
                or type(runtime.strategy) is not AssignedProgramStrategy
                or type(prefix) is not V4CommittedPrefix or type(packet) is not RecoveredProgramPolicy
                or packet._proof is not _RECOVERY_PROOF
                or runtime.run_id != parent["run_id"] or prefix.run_id != runtime.run_id
                or packet.run_id != runtime.run_id or packet.journal_sequence != basis
                or runtime.last_event_time != at or runtime.journal.latest_sequence(runtime.run_id) != basis
                or runtime.journal.pending_record_count or runtime.order_manager is None
                or runtime.strategy.program_release.header != packet.actor.program_release.header
                or runtime.strategy.assignments() != packet.actor.assignments()):
            raise ValueError("Rule decision native runtime differs from historical basis")
    require_scope()
    await _owned_worker(qualify_program_financial_runtime, runtime, cold, prefix, broker,
        oms_image=oms_image, through_sequence=basis)
    require_scope()
    capacity = asyncio.Semaphore(4)
    async def positions(account):
        async with capacity:
            return account, await runtime.broker.positions(account)
    broker_positions = dict(await asyncio.gather(*(positions(account) for account in runtime.config.account_ids)))
    require_scope()
    verify_program_filled_geometry(packet.actor, broker_positions, runtime.portfolio)
    assignment = packet.actor._by_assignment[parent["assignment_id"]]
    observation = observe_assignment_orders(runtime.order_manager, account_id=assignment.account_id,
        assignment_id=assignment.assignment_id, ticker=assignment.ticker, at=at)
    selection = reselect_recovered_rule_decision(parent, children, records,
        packet=packet, observation=observation, **scope)
    digest = rule_decision_semantic_hash(parent, children)
    evidence = _NativeDecisionEvidence(runtime.run_id, basis, digest, _NATIVE_DECISION_PROOF)
    return QualifiedRuleDecision(runtime.run_id, basis, digest, selection, evidence)


async def qualify_captured_rule_decision_runtime(parent, children, records, *,
        controller, runtime, cold, capture, packet, **scope):
    """Qualify a pending clock from owned native finances and replayed policy.

    No committed-prefix claim is made for the pending journal suffix. The
    original cold-prefix qualification remains available for durable recovery.
    """
    import asyncio
    from datetime import datetime
    from dataclasses import replace
    from .program_clock_financial_basis import verify_owned_program_clock_financial_basis
    from .program_financial_recovery import compare_captured_program_financial_runtime
    from .program_policy_recovery import (RecoveredProgramPolicy, _RECOVERY_PROOF,
        _ProgramPolicyContinuationSeal, program_policy_fingerprint, verify_program_filled_geometry)
    from .qmd_columnar_history_transport import _owned_worker
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
    at = datetime.fromisoformat(parent["boundary_at"])
    basis = parent["basis_sequence"]
    owned = controller._runtime
    digest = rule_decision_semantic_hash(parent, children)
    records = tuple(verify_rule_decision_intent_records(parent, children, records))
    intent_content = tuple(project_strategy_intent(record.intent) for record in records)
    def require_scope():
        seal = getattr(packet, "_continuation_seal", None)
        if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
                or type(runtime.broker) is not SimulatedBrokerAdapter
                or type(runtime.strategy) is not AssignedProgramStrategy
                or type(packet) is not RecoveredProgramPolicy or packet._proof is not _RECOVERY_PROOF
                or type(seal) is not _ProgramPolicyContinuationSeal or seal.token is not _RECOVERY_PROOF
                or (packet.run_id, packet.journal_sequence, packet.execution_count,
                    packet.completion_count, tuple(sorted(packet.completed_source_sequences.items())),
                    packet.source_commit_hashes, packet.state_hash) !=
                   (seal.run_id, seal.journal_sequence, seal.execution_count, seal.completion_count,
                    seal.completed, seal.source_hashes, seal.state_hash)
                or controller._runtime is not owned or runtime is owned
                or runtime.journal is owned.journal or runtime.broker is owned.broker
                or runtime.portfolio is owned.portfolio or runtime.order_manager is owned.order_manager
                or runtime.config != replace(owned.config, write_progress_checkpoints=False)
                or runtime.run_id != capture.run_id or runtime.run_id != parent["run_id"]
                or packet.run_id != runtime.run_id or packet.journal_sequence != basis
                or capture.journal_sequence != basis or capture.boundary_at != at
                or capture.source_boundary != (parent["source_batch_sequence"], parent["source_commit_hash"], at)
                or scope.get("source") is not controller._program_signal_runtime
                or runtime.last_event_time != at or runtime.journal.latest_sequence(runtime.run_id) != basis
                or runtime.journal.pending_record_count or runtime.order_manager is None
                or runtime.strategy.program_release.header != packet.actor.program_release.header
                or runtime.strategy.assignments() != packet.actor.assignments()
                or runtime.strategy.program_release.header["content_hash"] != capture.release_hash
                or packet.state_hash != capture.policy_hash
                or program_policy_fingerprint(packet.actor) != packet.state_hash
                or rule_decision_semantic_hash(parent, children) != digest
                or tuple(project_strategy_intent(record.intent) for record in records) != intent_content):
            raise ValueError("Pending rule decision differs from its owned native basis")
        verify_rule_decision_intent_records(parent, children, records)
    require_scope()
    await _owned_worker(verify_owned_program_clock_financial_basis, controller, capture)
    await _owned_worker(compare_captured_program_financial_runtime, runtime, cold, capture)
    require_scope()
    capacity = asyncio.Semaphore(4)
    async def positions(account):
        async with capacity:
            return account, await runtime.broker.positions(account)
    broker_positions = dict(await asyncio.gather(*(positions(account) for account in runtime.config.account_ids)))
    require_scope()
    verify_program_filled_geometry(packet.actor, broker_positions, runtime.portfolio)
    assignment = packet.actor._by_assignment[parent["assignment_id"]]
    observation = observe_assignment_orders(runtime.order_manager, account_id=assignment.account_id,
        assignment_id=assignment.assignment_id, ticker=assignment.ticker, at=at)
    selection = reselect_recovered_rule_decision(parent, children, records,
        packet=packet, observation=observation, **scope)
    await _owned_worker(verify_owned_program_clock_financial_basis, controller, capture)
    await _owned_worker(compare_captured_program_financial_runtime, runtime, cold, capture)
    require_scope()
    evidence = _NativeDecisionEvidence(runtime.run_id, basis, digest, _NATIVE_DECISION_PROOF)
    return QualifiedRuleDecision(runtime.run_id, basis, digest, selection, evidence)


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
