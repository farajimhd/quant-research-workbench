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
def test_prepared_qmd_scanner_source_boundary_reaches_actual_controller_broker(prepared_source, tmp_path, resolution, failure):
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
    publisher = TypedSignalPublicationQueue(FakeStorage(), capacity=1)
    def post(path, request, timeout):
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"])
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
            candidates = block.masks.with_columns(block.market["history.swing_low.2.1__valid"])
            ordinal = candidates.filter(pl.col("entry") & pl.col("history.swing_low.2.1__valid"))["observed_ordinal"][0]
            await controller.bootstrap_program_signals(publisher, **scope)
            policy = ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=2000))
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
                runtime = controller._program_signal_runtime
                candidate.update(last_price=row["reference_close"], rolling_share_volume=row["rolling_volume"])
                stage = runtime.stage_resolve(revision["payload"], [candidate], as_of=at, data_fields_projected=True)
                sequence, prior, _ = runtime.typed_source_cursor()
                pending = runtime.submit_staged(stage, publisher, batch_sequence=sequence + 1,
                    previous_commit_hash=prior, configuration_revision=scope["configuration_revision"],
                    source_revision=scope["source_revision"], catalogs=scope["catalogs"])
                await pending.wait_async()
                runtime.promote_staged(pending)
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
                result = await controller.dispatch_prepared_program_boundary(block, population,
                    ordinal=ordinal + offset, assignment_id="assignment", execution_policy=policy,
                    protection_profile=profile, outside_rth=False, terminal=offset == 2)
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
                from src.backend.program_policy_recovery import rebuild_program_policy_events
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
                prefix = V4CommittedPrefix(controller.run_id, len(events),
                    "00000000-0000-0000-0000-000000000002", "synthetic", "running",
                    ("00000000-0000-0000-0000-000000000002",))
                class Reader:
                    closed = False
                    def close(self):
                        self.closed = True
                reader = Reader()
                def page(client, selected_prefix, *, after_sequence, limit):
                    assert client is reader and selected_prefix is prefix
                    return tuple(events[after_sequence:after_sequence + limit])
                with patch("src.backend.program_policy_recovery.load_typed_event_page", page):
                    recovered = await controller.recover_program_policy(lambda: reader, prefix,
                        blocks=(block,), population=population, executions=executions, maximum_events=1000)
                assert reader.closed and recovered.actor is not actor and controller._runtime.strategy is actor
                assert recovered.actor.execution_state.positions == actor.execution_state.positions
                assert recovered.actor.target_state._targets == actor.target_state._targets
                assert recovered.actor.target_state._entry_intents == actor.target_state._entry_intents
                assert recovered.actor.target_state._advanced_intents == actor.target_state._advanced_intents
                assert recovered.actor.trail_state._water == actor.trail_state._water
                assert recovered.actor._selection_clocks == actor._selection_clocks
                assert recovered.completion_count == offset + 1
                assert recovered.completed_source_sequences == {"assignment": offset + 1}
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
                broker_ledgers = {account: await controller._runtime.broker.account_ledger(account) for account in accounts}
                broker_orders = await controller._runtime.broker.canonical_orders()
                observations = {"assignment": controller.program_order_observation(assignment_id="assignment", at=at)}
                verify_recovered_program_positions(recovered, actor, run_id=controller.run_id,
                    source_hashes=runtime.typed_source_commit_hashes(), at=at,
                    broker_positions=broker_positions, broker_ledgers=broker_ledgers, broker_executions=executions,
                    broker_orders=broker_orders,
                    order_manager=controller._runtime.order_manager, portfolio=portfolio,
                    portfolio_recovery=portfolio_image, observations=observations, population=population)
                assert program_policy_fingerprint(recovered.actor) == recovered.state_hash
                changed_ledgers = {account: replace(ledger, cashbalance=ledger.cashbalance + 1)
                    for account, ledger in broker_ledgers.items()}
                with pytest.raises(ValueError, match="cash ledger differs"):
                    verify_recovered_program_positions(recovered, actor, run_id=controller.run_id,
                        source_hashes=runtime.typed_source_commit_hashes(), at=at,
                        broker_positions=broker_positions, broker_ledgers=changed_ledgers, broker_executions=executions,
                        broker_orders=broker_orders, order_manager=controller._runtime.order_manager,
                        portfolio=portfolio, portfolio_recovery=portfolio_image,
                        observations=observations, population=population)
                fraction = recovered.actor.target_state.fraction
                recovered.actor.target_state.fraction = fraction + 1
                with pytest.raises(ValueError, match="policy changed"):
                    verify_recovered_program_positions(recovered, actor, run_id=controller.run_id,
                        source_hashes=runtime.typed_source_commit_hashes(), at=at,
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
                            source_hashes=runtime.typed_source_commit_hashes(), blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    altered = [replace(execution, commission=execution.commission + 1) for execution in executions]
                    with pytest.raises(ValueError, match="commission differs"):
                        rebuild_program_policy_events(actor, events, run_id=controller.run_id,
                            source_hashes=runtime.typed_source_commit_hashes(), blocks=(block,), population=population,
                            executions=altered, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    changed_history = replace(block, history_hashes={"listing": "f" * 64})
                    with pytest.raises(ValueError, match="prepared identity changed"):
                        rebuild_program_policy_events(actor, events, run_id=controller.run_id,
                            source_hashes=runtime.typed_source_commit_hashes(), blocks=(changed_history,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    gapped = [replace(events[0], event={**events[0].event, "sequence": 2}), *events[1:]]
                    with pytest.raises(ValueError, match="gapped"):
                        rebuild_program_policy_events(actor, gapped, run_id=controller.run_id,
                            source_hashes=runtime.typed_source_commit_hashes(), blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    missing_fee = [replace(item, event={**item.event, "category": "ignored"}, detail=None)
                        if item.event["entity_type"] == "commission" else item for item in events]
                    with pytest.raises(ValueError, match="coverage is incomplete"):
                        rebuild_program_policy_events(actor, missing_fee, run_id=controller.run_id,
                            source_hashes=runtime.typed_source_commit_hashes(), blocks=(block,), population=population,
                            executions=executions, order_manager=controller._runtime.order_manager,
                            maximum_events=1000, last_sequence=len(events))
                    entered, release_read = Event(), Event()
                    cancelled_reader = Reader()
                    def blocked_page(client, selected_prefix, *, after_sequence, limit):
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
                assert controller._runtime.strategy is recovered.actor
                assert controller._program_strategy is recovered.actor
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
