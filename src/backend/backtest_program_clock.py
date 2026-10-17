"""Complete prepared-clock dispatch on the existing controller and broker."""
from datetime import datetime
from collections.abc import Mapping


async def dispatch_qualified_program_boundary(controller, block, population, *, packet=None,
        ordinal, assignment_id, execution_policy, protection_profile, outside_rth, terminal=False,
        policy_blocks=None, maximum_events=None, initial_packet=None):
    """Capture, dispatch, independently qualify and retain one native boundary.

    The policy prefix is independently replayed or explicitly supplied. Publication
    stays batched by the existing publisher; no producer or writer starts here.
    """
    from dataclasses import replace
    from .backtest_program_session import require_program_session_owner
    require_program_session_owner(controller)
    from .program_clock_financial_basis import capture_program_clock_financial_basis
    from .program_policy_recovery import (RecoveredProgramPolicy, _RECOVERY_PROOF,
        _ProgramPolicyContinuationSeal, program_policy_fingerprint)
    from .program_runtime_recovery import build_recovered_program_runtime
    from .program_financial_recovery import compare_captured_program_financial_runtime
    from .backtest_program_publication import qualify_captured_pending_program_decision
    from .backtest_typed_publisher import BacktestTypedJournalPublisher
    from .backtest_journal_memory import BacktestMemoryJournal
    from .qmd_columnar_history_transport import _owned_worker
    from src.trading_runtime.arte_portfolio_recovery import recover_prepared_portfolio_engine_state
    from .program_clock_financial_basis import prepare_program_clock_financial_basis
    from .replay_run_service import ReplayRunController
    from .backtest_program_inputs import PreparedProgramBlock
    from .qmd_columnar_population import CertifiedPopulationWindow
    if (type(controller) is not ReplayRunController or type(block) is not PreparedProgramBlock
            or type(population) is not CertifiedPopulationWindow or type(ordinal) is not int
            or type(outside_rth) is not bool or type(terminal) is not bool):
        raise ValueError("Qualified dispatch requires its native prepared scope")
    if controller._program_dispatch_failure is not None:
        raise RuntimeError("Program dispatch requires recovery after an uncertain boundary") from controller._program_dispatch_failure
    if type(controller._journal_publisher) is not BacktestTypedJournalPublisher:
        raise ValueError("Qualified dispatch requires its owned typed publisher")
    publisher = controller._journal_publisher
    fence = (publisher.fenced_sequence, publisher._batch_id, publisher._source_cursor)
    def require_publisher():
        if (controller._journal_publisher is not publisher or publisher.journal is not controller._journal
                or publisher.writer.journal_profile != "backtest_v4" or publisher._error is not None
                or publisher._terminal_task is not None
                or publisher._task is not None and not publisher._task.done()
                or (publisher.fenced_sequence, publisher._batch_id, publisher._source_cursor) != fence):
            raise ValueError("Qualified dispatch lost its idle owned publisher")
    require_publisher()
    actor, runtime, journal = controller._program_strategy, controller._runtime, controller._journal
    listing = actor._listings[assignment_id]
    row, _ = block.row_for(listing, ordinal)
    at = row["boundary_at"]
    await reconcile_program_clock_order_observations(controller, assignment_id=assignment_id, at=at)
    if packet is None:
        from .program_policy_recovery import rebuild_owned_pending_program_policy
        if policy_blocks is None:
            raise ValueError("Automatic policy replay requires explicit prepared-block coverage")
        packet = await rebuild_owned_pending_program_policy(controller, policy_blocks, population,
            maximum_events=maximum_events, initial_packet=initial_packet)
    elif initial_packet is not None or policy_blocks is not None or maximum_events is not None:
        raise ValueError("Qualified dispatch cannot mix explicit and automatic policy bases")
    capture = await capture_program_clock_financial_basis(controller, at=at)
    seal = getattr(packet, "_continuation_seal", None)
    if (type(packet) is not RecoveredProgramPolicy or packet._proof is not _RECOVERY_PROOF
            or type(seal) is not _ProgramPolicyContinuationSeal or seal.token is not _RECOVERY_PROOF
            or (packet.run_id, packet.journal_sequence, packet.execution_count, packet.completion_count,
                tuple(sorted(packet.completed_source_sequences.items())), packet.source_commit_hashes, packet.state_hash)
               != (seal.run_id, seal.journal_sequence, seal.execution_count, seal.completion_count,
                   seal.completed, seal.source_hashes, seal.state_hash)
            or packet.run_id != controller.run_id or packet.journal_sequence != capture.journal_sequence
            or packet.state_hash != capture.policy_hash or program_policy_fingerprint(packet.actor) != packet.state_hash):
        raise ValueError("Qualified dispatch policy differs from its captured basis")
    prepared = await _owned_worker(prepare_program_clock_financial_basis, capture)
    cold = await _owned_worker(recover_prepared_portfolio_engine_state,
        run_id=controller.run_id, profiles=tuple(state.profile for state in runtime.portfolio.states.values()),
        run_account_ids=runtime.config.account_ids, prepared=prepared.portfolio, cutoff_at=at)
    candidate = build_recovered_program_runtime(replace(runtime.config, write_progress_checkpoints=False),
        actor, BacktestMemoryJournal(run_id=controller.run_id, initial_sequence=capture.journal_sequence),
        portfolio_recovery=cold, oms_image=capture.oms, broker_image=runtime.broker.checkpoint_state(),
        simulation_config=runtime.broker.config, completed_at=at, intent_planner=controller._planner,
        strategy_configuration=dict(controller.definition.configuration_revision["payload"].get("strategy") or {}),
        groups=tuple(runtime.portfolio.groups.values()), drawdown_measure_policy=runtime.portfolio.drawdown_measure_policy)
    dispatched = False
    try:
        await candidate.initialize(record_lifecycle=False)
        await _owned_worker(compare_captured_program_financial_runtime, candidate, cold, capture)
        from .program_clock_financial_basis import verify_owned_program_clock_financial_basis
        await _owned_worker(verify_owned_program_clock_financial_basis, controller, capture)
        require_publisher()
        if journal.latest_sequence(controller.run_id) != capture.journal_sequence:
            raise ValueError("Qualified dispatch journal changed before selection")
        dispatched = True
        result = await controller.dispatch_prepared_program_boundary(block, population,
            ordinal=ordinal, assignment_id=assignment_id, execution_policy=execution_policy,
            protection_profile=protection_profile, outside_rth=outside_rth, terminal=terminal)
        if "decision_basis" not in result:
            return result
        parent, children = result["decision_basis"]
        def intents():
            from src.trading_runtime.arte_journal_projection import project_journal_record
            from src.trading_runtime.arte_intent_projection import ProjectedIntent, RecoveredIntent, restore_strategy_intent
            wanted = {child["strategy_intent_id"] for child in children}
            recovered = {}
            for record in journal.unfenced_records(after_sequence=capture.journal_sequence,
                    through_sequence=parent["decision_sequence"]):
                if (record.category, record.entity_type) != ("strategy", "strategy_intent"):
                    continue
                batch = project_journal_record(record, run_month=publisher.run_month,
                    attempt_id=publisher.attempt_id, batch_id=parent["batch_id"],
                    prior_batch_id=publisher._batch_id, source_cursor=publisher._source_cursor,
                    expected_config=dict(strategy_id=actor.strategy_id, strategy_revision=actor.revision))
                excluded = {"record_id", "parent_record_id", "run_id", "event_month", "batch_id", "account_id", "content_hash"}
                core = {key: value for key, value in batch.intents[0].items() if key not in excluded}
                core["event_time"] = record.event_time.isoformat()
                slices = tuple({"intent_id": core["intent_id"], **{key: value for key, value in child.items()
                    if key not in excluded}} for child in batch.intent_slices)
                if core["intent_id"] in recovered:
                    raise ValueError("Qualified dispatch repeats a pending intent")
                recovered[core["intent_id"]] = RecoveredIntent(record.sequence, record.account_id,
                    record.record_id, batch.batch_id, restore_strategy_intent(ProjectedIntent(core, slices)))
            if set(recovered) != wanted:
                raise ValueError("Qualified dispatch pending intent coverage differs")
            return tuple(recovered[child["strategy_intent_id"]] for child in children)
        records = await _owned_worker(intents)
        envelope = await qualify_captured_pending_program_decision(controller, candidate, parent["record_id"],
            records, cold=cold, capture=capture, packet=packet, block=block, population=population,
            execution_policy=execution_policy, protection_profile=protection_profile, outside_rth=outside_rth)
        # Carry the completed suffix before the publisher can compact it. A
        # pre-decision packet would otherwise lag the next batch fence.
        from .program_policy_recovery import rebuild_owned_pending_program_policy
        next_packet = await rebuild_owned_pending_program_policy(controller, (block,), population,
            maximum_events=(maximum_events if maximum_events is not None else journal.latest_sequence(controller.run_id)),
            initial_packet=packet)
        return {**result, "qualification": envelope.qualification, "policy_packet": next_packet}
    except BaseException as exc:
        if dispatched:
            controller._program_dispatch_failure = exc
        raise
    finally:
        await candidate.finish()


def is_fresh_rejected_program_assignment(actor, assignment, candidate, observation):
    """Match sparse dispatch admission without changing policy or OMS state."""
    identity = assignment.assignment_id
    listing = actor._listings[identity]
    fresh_mask_rejected = (identity not in actor._selection_clocks
        and (assignment.account_id, actor.strategy_id, identity, listing) not in actor.execution_state.positions
        and not actor.position_for(identity).position_open
        and not (candidate.eligible and candidate.rules["entry"] and candidate.validity["entry"]))
    if not fresh_mask_rejected:
        return False
    # Dispatch preserves its lazy OMS read. Recovery already owns an exact
    # completed observation and passes that immutable value directly.
    observed = observation() if callable(observation) else observation
    return not (observed.working_orders or observed.unobserved_order_ids
        or observed.pending_acquisition or observed.pending_reduction)



async def dispatch_prepared_program_clock(controller, blocks, population, *, at,
        assignment_order, execution_policy, protection_profiles, outside_rth, terminal=False,
        qualify=False, policy_blocks=None, maximum_events=None, initial_packet=None):
    """Validate full coverage before dispatching the caller's explicit order.

    The native compute funnel supplies the order; this adapter invents no
    capital priority, broker clock, market data, fills or evaluation blocks.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_program_session import require_program_session_owner
    require_program_session_owner(controller)
    from .backtest_program_inputs import PreparedProgramBlock
    from .qmd_columnar_population import CertifiedPopulationWindow
    from .signal_stream_runtime_service import SignalStreamRuntime
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    if (type(controller) is not ReplayRunController or type(population) is not CertifiedPopulationWindow
            or not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None
            or type(outside_rth) is not bool or type(terminal) is not bool or type(qualify) is not bool
            or not isinstance(blocks, Mapping) or not isinstance(protection_profiles, Mapping)
            or isinstance(assignment_order, (str, bytes))):
        raise ValueError("Program clock needs its native typed scope")
    if qualify:
        if policy_blocks is None or type(maximum_events) is not int:
            raise ValueError("Qualified program clock requires explicit replay coverage and bounds")
        policy_blocks = tuple(policy_blocks)
    elif policy_blocks is not None or maximum_events is not None or initial_packet is not None:
        raise ValueError("Unqualified program clock cannot accept policy replay inputs")
    actor, runtime, source = controller._program_strategy, controller._runtime, controller._program_signal_runtime
    order = tuple(assignment_order)
    if (type(actor) is not AssignedProgramStrategy or runtime is None or runtime.strategy is not actor
            or type(source) is not SignalStreamRuntime or source._typed_scope is None
            or population.source_revision != source._typed_scope[1]
            or not order or len(order) != len(set(order))
            or set(order) != set(actor._by_assignment)
            or set(blocks) != set(order) or set(protection_profiles) != set(order)
            or runtime.last_event_time != at):
        raise ValueError("Program clock lacks complete assignment/source/broker coverage")
    boundary = source.typed_source_boundary(at)
    if boundary[0] < 1 or boundary[2] != at:
        raise ValueError("Program clock differs from its committed source")
    prepared = []
    for identity in order:
        block = blocks[identity]
        if type(block) is not PreparedProgramBlock or block.release_hash != actor.program_release.header["content_hash"]:
            raise ValueError("Program clock block differs from its immutable release")
        assignment = actor._by_assignment[identity]
        ordinal, candidate = block.boundary_at(actor.program_release, population,
            listing_id=actor._listings[identity], ticker=assignment.ticker, at=at)
        # Fresh rejected assignments own no prior programme state to advance.
        # Retain every previously visited assignment conservatively: it may
        # still own deferred capital, OMS or policy lifecycle work.
        fresh_rejected = is_fresh_rejected_program_assignment(actor, assignment, candidate,
            lambda: controller.program_order_observation(assignment_id=identity, at=at))
        prepared.append((identity, block, ordinal, protection_profiles[identity], fresh_rejected))
    results = []
    for identity, block, ordinal, protection, fresh_rejected in prepared:
        if (controller._program_strategy is not actor or controller._runtime is not runtime
                or controller._program_signal_runtime is not source or runtime.last_event_time != at
                or source.typed_source_boundary(at) != boundary):
            error = ValueError("Program clock ownership changed during dispatch")
            controller._program_dispatch_failure = error
            raise error
        if fresh_rejected:
            results.append((identity, {"status": "not_admitted", "reason": "fresh_entry_mask_rejected"}))
            continue
        if qualify:
            result = await dispatch_qualified_program_boundary(controller, block, population,
                ordinal=ordinal, assignment_id=identity, execution_policy=execution_policy,
                protection_profile=protection, outside_rth=outside_rth, terminal=terminal,
                policy_blocks=policy_blocks, maximum_events=maximum_events, initial_packet=initial_packet)
            initial_packet = result.get("policy_packet", initial_packet)
        else:
            result = await controller.dispatch_prepared_program_boundary(block, population,
                ordinal=ordinal, assignment_id=identity, execution_policy=execution_policy,
                protection_profile=protection, outside_rth=outside_rth, terminal=terminal)
        results.append((identity, result))
    if (controller._program_strategy is not actor or controller._runtime is not runtime
            or controller._program_signal_runtime is not source or runtime.last_event_time != at
            or source.typed_source_boundary(at) != boundary):
        error = ValueError("Program clock ownership changed during dispatch")
        controller._program_dispatch_failure = error
        raise error
    return tuple(results)


async def reconcile_program_clock_order_observations(controller, *, assignment_id, at):
    """Observe cross-assignment cancellation replies before the global capture.

    A cancel request remains pending until an actual broker observation confirms
    its terminal state. No matching or event-clock advancement occurs here.
    """
    manager = controller._runtime.order_manager
    if (manager.has_pending_cancellations
            or controller.program_order_observation(
                assignment_id=assignment_id, at=at).unobserved_order_ids):
        await manager.reconcile()
