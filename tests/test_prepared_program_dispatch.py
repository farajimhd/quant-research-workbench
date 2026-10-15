import asyncio
from copy import deepcopy
from threading import Event
from dataclasses import replace
from datetime import timedelta

import polars as pl
import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_program_inputs import prepare_program_block
from src.backend.qmd_columnar_population import CertifiedPopulationWindow
from src.backend.replay_run_service import ReplayRunController
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from src.market_engine import columnar_history_product as c
from src.market_engine.volume_rank_rules import select_volume_universe
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionEnvelope, ProtectionProfile, ProtectionSlice, StopRule
from test_columnar_history_publication import prepared_source, Store, publish, consume
from tests.test_backtest_program_inputs import release_for
from tests.test_qmd_columnar_history_transport import response_for
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_signal_source_runtime_bootstrap import source
from tests.test_signal_stream_typed_publication import FakeStorage
from tests.test_broker_liquidity_resolution import second_bar


@pytest.mark.parametrize("resolution", [100, 1000])
@pytest.mark.parametrize("failure", [None, "submission", "completion_record"])
@pytest.mark.parametrize("prefetch", [False, True])
@pytest.mark.parametrize("automatic", [False, True])
def test_prepared_qmd_scanner_source_boundary_reaches_actual_controller_broker(prepared_source, tmp_path, resolution, failure, prefetch, automatic, monkeypatch):
    storage = Store()
    publish(storage, prepared_source)
    certificate = consume(storage, prepared_source)
    pin = certificate.pin()
    release = release_for(pin)
    definition, original = definition_and_actor()
    configuration, scope, candidate = source()
    scope["session_key"] = pin.scope.session_date.isoformat()
    revision = deepcopy(definition.configuration_revision)
    revision["revision_id"] = scope["configuration_revision"]
    revision["payload"].update(configuration)
    definition = replace(definition, session_date=pin.scope.session_date, configuration_revision=revision)
    actor = AssignedProgramStrategy(release, original.assignments(), {"assignment": "listing"})
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=resolution)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    from tests.test_program_signal_source import executable_contextual_publisher, source_context_for
    publisher = executable_contextual_publisher(source_context_for(controller, scope['source_revision']), scope['catalogs'])
    def post(path, request, timeout):
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"], request["field_ids"])
    async def run():
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        try:
            block = await controller.prepare_program_inputs({"listing": pin}, first_ordinal=0, stop_ordinal=200,
                evaluation_block="optimizer-block", concurrency=2,
                as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), history_post=post)
            scanner = block.market.with_columns(pl.lit("ABC").alias("ticker"),
                (pl.col("boundary_at") - pl.duration(days=1)).alias("prior_close_available_at"),
                pl.lit(10.).alias("adjusted_prior_close"), pl.lit(30).alias("volume_window_seconds"))
            population = CertifiedPopulationWindow(scope["source_revision"], scanner,
                select_volume_universe(scanner, release.universe), 1, 1, 0, 0, 0)
            from src.backend.backtest_program_inputs import (
                prepare_program_scanner_projection, publish_prepared_program_source_clock)
            scanner_projection = prepare_program_scanner_projection(block, population, release)
            candidates = block.masks.with_columns(block.market["history.swing_low.2.1__valid"])
            ordinal = candidates.filter(pl.col("entry") & pl.col("history.swing_low.2.1__valid"))["observed_ordinal"][0]
            await controller.bootstrap_program_signals(publisher, **scope)
            policy = ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=2000))
            runtime = controller._program_signal_runtime
            first_source_at = block.row_for("listing", ordinal)[0]["boundary_at"]
            with pytest.raises(ValueError, match="listing/ticker identity"):
                await publish_prepared_program_source_clock(controller,
                    replace(scanner_projection, frame=scanner_projection.frame.with_columns(
                        pl.lit("WRONG").alias("ticker"))), publisher,
                    at=first_source_at, catalogs=scope["catalogs"])
            assert runtime.typed_source_cursor()[0] == 0
            async def publish_source_row(row):
                source_at = row["boundary_at"]
                await publish_prepared_program_source_clock(controller, scanner_projection, publisher,
                    at=source_at, catalogs=scope["catalogs"])
            if prefetch:
                for source_offset in range(3):
                    source_row, _ = block.row_for("listing", ordinal + source_offset)
                    await publish_source_row(source_row)
            previous_basis_packet = None
            previous_dispatch_packet = None
            for offset in range(3):
                row, _ = block.row_for("listing", ordinal + offset)
                at = row["boundary_at"]
                controller.current_time = at
                price = round(row["reference_close"] * 10000)
                liquidity = second_bar(at)
                liquidity.update(ticker="ABC", ask_int=price, bid_int=price - 1,
                    close_int=price, high_int=price + 1, low_int=price - 1)
                if resolution == 100:
                    liquidity.update(resolution_ms=100, bucket_index=(liquidity["bucket_index"] + 1) * 10 - 1)
                await controller._runtime.process_liquidity_boundary((liquidity,), at=at)
                if not prefetch:
                    await publish_source_row(row)
                source_prefix_hashes = runtime.typed_source_commit_hashes()[:offset + 1]
                assert runtime.typed_source_boundary(at)[0] == offset + 1
                if prefetch:
                    assert runtime.typed_source_cursor()[0] == 3
                stop = row["history.swing_low.2.1"]
                profile = ProtectionProfile("program-stop", 1, (ProtectionSlice("one", 1., StopRule(price=stop)),))
                if offset == 0 and failure:
                    async def failed_submit(*args, **kwargs):
                        raise RuntimeError("injected submission uncertainty")
                    original_append = controller._journal.append
                    def failed_append(**kwargs):
                        if kwargs["payload"].get("signal_type") == "program_boundary_completed":
                            raise RuntimeError("injected completion uncertainty")
                        return original_append(**kwargs)
                    if failure == "submission":
                        controller._runtime.submit_program_proposal = failed_submit
                    else:
                        controller._journal.append = failed_append
                    with pytest.raises(RuntimeError, match="injected .* uncertainty"):
                        await controller.dispatch_prepared_program_boundary(block, population,
                            ordinal=ordinal, assignment_id="assignment", execution_policy=policy,
                            protection_profile=profile, outside_rth=False)
                    orders_before_retry = await controller._runtime.broker.live_orders()
                    assert bool(orders_before_retry) == (failure == "completion_record")
                    with pytest.raises(RuntimeError, match="requires recovery"):
                        await controller.dispatch_prepared_program_boundary(block, population,
                            ordinal=ordinal, assignment_id="assignment", execution_policy=policy,
                            protection_profile=profile, outside_rth=False)
                    assert await controller._runtime.broker.live_orders() == orders_before_retry
                    assert not [record for record in controller._journal.records(controller.run_id)
                        if record.payload.get("signal_type") == "program_boundary_completed"]
                    return
                basis_sequence = controller._journal.latest_sequence(controller.run_id)
                from src.backend.program_clock_financial_basis import (
                    capture_program_clock_financial_basis, prepare_program_clock_financial_basis,
                    verify_owned_program_clock_financial_basis)
                captured = await capture_program_clock_financial_basis(controller, at=at)
                assert verify_owned_program_clock_financial_basis(controller, captured).capture is captured
                with pytest.raises(ValueError, match="controller custody"):
                    verify_owned_program_clock_financial_basis(controller, replace(captured, policy_hash="0" * 64))
                with pytest.raises(ValueError, match="controller custody"):
                    verify_owned_program_clock_financial_basis(controller, replace(captured, _evidence=None))
                assert captured.journal_sequence == basis_sequence
                assert captured.source_boundary == runtime.typed_source_boundary(at)
                assert captured.broker.snapshot["checkpoint_sequence"] == basis_sequence
                from src.trading_runtime.arte_oms_projection import freeze_oms_group
                manager = controller._runtime.order_manager
                assert captured.oms.group_by_client_id == manager._group_by_client_id
                assert captured.oms.group_by_broker_id == manager._group_by_broker_id
                assert set(captured.oms.groups) == set(manager._groups)
                for identity, group in captured.oms.groups.items():
                    assert group is not manager._groups[identity]
                    assert group.reprice_task is None and group.protection_task is None
                    assert freeze_oms_group(group) == freeze_oms_group(manager._groups[identity])
                    if group.orders:
                        original_order = group.orders[0]
                        group.orders[0] = replace(original_order, quantity=original_order.quantity + 1)
                        assert manager._groups[identity].orders[0].quantity == original_order.quantity
                        group.orders[0] = original_order
                assert {snapshot.account_id for snapshot in captured.portfolio} == set(controller._runtime.config.account_ids)
                with pytest.raises(TypeError):
                    captured.broker.snapshot["checkpoint_sequence"] = 0
                prepared_finances = prepare_program_clock_financial_basis(captured)
                with pytest.raises(ValueError, match="OMS scope"):
                    prepare_program_clock_financial_basis(replace(captured,
                        oms=replace(captured.oms, run_id="foreign-run")))
                assert prepared_finances.broker == captured.broker
                assert all(snapshot.state_revision == basis_sequence for snapshot in prepared_finances.portfolio)
                from src.trading_runtime.arte_portfolio_snapshot import (
                    decode_prepared_portfolio_snapshot, prepare_portfolio_snapshot)
                for snapshot in prepared_finances.portfolio:
                    decoded = decode_prepared_portfolio_snapshot(snapshot)
                    assert prepare_portfolio_snapshot(run_id=snapshot.run_id, account_id=snapshot.account_id,
                        state_revision=snapshot.state_revision, snapshot_at=at, state=decoded) == snapshot
                    from types import MappingProxyType
                    invalid = replace(snapshot, rows=replace(snapshot.rows, account=MappingProxyType({
                        **snapshot.rows.account, "control_mode": "invalid-control-mode"})))
                    with pytest.raises(ValueError):
                        decode_prepared_portfolio_snapshot(invalid)
                from src.trading_runtime import arte_portfolio_recovery as portfolio_recovery
                from src.trading_runtime.portfolio import PortfolioSyncState
                profiles = tuple(state.profile for state in controller._runtime.portfolio.states.values())
                with monkeypatch.context() as no_database:
                    def forbidden_read(*args, **kwargs):
                        raise AssertionError("Staged Portfolio reconstruction opened a database reader")
                    no_database.setattr(portfolio_recovery, "load_typed_run_context", forbidden_read)
                    no_database.setattr(portfolio_recovery, "load_portfolio_snapshot", forbidden_read)
                    cold = portfolio_recovery.recover_prepared_portfolio_engine_state(
                        run_id=controller.run_id, profiles=profiles, run_account_ids=controller._runtime.config.account_ids,
                        prepared=prepared_finances.portfolio, cutoff_at=at)
                assert all(state.sync_state is PortfolioSyncState.ENTRIES_BLOCKED for state in cold.states.values())
                assert all(state.summary is None and state.ledger is None and not state.positions for state in cold.states.values())
                assert set(cold.reservations) == set(controller._runtime.portfolio.reservations)
                assert set(cold.allocations) == set(controller._runtime.portfolio.allocations)
                from src.backend.program_runtime_recovery import build_recovered_program_runtime
                from src.backend.program_financial_recovery import compare_captured_program_financial_runtime
                candidate_journal = BacktestMemoryJournal(run_id=controller.run_id, initial_sequence=basis_sequence)
                captured_broker_image = controller._runtime.broker.checkpoint_state()
                financial_candidate = build_recovered_program_runtime(
                    replace(controller._runtime.config, write_progress_checkpoints=False), actor, candidate_journal,
                    portfolio_recovery=cold, oms_image=captured.oms,
                    broker_image=captured_broker_image,
                    simulation_config=controller._runtime.broker.config, completed_at=at,
                    intent_planner=controller._planner,
                    strategy_configuration=dict(controller.definition.configuration_revision["payload"].get("strategy") or {}),
                    groups=tuple(controller._runtime.portfolio.groups.values()),
                    drawdown_measure_policy=controller._runtime.portfolio.drawdown_measure_policy)
                try:
                    await financial_candidate.initialize(record_lifecycle=False)
                    compared = compare_captured_program_financial_runtime(financial_candidate, cold, captured)
                    assert compared.run_id == controller.run_id
                    assert all(value == basis_sequence for value in compared.revisions.values())
                    assert financial_candidate is not controller._runtime
                    assert financial_candidate.broker is not controller._runtime.broker
                    assert financial_candidate.portfolio is not controller._runtime.portfolio
                    account = financial_candidate.config.account_ids[0]
                    original_cash = financial_candidate.broker._cash[account]
                    financial_candidate.broker._cash[account] += 1.
                    try:
                        with pytest.raises(ValueError, match="broker image changed"):
                            compare_captured_program_financial_runtime(financial_candidate, cold, captured)
                    finally:
                        financial_candidate.broker._cash[account] = original_cash
                finally:
                    await financial_candidate.finish()
                assert controller._journal.latest_sequence(controller.run_id) == basis_sequence
                with pytest.raises(ValueError, match="exact normalized account coverage"):
                    portfolio_recovery.recover_prepared_portfolio_engine_state(
                        run_id=controller.run_id, profiles=profiles, run_account_ids=controller._runtime.config.account_ids,
                        prepared=prepared_finances.portfolio * 2, cutoff_at=at)
                with pytest.raises(ValueError, match="beyond its cutoff"):
                    portfolio_recovery.recover_prepared_portfolio_engine_state(
                        run_id=controller.run_id, profiles=profiles, run_account_ids=controller._runtime.config.account_ids,
                        prepared=prepared_finances.portfolio, cutoff_at=at - timedelta(seconds=1))
                with pytest.raises(ValueError, match="Portfolio coverage"):
                    prepare_program_clock_financial_basis(replace(captured, portfolio=()))
                if offset == 0:
                    from src.backend import qmd_columnar_history_transport as transport
                    original_worker = transport._owned_worker
                    async def moving_worker(*args, **kwargs):
                        value = await original_worker(*args, **kwargs)
                        controller._runtime.last_event_time = at + timedelta(seconds=1)
                        return value
                    try:
                        with monkeypatch.context() as changed_scope:
                            changed_scope.setattr(transport, "_owned_worker", moving_worker)
                            with pytest.raises(ValueError, match="changed during projection"):
                                await capture_program_clock_financial_basis(controller, at=at)
                    finally:
                        controller._runtime.last_event_time = at
                historical_actor = AssignedProgramStrategy(actor.program_release, actor.assignments(), actor._listings)
                historical_actor.execution_state = deepcopy(actor.execution_state)
                historical_actor.target_state = deepcopy(actor.target_state)
                historical_actor.trail_state = deepcopy(actor.trail_state)
                historical_actor._selection_clocks = dict(actor._selection_clocks)
                captured_policy_state = (*deepcopy((vars(actor.execution_state), vars(actor.target_state),
                    vars(actor.trail_state))), dict(actor._selection_clocks))
                from src.backend.program_policy_recovery import _program_policy_fingerprint_value
                from src.trading_runtime.journal_contract import canonical_json
                captured_policy_json = canonical_json(_program_policy_fingerprint_value(actor))
                if automatic:
                    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
                    from types import SimpleNamespace
                    if controller._journal_publisher is None:
                        controller._journal_publisher = BacktestTypedJournalPublisher(controller._journal,
                            SimpleNamespace(run_id=controller.run_id, run_mode="backtest", coalesce_batches=False,
                                journal_profile="backtest_v4", max_events_per_commit=512),
                            attempt_id="00000000-0000-0000-0000-000000000001", run_month=at.date().replace(day=1))
                    from src.backend.program_policy_recovery import project_pending_program_policy_events, rebuild_program_policy_events
                    prior_executions = await controller._runtime.broker.trades()
                    pre_events = project_pending_program_policy_events(controller._journal.records(controller.run_id),
                        executions=prior_executions, run_month=at.date().replace(day=1),
                        attempt_id=controller._journal_publisher.attempt_id,
                        batch_id="00000000-0000-0000-0000-000000000002",
                        prior_batch_id=controller._journal_publisher._batch_id, source_cursor="synthetic")
                    pre_packet = rebuild_program_policy_events(actor, pre_events, run_id=controller.run_id,
                        source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                        executions=prior_executions, order_manager=controller._runtime.order_manager,
                        maximum_events=1000, last_sequence=basis_sequence)
                historical_observation = controller.program_order_observation(assignment_id="assignment", at=at)
                original_records = controller._journal.records
                def forbidden_rescan(*args, **kwargs):
                    if automatic:
                        assert kwargs.get("after_sequence") in {
                            0 if previous_dispatch_packet is None else previous_dispatch_packet.journal_sequence,
                            basis_sequence}
                        return original_records(*args, **kwargs)
                    raise AssertionError("Completed dispatch must not rescan journal records")
                controller._journal.records = forbidden_rescan
                try:
                    with pytest.raises(ValueError, match="complete assignment/source/broker coverage"):
                        await controller.dispatch_prepared_program_clock({}, population, at=at,
                            assignment_order=("assignment",), execution_policy=policy,
                            protection_profiles={"assignment": profile}, outside_rth=False)
                    if automatic:
                        from src.backend.backtest_program_clock import dispatch_qualified_program_boundary
                        with pytest.raises(ValueError, match="captured basis"):
                            await dispatch_qualified_program_boundary(controller, block, population,
                                packet=replace(pre_packet, completion_count=pre_packet.completion_count + 1),
                                ordinal=ordinal + offset, assignment_id="assignment", execution_policy=policy,
                                protection_profile=profile, outside_rth=False, terminal=offset == 2)
                        assert controller._journal.latest_sequence(controller.run_id) == basis_sequence
                        assert controller._program_dispatch_failure is None
                        dispatched = await controller.dispatch_prepared_program_clock({"assignment": block}, population,
                            at=at, assignment_order=("assignment",), execution_policy=policy,
                            protection_profiles={"assignment": profile}, outside_rth=False, terminal=offset == 2,
                            qualify=True, policy_blocks=(block,), maximum_events=1000,
                            initial_packet=previous_dispatch_packet)
                        auto_result = dispatched[0][1]
                        assert auto_result["qualification"].selection.status == auto_result["status"]
                        from src.backend.program_policy_recovery import program_policy_fingerprint
                        assert auto_result["policy_packet"].state_hash == program_policy_fingerprint(actor)
                        assert auto_result["policy_packet"].journal_sequence == controller._journal.latest_sequence(controller.run_id)
                        assert auto_result["policy_packet"].journal_sequence > basis_sequence
                        # Synthetic compacted-prefix handoff. This is an in-memory
                        # fixture, not a claim of an operational storage receipt.
                        from src.backend.program_policy_recovery import rebuild_owned_pending_program_policy
                        post_packet = auto_result["policy_packet"]
                        previous_dispatch_packet = post_packet
                        compacted = BacktestMemoryJournal(run_id=controller.run_id,
                            initial_sequence=post_packet.journal_sequence)
                        compacted_publisher = BacktestTypedJournalPublisher(compacted,
                            controller._journal_publisher.writer,
                            attempt_id=controller._journal_publisher.attempt_id,
                            run_month=at.date().replace(day=1), initial_sequence=post_packet.journal_sequence,
                            prior_batch_id="00000000-0000-0000-0000-000000000002")
                        with monkeypatch.context() as compacted_scope:
                            compacted_scope.setattr(controller, "_journal", compacted)
                            compacted_scope.setattr(controller._runtime, "journal", compacted)
                            compacted_scope.setattr(controller, "_journal_publisher", compacted_publisher)
                            resumed_packet = await rebuild_owned_pending_program_policy(controller, (block,), population,
                                maximum_events=1000, initial_packet=post_packet)
                            assert resumed_packet.state_hash == post_packet.state_hash
                            assert resumed_packet.journal_sequence == post_packet.journal_sequence
                            with pytest.raises(ValueError, match="read from ClickHouse"):
                                await rebuild_owned_pending_program_policy(controller, (block,), population,
                                    maximum_events=1000, initial_packet=pre_packet)
                    else:
                        dispatched = await controller.dispatch_prepared_program_clock({"assignment": block}, population,
                            at=at, assignment_order=("assignment",), execution_policy=policy,
                            protection_profiles={"assignment": profile}, outside_rth=False, terminal=offset == 2)
                    assert len(dispatched) == 1 and dispatched[0][0] == "assignment"
                    result = dispatched[0][1]
                finally:
                    controller._journal.records = original_records
                from src.trading_runtime.rule_decision_basis_projection import project_program_completion_basis
                basis_records = controller._journal.records(controller.run_id)
                completion = next(record for record in reversed(basis_records)
                    if record.payload.get("signal_type") == "program_boundary_completed")
                decision_intents = []
                for record in basis_records:
                    if record.sequence > basis_sequence and (record.category, record.entity_type) == ("strategy", "strategy_intent"):
                        reason_stage = record.payload["reason"].split(":", 1)[1]
                        stage_name = {"target": "reduce", "terminal": "exit",
                            "cancel_pending_acquisition": "exit"}.get(reason_stage, reason_stage)
                        decision_intents.append(dict(stage=stage_name, strategy_intent_id=record.entity_id))
                basis_parent, basis_children = project_program_completion_basis(completion,
                    release=actor.program_release, block=block, assignment=actor.assignments()[0],
                    listing_id="listing", ordinal=ordinal + offset,
                    source_boundary=runtime.typed_source_boundary(at), basis_sequence=basis_sequence,
                    intents=decision_intents, terminal=offset == 2,
                    batch_id="00000000-0000-0000-0000-000000000002")
                assert basis_parent["source_commit_hash"] == source_prefix_hashes[-1]
                assert basis_parent["intent_count"] == len(basis_children) == len(decision_intents)
                dispatched_parent, dispatched_children = result["decision_basis"]
                assert controller._journal.rule_decision_basis_for_record(completion.record_id) is result["decision_basis"]
                with pytest.raises(ValueError, match="pending journal record"):
                    controller._journal.retain_rule_decision_basis(completion, dispatched_parent, dispatched_children)
                assert dispatched_parent["record_id"] == completion.record_id
                assert dispatched_parent["basis_sequence"] == basis_sequence
                assert dispatched_parent["source_commit_hash"] == basis_parent["source_commit_hash"]
                from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_prepared_source, project_rule_decision_basis
                source_scope = dict(release=actor.program_release, block=block,
                    assignment=actor.assignments()[0], source=runtime)
                assert verify_rule_decision_prepared_source(dispatched_parent, dispatched_children,
                    **source_scope) == (dispatched_parent, dispatched_children)
                substituted_values = {key: value for key, value in dispatched_parent.items()
                    if key not in {"content_hash", "intent_count", "intent_hash"}}
                for changed in (dict(history_input_hash="e" * 64), dict(evaluation_block="other")):
                    substituted_parent, substituted_children = project_rule_decision_basis(
                        {**substituted_values, **changed}, decision_intents)
                    with pytest.raises(ValueError, match="prepared committed source"):
                        verify_rule_decision_prepared_source(substituted_parent, substituted_children, **source_scope)
                assert [child["strategy_intent_id"] for child in dispatched_children] == [
                    child["strategy_intent_id"] for child in basis_children]
                with pytest.raises(TypeError):
                    dispatched_parent["source_commit_hash"] = "e" * 64
                from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_completion_record
                assert verify_rule_decision_completion_record(basis_parent, basis_children, completion) is completion
                from src.trading_runtime.arte_journal_projection import project_journal_record
                from src.trading_runtime.rule_decision_basis_projection import RuleDecisionBasisBatch
                typed_completion = project_journal_record(completion, run_month=at.date().replace(day=1),
                    attempt_id="00000000-0000-0000-0000-000000000001",
                    batch_id=basis_parent["batch_id"], prior_batch_id="00000000-0000-0000-0000-000000000000",
                    source_cursor="prepared-source")
                transport = RuleDecisionBasisBatch(typed_completion, basis_parent, basis_children)
                retained_transport = RuleDecisionBasisBatch.from_pending_completion(
                    controller._journal, completion, typed_completion)
                assert retained_transport.basis == transport.basis
                assert retained_transport.intents == transport.intents
                assert dispatched_parent["batch_id"] == completion.record_id
                assert retained_transport.basis["batch_id"] == typed_completion.batch_id
                before_rejected_qualification = controller._journal.latest_sequence(controller.run_id)
                with pytest.raises(ValueError, match="controller ownership"):
                    await controller.qualify_program_decision_candidate(controller._runtime,
                        dispatched_parent, dispatched_children, (), cold=None, prefix=None, broker=None,
                        oms_image=None, packet=None, block=block, population=population,
                        execution_policy=policy, protection_profile=profile, outside_rth=False)
                assert controller._journal.latest_sequence(controller.run_id) == before_rejected_qualification
                assert controller._runtime.strategy is actor
                from copy import copy
                with pytest.raises(ValueError, match="controller ownership"):
                    await controller.qualify_program_decision_candidate(copy(controller._runtime),
                        dispatched_parent, dispatched_children, (), cold=None, prefix=None, broker=None,
                        oms_image=None, packet=None, block=block, population=population,
                        execution_policy=policy, protection_profile=profile, outside_rth=False)
                from src.backend.rule_decision_reselection import reselect_rule_decision
                from src.trading_runtime.arte_intent_projection import ProjectedIntent, RecoveredIntent, restore_strategy_intent
                recovered_intents = []
                for intent_record in basis_records:
                    if not basis_sequence < intent_record.sequence < completion.sequence or (
                            intent_record.category, intent_record.entity_type) != ("strategy", "strategy_intent"):
                        continue
                    intent_batch = project_journal_record(intent_record, run_month=at.date().replace(day=1),
                        attempt_id=typed_completion.attempt_id, batch_id=typed_completion.batch_id,
                        prior_batch_id=typed_completion.prior_batch_id, source_cursor="source",
                        expected_config=dict(strategy_id=actor.strategy_id, strategy_revision=actor.revision))
                    core = {key: value for key, value in intent_batch.intents[0].items()
                        if key not in {"record_id", "run_id", "event_month", "batch_id", "account_id", "content_hash"}}
                    core["event_time"] = intent_record.event_time.isoformat()
                    slices = tuple({"intent_id": core["intent_id"], **{key: value for key, value in child.items()
                        if key not in {"record_id", "parent_record_id", "run_id", "event_month", "batch_id", "account_id", "content_hash"}}}
                        for child in intent_batch.intent_slices)
                    recovered_intents.append(RecoveredIntent(intent_record.sequence, intent_record.account_id,
                        intent_record.record_id, intent_batch.batch_id, restore_strategy_intent(ProjectedIntent(core, slices))))
                reselection_scope = dict(basis_sequence=basis_sequence, block=block, population=population,
                    source=runtime, observation=historical_observation, execution_policy=policy,
                    protection_profile=profile, outside_rth=False)
                with pytest.raises(ValueError, match="historical policy basis"):
                    reselect_rule_decision(dispatched_parent, dispatched_children, recovered_intents,
                        actor=historical_actor, **{**reselection_scope, "basis_sequence": basis_sequence + 1})
                if recovered_intents:
                    corrupt_actor = AssignedProgramStrategy(actor.program_release, actor.assignments(), actor._listings)
                    corrupt_actor.execution_state = deepcopy(historical_actor.execution_state)
                    corrupt_actor.target_state = deepcopy(historical_actor.target_state)
                    corrupt_actor.trail_state = deepcopy(historical_actor.trail_state)
                    corrupt_actor._selection_clocks = dict(historical_actor._selection_clocks)
                    corrupt_records = [replace(recovered_intents[0],
                        intent=replace(recovered_intents[0].intent, quantity=recovered_intents[0].intent.quantity + 1)),
                        *recovered_intents[1:]]
                    with pytest.raises(ValueError, match="semantic intent"):
                        reselect_rule_decision(dispatched_parent, dispatched_children, corrupt_records,
                            actor=corrupt_actor, **reselection_scope)
                selected_again = reselect_rule_decision(dispatched_parent, dispatched_children, recovered_intents,
                    actor=historical_actor, **reselection_scope)
                assert selected_again.status == result["status"]
                from src.trading_runtime.rule_decision_publication import prepare_rule_decision_commit
                from src.trading_runtime.arte_journal_commit_v4 import verify_commit_v4
                prepared_commit = prepare_rule_decision_commit(retained_transport, committed_at=at)
                from src.trading_runtime.rule_decision_publication import prepare_rule_decision_insert_requests
                requests = prepare_rule_decision_insert_requests(prepared_commit)
                assert requests == prepare_rule_decision_insert_requests(prepared_commit)
                assert len(requests) == 1 + bool(dispatched_children)
                assert all(request.batch_id == typed_completion.batch_id and request.run_id == completion.run_id
                    and request.last_sequence == completion.sequence
                    and b"FORMAT RowBinary\n" in request.sql for request in requests)
                from src.trading_runtime.rule_decision_publication import verify_rule_decision_commit_readback
                from src.trading_runtime.arte_journal_writer import _CONTRACTS, _datetime_wire
                from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES
                contracts = {**_CONTRACTS, **{table.name: table for table in STAGED_TABLES}}
                readback = []
                for name, rows in prepared_commit.families:
                    contract = contracts["trading_strategy_signal_v1" if name == "trading_strategy_signal_v2" else name]
                    stored_rows = []
                    for row in rows:
                        stored = dict(row)
                        for column, kind in contract.columns:
                            if kind.startswith("DateTime64(") and stored[column] is not None:
                                stored[column] = _datetime_wire(stored[column], 9 if kind.startswith("DateTime64(9") else 6)
                        stored_rows.append(stored)
                    readback.append((name, tuple(stored_rows)))
                verify_rule_decision_commit_readback(prepared_commit, readback)
                corrupt = [(name, tuple({**row, "history_input_hash": "e" * 64} for row in rows)
                    if name == "trading_rule_decision_basis_v1" else rows) for name, rows in readback]
                with pytest.raises(ValueError, match="scalar hash"):
                    verify_rule_decision_commit_readback(prepared_commit, corrupt)
                names = {name for name, _ in prepared_commit.families}
                assert {"trading_event_v1", "trading_strategy_signal_v2",
                    "trading_rule_decision_basis_v1"} <= names
                assert ("trading_rule_decision_intent_v1" in names) == bool(dispatched_children)
                with pytest.raises(ValueError, match="lacks complete normalized families"):
                    verify_commit_v4(prepared_commit.commit, prepared_commit.family_seals,
                        detail_identities={name: tuple((row["record_id"], row["content_hash"]) for row in rows)
                            for name, rows in prepared_commit.families if name != "trading_rule_decision_basis_v1"})
                assert transport.base is typed_completion
                with pytest.raises(TypeError):
                    transport.basis["source_commit_hash"] = "e" * 64
                with pytest.raises(ValueError, match="typed batch"):
                    RuleDecisionBasisBatch(replace(typed_completion, run_id="other"), basis_parent, basis_children)
                with pytest.raises(ValueError, match="event envelope"):
                    RuleDecisionBasisBatch(replace(typed_completion, events=({**typed_completion.events[0],
                        "causation_id": "e" * 64},)), basis_parent, basis_children)
                for changed_signals in ((), ({**typed_completion.signals[0], "signal_type": "other"},),
                        ({**typed_completion.signals[0], "decision_assignment_id": "other"},),
                        ({**typed_completion.signals[0], "decision_reason_detail": "other"},)):
                    with pytest.raises(ValueError, match="completion detail"):
                        RuleDecisionBasisBatch(replace(typed_completion, signals=changed_signals),
                            basis_parent, basis_children)
                for changed in (replace(completion, run_id="foreign-run"),
                        replace(completion, sequence=completion.sequence + 1),
                        replace(completion, payload={**completion.payload, "causation_id": "e" * 64})):
                    with pytest.raises(ValueError, match="journal authority"):
                        verify_rule_decision_completion_record(basis_parent, basis_children, changed)
                with pytest.raises(ValueError, match="prepared source"):
                    project_program_completion_basis(completion,
                        release=actor.program_release, block=block, assignment=actor.assignments()[0],
                        listing_id="listing", ordinal=ordinal + offset,
                        source_boundary=(offset + 1, "e" * 64, at), basis_sequence=basis_sequence,
                        intents=decision_intents, terminal=offset == 2,
                        batch_id="00000000-0000-0000-0000-000000000002")
                if offset == 0:
                    assert result["status"] == "selected" and not actor.position_for("assignment").position_open
                    repeated = await controller.dispatch_prepared_program_boundary(block, population,
                        ordinal=ordinal, assignment_id="assignment", execution_policy=policy,
                        protection_profile=profile, outside_rth=False)
                    assert repeated == {"status": "already_consumed", "results": []}
                elif offset == 1:
                    assert actor.position_for("assignment").position_open and result["status"] == "selected"
                else:
                    position = actor.position_for("assignment")
                    assert not position.position_open and position.closed_positions == 1 and position.fees > 0
                from src.trading_runtime.arte_journal_projection import project_journal_record
                from src.trading_runtime.arte_journal_reader import TypedJournalEvent
                from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
                from src.backend.program_policy_recovery import rebuild_program_policy_events, program_policy_fingerprint
                await controller._runtime.order_manager.reconcile()
                await controller._runtime._refresh_portfolio_from_broker()
                from unittest.mock import patch
                executions = await controller._runtime.broker.trades()
                raw_keys = ("strategy_id", "canonical_strategy_revision", "canonical_run_id", "canonical_metadata")
                lineages = {execution.order_ref: ({key: execution.raw.get(key) for key in raw_keys},
                    execution.account, execution.symbol, execution.conid) for execution in executions}
                events = []
                for record in controller._journal.records(controller.run_id):
                    detail, family = None, None
                    event = dict(run_id=record.run_id, sequence=record.sequence,
                        category=record.category, entity_type=record.entity_type, entity_id=record.entity_id,
                        event_time=record.event_time, account_id=record.account_id,
                        correlation_id=record.payload.get("correlation_id", ""),
                        causation_id=record.payload.get("causation_id", ""))
                    if record.category == "execution" or record.payload.get("signal_type") == "program_boundary_completed":
                        batch = project_journal_record(record, run_month=at.date().replace(day=1),
                            attempt_id="00000000-0000-0000-0000-000000000001",
                            batch_id="00000000-0000-0000-0000-000000000002",
                            prior_batch_id="00000000-0000-0000-0000-000000000000", source_cursor="synthetic",
                            committed_order_lineage=lineages)
                        event = dict(batch.events[0])
                        if record.entity_type == "fill":
                            family, detail = "trading_execution_v1", dict(batch.executions[0])
                        elif record.entity_type == "commission":
                            family, detail = "trading_commission_v1", dict(batch.commissions[0])
                        else:
                            family, detail = "trading_strategy_signal_v2", dict(batch.signals[0])
                    events.append(TypedJournalEvent(event, family, detail))
                from src.backend.rule_decision_reselection import reselect_recovered_rule_decision
                basis_packet = rebuild_program_policy_events(actor, events[:basis_sequence],
                    run_id=controller.run_id, source_hashes=source_prefix_hashes, blocks=(block,),
                    population=population, executions=executions,
                    order_manager=controller._runtime.order_manager, maximum_events=1000,
                    last_sequence=basis_sequence)
                assert captured.journal_sequence == basis_packet.journal_sequence
                assert captured_policy_state == (vars(basis_packet.actor.execution_state),
                    vars(basis_packet.actor.target_state), vars(basis_packet.actor.trail_state),
                    basis_packet.actor._selection_clocks)
                assert captured_policy_json == canonical_json(_program_policy_fingerprint_value(basis_packet.actor))
                assert captured.policy_hash == basis_packet.state_hash
                from src.backend.rule_decision_reselection import qualify_captured_rule_decision_runtime
                qualifier_candidate = build_recovered_program_runtime(
                    replace(controller._runtime.config, write_progress_checkpoints=False), actor,
                    BacktestMemoryJournal(run_id=controller.run_id, initial_sequence=basis_sequence),
                    portfolio_recovery=cold, oms_image=captured.oms, broker_image=captured_broker_image,
                    simulation_config=controller._runtime.broker.config, completed_at=at,
                    intent_planner=controller._planner,
                    strategy_configuration=dict(controller.definition.configuration_revision["payload"].get("strategy") or {}),
                    groups=tuple(controller._runtime.portfolio.groups.values()),
                    drawdown_measure_policy=controller._runtime.portfolio.drawdown_measure_policy)
                qualification_scope = {key: value for key, value in reselection_scope.items()
                    if key not in {"basis_sequence", "observation"}}
                try:
                    await qualifier_candidate.initialize(record_lifecycle=False)
                    qualification = await qualify_captured_rule_decision_runtime(
                        dispatched_parent, dispatched_children, recovered_intents, controller=controller,
                        runtime=qualifier_candidate, cold=cold, capture=captured, packet=basis_packet,
                        **qualification_scope)
                    assert qualification.selection == selected_again
                    assert qualification.basis_sequence == basis_sequence
                    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
                    from src.backend.backtest_program_publication import (
                        qualify_captured_pending_program_decision, program_decision_projection_arguments)
                    from types import SimpleNamespace
                    if controller._journal_publisher is None:
                        synthetic_writer = SimpleNamespace(run_id=controller.run_id, run_mode="backtest",
                            coalesce_batches=False, journal_profile="backtest_v4", max_events_per_commit=512)
                        controller._journal_publisher = BacktestTypedJournalPublisher(controller._journal,
                            synthetic_writer, attempt_id="00000000-0000-0000-0000-000000000001",
                            run_month=at.date().replace(day=1))
                    if offset == 0 and not automatic:
                        from src.backend import qmd_columnar_history_transport as transport
                        original_worker = transport._owned_worker
                        owned_publisher = controller._journal_publisher
                        original_cursor = owned_publisher._source_cursor
                        async def moving_publisher_worker(*args, **kwargs):
                            result = await original_worker(*args, **kwargs)
                            owned_publisher._source_cursor = "changed-during-qualification"
                            return result
                        try:
                            with monkeypatch.context() as changed_publisher:
                                changed_publisher.setattr(transport, "_owned_worker", moving_publisher_worker)
                                with pytest.raises(ValueError, match="idle owned publisher"):
                                    await qualify_captured_pending_program_decision(controller, qualifier_candidate,
                                        completion.record_id, recovered_intents, cold=cold, capture=captured,
                                        packet=basis_packet, block=block, population=population,
                                        execution_policy=policy, protection_profile=profile, outside_rth=False)
                            assert program_decision_projection_arguments(owned_publisher, completion.sequence) == {}
                        finally:
                            owned_publisher._source_cursor = original_cursor
                    if not automatic:
                        envelope = await qualify_captured_pending_program_decision(controller, qualifier_candidate,
                            completion.record_id, recovered_intents, cold=cold, capture=captured, packet=basis_packet,
                            block=block, population=population, execution_policy=policy,
                            protection_profile=profile, outside_rth=False)
                        retained_qualification = envelope.qualification
                    else:
                        retained_qualification = auto_result["qualification"]
                    assert retained_qualification.selection == selected_again
                    projected = program_decision_projection_arguments(controller._journal_publisher,
                        completion.sequence)["rule_decision_qualifications"]
                    assert projected[completion.record_id] is retained_qualification
                    assert controller._journal_publisher.fenced_sequence == 0
                    with pytest.raises(ValueError, match="cannot be replaced"):
                        await qualify_captured_pending_program_decision(controller, qualifier_candidate,
                            completion.record_id, recovered_intents, cold=cold, capture=captured, packet=basis_packet,
                            block=block, population=population, execution_policy=policy,
                            protection_profile=profile, outside_rth=False)
                    with pytest.raises(ValueError, match="owned native basis"):
                        await qualify_captured_rule_decision_runtime(
                            dispatched_parent, dispatched_children, recovered_intents, controller=controller,
                            runtime=controller._runtime, cold=cold, capture=captured, packet=basis_packet,
                            **qualification_scope)
                    with pytest.raises(ValueError, match="owned native basis"):
                        await qualify_captured_rule_decision_runtime(
                            dispatched_parent, dispatched_children, recovered_intents, controller=controller,
                            runtime=qualifier_candidate, cold=cold, capture=captured,
                            packet=replace(basis_packet, completion_count=basis_packet.completion_count + 1),
                            **qualification_scope)
                finally:
                    await qualifier_candidate.finish()
                empty_packet = rebuild_program_policy_events(actor, (), run_id=controller.run_id,
                    source_hashes=(), blocks=(block,), population=population, executions=(),
                    order_manager=controller._runtime.order_manager, maximum_events=1000, last_sequence=0)
                continued = rebuild_program_policy_events(actor, events[:basis_sequence],
                    run_id=controller.run_id, source_hashes=source_prefix_hashes, blocks=(block,),
                    population=population, executions=executions, order_manager=controller._runtime.order_manager,
                    maximum_events=1000, last_sequence=basis_sequence, initial_packet=empty_packet)
                assert continued.state_hash == basis_packet.state_hash
                assert continued.journal_sequence == basis_packet.journal_sequence
                assert program_policy_fingerprint(empty_packet.actor) == empty_packet.state_hash
                unchanged = rebuild_program_policy_events(actor, (), run_id=controller.run_id,
                    source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                    executions=executions, order_manager=controller._runtime.order_manager,
                    maximum_events=1000, last_sequence=basis_sequence, initial_packet=continued)
                assert unchanged.state_hash == continued.state_hash
                budget = basis_sequence - 1
                if budget >= max(len(source_prefix_hashes), len(executions), 1):
                    limited = rebuild_program_policy_events(actor, (), run_id=controller.run_id,
                        source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                        executions=executions, order_manager=controller._runtime.order_manager,
                        maximum_events=budget, last_sequence=basis_sequence, initial_packet=continued)
                    assert limited.state_hash == continued.state_hash and limited.journal_sequence == basis_sequence
                    with pytest.raises(ValueError, match="bounded exact scope"):
                        rebuild_program_policy_events(actor, (), run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=budget, last_sequence=basis_sequence)
                if previous_basis_packet is not None:
                    advanced = rebuild_program_policy_events(actor,
                        events[previous_basis_packet.journal_sequence:basis_sequence],
                        run_id=controller.run_id, source_hashes=source_prefix_hashes, blocks=(block,),
                        population=population, executions=executions, order_manager=controller._runtime.order_manager,
                        maximum_events=1000, last_sequence=basis_sequence, initial_packet=previous_basis_packet)
                    assert advanced.state_hash == basis_packet.state_hash
                    assert advanced.execution_count == basis_packet.execution_count
                    assert advanced.completion_count == basis_packet.completion_count
                    suffix_budget = max(basis_sequence - previous_basis_packet.journal_sequence,
                        len(source_prefix_hashes), len(executions), 1)
                    if suffix_budget < basis_sequence:
                        bounded_advanced = rebuild_program_policy_events(actor,
                            events[previous_basis_packet.journal_sequence:basis_sequence],
                            run_id=controller.run_id, source_hashes=source_prefix_hashes, blocks=(block,),
                            population=population, executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=suffix_budget, last_sequence=basis_sequence, initial_packet=previous_basis_packet)
                        assert bounded_advanced.state_hash == advanced.state_hash
                    assert program_policy_fingerprint(previous_basis_packet.actor) == previous_basis_packet.state_hash
                    with pytest.raises(ValueError, match="verified packet"):
                        rebuild_program_policy_events(actor, (), run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=basis_sequence,
                            initial_packet=replace(previous_basis_packet, state_hash="0" * 64))
                    with pytest.raises(ValueError, match="verified packet"):
                        rebuild_program_policy_events(actor, (), run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=basis_sequence,
                            initial_packet=replace(previous_basis_packet,
                                completion_count=previous_basis_packet.completion_count + 1))
                previous_basis_packet = basis_packet
                recovered_selection_scope = {key: value for key, value in reselection_scope.items()
                    if key != "basis_sequence"}
                packet_selection = reselect_recovered_rule_decision(dispatched_parent, dispatched_children,
                    recovered_intents, packet=basis_packet, **recovered_selection_scope)
                assert packet_selection == selected_again
                assert program_policy_fingerprint(basis_packet.actor) == basis_packet.state_hash
                with pytest.raises(ValueError, match="recovery packet differs"):
                    reselect_recovered_rule_decision(dispatched_parent, dispatched_children, recovered_intents,
                        packet=replace(basis_packet, journal_sequence=basis_sequence + 1), **recovered_selection_scope)
                prefix = V4CommittedPrefix(controller.run_id, len(events),
                    "00000000-0000-0000-0000-000000000002", "synthetic", "running",
                    ("00000000-0000-0000-0000-000000000002",))
                class Reader:
                    closed = False
                    def execute(self, sql):
                        assert sql == "SELECT getSetting('readonly')"
                        return "1"
                    def close(self):
                        self.closed = True
                reader = Reader()
                monkeypatch.setattr("src.backend.program_policy_recovery.load_verified_v4_prefix", lambda *_: prefix)
                def page(client, selected_prefix, *, after_sequence, limit, exact_prices=False):
                    assert exact_prices is True
                    assert client is reader and selected_prefix is prefix
                    return tuple(events[after_sequence:after_sequence + limit])
                with patch("src.backend.program_policy_recovery.load_typed_event_page", page):
                    recovered = await controller.recover_program_policy(lambda: reader, prefix,
                        blocks=(block,), population=population, executions=executions, maximum_events=1000)
                assert reader.closed and recovered.actor is not actor and controller._runtime.strategy is actor
                from src.backend.program_policy_recovery import recover_program_policy_async
                continuation_reader = Reader()
                continuation_starts = []
                def continuation_page(client, selected_prefix, *, after_sequence, limit, exact_prices):
                    assert client is continuation_reader and selected_prefix is prefix and exact_prices is True
                    assert after_sequence >= basis_packet.journal_sequence
                    continuation_starts.append(after_sequence)
                    return tuple(events[after_sequence:after_sequence + limit])
                with patch("src.backend.program_policy_recovery.load_typed_event_page", continuation_page):
                    continuation_policy = await recover_program_policy_async(lambda: continuation_reader,
                        prefix, actor, exact_prices=True, source_hashes=source_prefix_hashes, blocks=(block,),
                        population=population, executions=executions, order_manager=controller._runtime.order_manager,
                        maximum_events=1000, initial_packet=basis_packet)
                assert continuation_reader.closed and continuation_starts[0] == basis_packet.journal_sequence
                assert continuation_policy.state_hash == recovered.state_hash
                assert continuation_policy.journal_sequence == recovered.journal_sequence
                later_prefix = replace(prefix, last_sequence=prefix.last_sequence + 1)
                historical_reader = Reader()
                def historical_page(client, selected_prefix, *, after_sequence, limit):
                    assert client is historical_reader and selected_prefix is later_prefix
                    assert after_sequence + limit <= prefix.last_sequence
                    return tuple(events[after_sequence:after_sequence + limit])
                with patch("src.backend.program_policy_recovery.load_verified_v4_prefix", lambda *_: later_prefix), patch(
                        "src.backend.program_policy_recovery.load_typed_event_page", historical_page):
                    historical_policy = await recover_program_policy_async(lambda: historical_reader,
                        later_prefix, actor, through_sequence=prefix.last_sequence,
                        source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                        executions=executions, order_manager=controller._runtime.order_manager, maximum_events=1000)
                assert historical_reader.closed and historical_policy.state_hash == recovered.state_hash
                assert historical_policy.journal_sequence == prefix.last_sequence
                moved_reader = Reader()
                head_reads = []
                def moving_head(*args):
                    head_reads.append(True)
                    return prefix if len(head_reads) == 1 else later_prefix
                with patch("src.backend.program_policy_recovery.load_verified_v4_prefix", moving_head), patch(
                        "src.backend.program_policy_recovery.load_typed_event_page",
                        lambda client, selected_prefix, *, after_sequence, limit: tuple(events[after_sequence:after_sequence + limit])):
                    with pytest.raises(ValueError, match="prefix moved during"):
                        await recover_program_policy_async(lambda: moved_reader, prefix, actor,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager, maximum_events=1000)
                assert moved_reader.closed and controller._runtime.strategy is actor
                assert recovered.actor.execution_state.positions == actor.execution_state.positions
                assert recovered.actor.target_state._targets == actor.target_state._targets
                assert recovered.actor.target_state._entry_intents == actor.target_state._entry_intents
                assert recovered.actor.target_state._advanced_intents == actor.target_state._advanced_intents
                assert recovered.actor.trail_state._water == actor.trail_state._water
                assert recovered.actor._selection_clocks == actor._selection_clocks
                assert recovered.completion_count == offset + 1
                assert recovered.completed_source_sequences == {"assignment": offset + 1}
                assert recovered.source_commit_hashes == source_prefix_hashes
                from src.backend.program_policy_recovery import verify_recovered_program_positions, program_policy_fingerprint
                from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
                portfolio = controller._runtime.portfolio
                accounts = tuple(controller._runtime.config.account_ids)
                portfolio_image = PortfolioRecovery(controller.run_id,
                    {account: recovered.journal_sequence for account in accounts},
                    deepcopy(portfolio.states), deepcopy(portfolio.reservations),
                    deepcopy(portfolio.allocations), deepcopy(portfolio.differences),
                    deepcopy(portfolio._last_filled_by_reservation))
                broker_positions = {account: await controller._runtime.broker.positions(account) for account in accounts}
                from src.backend.program_policy_recovery import verify_program_filled_geometry
                verify_program_filled_geometry(recovered.actor, broker_positions, controller._runtime.portfolio)
                with pytest.raises(ValueError, match="account scope"):
                    verify_program_filled_geometry(recovered.actor, {**broker_positions, "foreign": []},
                        controller._runtime.portfolio)
                if any(broker_positions.values()):
                    altered_positions = {account: [replace(row, position=row.position + 1) for row in rows]
                        for account, rows in broker_positions.items()}
                    with pytest.raises(ValueError, match="positions differ"):
                        verify_program_filled_geometry(recovered.actor, altered_positions, controller._runtime.portfolio)
                broker_ledgers = {account: await controller._runtime.broker.account_ledger(account) for account in accounts}
                broker_orders = await controller._runtime.broker.canonical_orders()
                observations = {"assignment": controller.program_order_observation(assignment_id="assignment", at=at)}
                verify_recovered_program_positions(recovered, actor, run_id=controller.run_id,
                    source_hashes=source_prefix_hashes, at=at,
                    broker_positions=broker_positions, broker_ledgers=broker_ledgers, broker_executions=executions,
                    broker_orders=broker_orders,
                    order_manager=controller._runtime.order_manager, portfolio=portfolio,
                    portfolio_recovery=portfolio_image, observations=observations, population=population)
                assert program_policy_fingerprint(recovered.actor) == recovered.state_hash
                changed_ledgers = {account: replace(ledger, cashbalance=ledger.cashbalance + 1)
                    for account, ledger in broker_ledgers.items()}
                with pytest.raises(ValueError, match="cash ledger differs"):
                    verify_recovered_program_positions(recovered, actor, run_id=controller.run_id,
                        source_hashes=source_prefix_hashes, at=at,
                        broker_positions=broker_positions, broker_ledgers=changed_ledgers, broker_executions=executions,
                        broker_orders=broker_orders, order_manager=controller._runtime.order_manager,
                        portfolio=portfolio, portfolio_recovery=portfolio_image,
                        observations=observations, population=population)
                fraction = recovered.actor.target_state.fraction
                recovered.actor.target_state.fraction = fraction + 1
                with pytest.raises(ValueError, match="policy changed"):
                    verify_recovered_program_positions(recovered, actor, run_id=controller.run_id,
                        source_hashes=source_prefix_hashes, at=at,
                        broker_positions=broker_positions, broker_ledgers=broker_ledgers, broker_executions=executions,
                        broker_orders=broker_orders, order_manager=controller._runtime.order_manager,
                        portfolio=portfolio, portfolio_recovery=portfolio_image,
                        observations=observations, population=population)
                recovered.actor.target_state.fraction = fraction
                if offset == 2:
                    bad = list(events)
                    last_completion = max(index for index, item in enumerate(events)
                        if item.detail and item.detail.get("signal_type") == "program_boundary_completed")
                    bad[last_completion] = replace(bad[last_completion],
                        detail={**bad[last_completion].detail, "decision_reference_price": 999.})
                    with pytest.raises(ValueError, match="reference mark changed"):
                        rebuild_program_policy_events(actor, bad, run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    altered = [replace(execution, commission=execution.commission + 1) for execution in executions]
                    with pytest.raises(ValueError, match="commission differs"):
                        rebuild_program_policy_events(actor, events, run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=altered, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    changed_history = replace(block, history_hashes={"listing": "f" * 64})
                    with pytest.raises(ValueError, match="prepared identity changed"):
                        rebuild_program_policy_events(actor, events, run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(changed_history,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    gapped = [replace(events[0], event={**events[0].event, "sequence": 2}), *events[1:]]
                    with pytest.raises(ValueError, match="gapped"):
                        rebuild_program_policy_events(actor, gapped, run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    missing_fee = [replace(item, event={**item.event, "category": "ignored"}, detail=None)
                        if item.event["entity_type"] == "commission" else item for item in events]
                    with pytest.raises(ValueError, match="coverage is incomplete"):
                        rebuild_program_policy_events(actor, missing_fee, run_id=controller.run_id,
                            source_hashes=source_prefix_hashes, blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    entered, release_read = Event(), Event()
                    cancelled_reader = Reader()
                    def blocked_page(client, selected_prefix, *, after_sequence, limit, exact_prices=False):
                        assert exact_prices is True
                        assert client is cancelled_reader and selected_prefix is prefix
                        entered.set()
                        assert release_read.wait(5)
                        return tuple(events[after_sequence:after_sequence + limit])
                    with patch("src.backend.program_policy_recovery.load_typed_event_page", blocked_page):
                        task = asyncio.create_task(controller.recover_program_policy(lambda: cancelled_reader,
                            prefix, blocks=(block,), population=population, executions=executions, maximum_events=1000))
                        try:
                            assert await asyncio.to_thread(entered.wait, 5)
                            task.cancel()
                            await asyncio.sleep(0)
                            assert not task.done() and not cancelled_reader.closed
                            release_read.set()
                            with pytest.raises(asyncio.CancelledError):
                                await task
                            assert cancelled_reader.closed and controller._runtime.strategy is actor
                        finally:
                            release_read.set()
            completions = [record for record in controller._journal.records(controller.run_id)
                if record.payload.get("signal_type") == "program_boundary_completed"]
            assert len(completions) == 3 and len({record.entity_id for record in completions}) == 3
            from src.trading_runtime.arte_journal_projection import project_journal_record
            for record in completions:
                batch = project_journal_record(record, run_month=record.event_time.date().replace(day=1),
                    attempt_id="00000000-0000-0000-0000-000000000001",
                    batch_id="00000000-0000-0000-0000-000000000002",
                    prior_batch_id="00000000-0000-0000-0000-000000000000", source_cursor="synthetic")
                assert batch.signals[0]["decision_status"] == "completed"
                assert batch.signals[0]["decision_assignment_id"] == "assignment"
                assert batch.events[0]["causation_id"] in runtime.typed_source_commit_hashes()
                assert not batch.signal_evidence_nodes
            from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
            if prefetch:
                future_row, _ = block.row_for("listing", ordinal + 3)
                await publish_source_row(future_row)
                assert runtime.typed_source_cursor()[2] > controller._runtime.last_event_time
            from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
            from tests.test_backtest_v4_keeper_lease import _session
            from src.trading_runtime.runtime import typed_run_config_payload
            from types import SimpleNamespace
            lease_session = _session()
            lease = BacktestV4KeeperLease.acquire(lease_session, run_id=controller.run_id,
                owner_id="synthetic-program-recovery")
            writer = SimpleNamespace(run_id=controller.run_id, run_mode="backtest", coalesce_batches=False,
                journal_profile="backtest_v4", max_events_per_commit=512,
                _client=SimpleNamespace(backtest_v4_lease=lease))
            controller._fixed_v4_lease = lease
            controller._journal_publisher = BacktestTypedJournalPublisher(controller._journal, writer,
                attempt_id="00000000-0000-0000-0000-000000000001", run_month=at.date().replace(day=1),
                initial_sequence=recovered.journal_sequence, prior_batch_id=prefix.last_batch_id,
                source_cursor=prefix.source_cursor, expected_config=typed_run_config_payload(controller._runtime.config))
            try:
                with pytest.raises(ValueError, match="fully fenced"):
                    await controller.install_recovered_program_policy(recovered,
                        portfolio_recovery=portfolio_image, population=population)
                assert controller._runtime.strategy is actor
                # Synthetic storage receipt: the normalized event projection and
                # fake readback above stand in for ClickHouse; no operational write.
                controller._journal.mark_fenced(recovered.journal_sequence)
                assert lease.release()
                with pytest.raises(RuntimeError, match="lease lost"):
                    await controller.install_recovered_program_policy(recovered,
                        portfolio_recovery=portfolio_image, population=population)
                assert controller._runtime.strategy is actor
                lease = BacktestV4KeeperLease.acquire(lease_session, run_id=controller.run_id,
                    owner_id="synthetic-program-recovery-successor")
                controller._fixed_v4_lease = lease
                writer._client.backtest_v4_lease = lease
                await controller.install_recovered_program_policy(recovered,
                    portfolio_recovery=portfolio_image, population=population)
                assert controller._runtime.strategy is controller._program_strategy
                assert controller._program_strategy is not recovered.actor
                assert program_policy_fingerprint(controller._program_strategy) == recovered.state_hash
                # Financial cold rows are normalized via the existing reader.
                # OMS/broker rows below are synthetic, exact captures of the
                # executed three-clock fixture, not operational storage seals.
                from tests.test_arte_portfolio_snapshot_persistence import SnapshotClient
                from src.trading_runtime import arte_portfolio_recovery as portfolio_reader
                from src.trading_runtime.arte_portfolio_snapshot import publish_portfolio_snapshot
                from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage
                from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
                from src.backend.backtest_market_data import market_day_boundary
                snapshot_client = SnapshotClient()
                old_runtime = controller._runtime
                for account, raw in controller._journal.portfolio_states().items():
                    publish_portfolio_snapshot(snapshot_client, run_id=controller.run_id,
                        account_id=account, state_revision=recovered.journal_sequence,
                        snapshot_at=at, state=raw)
                monkeypatch.setattr(portfolio_reader, "load_typed_run_context",
                    lambda *_: {"account_ids": accounts})
                cold = portfolio_reader.recover_portfolio_engine_state(snapshot_client,
                    run_id=controller.run_id, profiles=tuple(state.profile for state in old_runtime.portfolio.states.values()),
                    state_revisions={account: recovered.journal_sequence for account in accounts}, cutoff_at=at)
                manager = old_runtime.order_manager
                groups = {}
                for identity, group in manager._groups.items():
                    assert all(task is None or task.done() for task in (group.reprice_task, group.protection_task))
                    tactic = group.tactic
                    groups[identity] = deepcopy(replace(group, reprice_task=None,
                        protection_task=None, reprice_event=asyncio.Event(), tactic=tactic))
                oms_image = TypedOmsActorImage(controller.run_id, recovered.actor.strategy_id,
                    recovered.actor.revision, groups, dict(manager._group_by_client_id),
                    dict(manager._group_by_broker_id), dict(manager._protection_versions),
                    frozenset(manager._body_entry_group_ids))
                broker_image = old_runtime.broker.checkpoint_state()
                boundary_ms = int((at - market_day_boundary(at.date(), 0)).total_seconds() * 1000)
                broker_rows = project_broker_match_snapshot(run_id=controller.run_id,
                    session_date=at.date(), checkpoint_sequence=recovered.journal_sequence,
                    boundary_ms=boundary_ms, state=broker_image)
                financial_candidate = await controller.stage_program_financial_runtime(portfolio_recovery=cold,
                    oms_image=oms_image, broker_image=broker_image, completed_at=at)
                await financial_candidate.initialize(record_lifecycle=False)
                try:
                    assert lease.release()
                    with pytest.raises(RuntimeError, match="lease lost"):
                        await controller.install_program_runtime_candidate(financial_candidate, recovered,
                            cold=cold, prefix=prefix, broker=broker_rows, oms_image=oms_image, population=population)
                    assert controller._runtime is old_runtime
                    assert controller._journal_publisher.journal is old_runtime.journal
                    lease = BacktestV4KeeperLease.acquire(lease_session, run_id=controller.run_id,
                        owner_id="synthetic-program-financial-successor")
                    controller._fixed_v4_lease = lease
                    writer._client.backtest_v4_lease = lease
                    with pytest.raises(ValueError, match="idle financial fence"):
                        await controller.install_program_runtime_candidate(financial_candidate, recovered,
                            cold=cold, prefix=replace(prefix, last_batch_id="foreign-batch"),
                            broker=broker_rows, oms_image=oms_image, population=population)
                    assert controller._runtime is old_runtime
                    await controller.install_program_runtime_candidate(financial_candidate, recovered,
                        cold=cold, prefix=prefix, broker=broker_rows, oms_image=oms_image, population=population)
                    assert controller._runtime is financial_candidate
                    assert controller._journal is financial_candidate.journal
                    assert controller._journal_publisher.journal is financial_candidate.journal
                    assert financial_candidate.strategy is controller._program_strategy
                    assert financial_candidate.strategy is not recovered.actor
                    assert program_policy_fingerprint(financial_candidate.strategy) == recovered.state_hash
                    assert old_runtime.order_manager._closed and not old_runtime.broker._initialized
                    repeated = await controller.dispatch_prepared_program_boundary(block, population,
                        ordinal=ordinal + 2, assignment_id="assignment", execution_policy=policy,
                        protection_profile=profile, outside_rth=False, terminal=True)
                    assert repeated == {"status": "already_consumed", "results": []}
                    assert financial_candidate.journal.pending_record_count == 0
                finally:
                    if controller._runtime is not financial_candidate:
                        await financial_candidate.finish()
            finally:
                lease.release()
                controller._fixed_v4_lease = None
                controller._journal_publisher = None
        finally:
            await controller._runtime.finish()
    try:
        asyncio.run(run())
    finally:
        publisher.close()
