"""Native cohort scheduling with synthetic sealed-product transport fixtures."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from decimal import Decimal, localcontext

import polars as pl
import pytest

from test_columnar_history_publication import prepared_source, Store, publish, consume
from tests.test_qmd_columnar_history_transport import response_for
from tests.test_backtest_program_inputs import release_for
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_signal_source_runtime_bootstrap import source
from tests.test_signal_stream_typed_publication import FakeStorage
from tests.test_broker_liquidity_resolution import second_bar
from tests.test_liquidity_resolution_producer import source as liquidity_source
from src.backend import backtest_liquidity_resolution as liquidity
from src.backend.backtest_program_inputs import prepare_program_scanner_projection
from src.backend.qmd_columnar_population import CertifiedPopulationWindow
from src.backend.replay_run_service import ReplayRunController
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit
from src.market_engine.volume_rank_rules import select_volume_universe
from src.market_engine import columnar_history_product as history
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.runtime import typed_run_config_payload
from src.trading_runtime.execution_policies import ExecutionPolicy, ExecutionEnvelope, ProtectionProfile, ProtectionSlice, StopRule


def assert_multiple_entry_fill_geometry(actor, executions):
    buys = [row for row in executions if row.side in ("B", "BUY")]
    assert len(buys) >= 2 and len({row.price for row in buys}) >= 2
    assert sum(row.size for row in buys) == 3
    with localcontext() as context:
        context.prec = 80
        quantity = sum(Decimal(str(row.size)) for row in buys)
        average = sum(Decimal(str(row.size)) * Decimal(str(row.price)) for row in buys) / quantity
        position = actor.position_for("assignment")
        assert position.quantity == quantity and position.average_price == average
        assert position.fees == sum(Decimal(str(row.commission)) for row in executions)
        assert average != average.quantize(Decimal("0.0000000001"))


@pytest.mark.parametrize("resolution", [100, 1000])
@pytest.mark.parametrize("partial_fills", [False, True, "multiple"])
@pytest.mark.parametrize("fenced", [False, True, "session", "launch"])
def test_cohort_schedules_native_liquidity_sources_and_qualified_decisions(prepared_source, tmp_path, monkeypatch, resolution, partial_fills, fenced):
    store = Store()
    publish(store, prepared_source)
    certificate = consume(store, prepared_source)
    pin = certificate.pin()
    release = release_for(pin, minimum_age_seconds={stage: 3 for stage in ("add", "reduce", "exit", "trail")}
        if partial_fills == "multiple" else None)
    definition, original = definition_and_actor()
    configuration, scope, _ = source()
    scope["session_key"] = pin.scope.session_date.isoformat()
    revision = {**definition.configuration_revision, "revision_id": scope["configuration_revision"],
        "payload": {**definition.configuration_revision["payload"], **configuration}}
    definition = replace(definition, session_date=pin.scope.session_date, configuration_revision=revision)
    actor = AssignedProgramStrategy(release, original.assignments(), {"assignment": "listing"})
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=resolution)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    publisher = TypedSignalPublicationQueue(FakeStorage(), capacity=1)
    def post(path, request, timeout):
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"])
    async def run_test():
        queued_writer = None
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        try:
            block = await controller.prepare_program_inputs({"listing": pin}, first_ordinal=0, stop_ordinal=200,
                evaluation_block="origin", concurrency=1,
                as_of=history.completed_clock(pin.scope, pin.scope.stop_bucket - 1),
                history_post=post)
            rows = block.market.with_columns(pl.lit("ABC").alias("ticker"),
                (pl.col("boundary_at") - pl.duration(days=1)).alias("prior_close_available_at"),
                pl.lit(10.).alias("adjusted_prior_close"), pl.lit(30).alias("volume_window_seconds"))
            population = CertifiedPopulationWindow(scope["source_revision"], rows,
                select_volume_universe(rows, release.universe), 1, 1, 0, 0, 0)
            projection = prepare_program_scanner_projection(block, population, release)
            masks = block.masks.with_columns(block.market["history.swing_low.2.1__valid"])
            ordinal = masks.filter(pl.col("entry") & pl.col("history.swing_low.2.1__valid"))["observed_ordinal"][0]
            await controller.bootstrap_program_signals(publisher, **scope)
            writer = SimpleNamespace(run_id=controller.run_id, run_mode="backtest", coalesce_batches=False,
                journal_profile="backtest_v4", max_events_per_commit=512)
            if fenced:
                from tests.test_arte_journal_commit_v4 import attached_v4_client
                from src.trading_runtime import arte_journal_writer as writer_module
                from src.trading_runtime import rule_decision_writer_preflight as audit
                persisted = attached_v4_client()
                monkeypatch.setattr(writer_module, "_v4_preflight", lambda client: None)
                monkeypatch.setattr(writer_module, "_verify_run_identity", lambda client, run: {
                    "mode": "backtest", "account_ids": controller._runtime.config.account_ids})
                monkeypatch.setattr(audit, "require_rule_decision_writer_audit", lambda client: None)
                queued_writer = writer_module.ArteJournalWriter(persisted, run_id=controller.run_id,
                    journal_profile="backtest_v4", capacity=1, coalesce_batches=False)
                writer = queued_writer
            controller._journal_publisher = BacktestTypedJournalPublisher(controller._journal, writer,
                attempt_id="00000000-0000-0000-0000-000000000001", run_month=pin.scope.session_date.replace(day=1),
                expected_config=typed_run_config_payload(controller._runtime.config), exact_oms_prices=bool(fenced))
            survivor_probe = fenced != "launch" and fenced and not partial_fills and resolution == 1000
            if survivor_probe:
                from src.backend.backtest_program_inputs import publish_prepared_program_source_clock
                early = block.row_for("listing", 0)[0]["boundary_at"]
                await controller._runtime.advance_program_session_clock(early)
                await publish_prepared_program_source_clock(controller, projection, publisher,
                    at=early, catalogs=scope["catalogs"])
                before_probe = controller._journal.latest_sequence(controller.run_id)
                rejected = await controller.dispatch_prepared_program_clock(
                    {"assignment": block}, population, at=early, assignment_order=("assignment",),
                    execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=3000)),
                    protection_profiles={"assignment": ProtectionProfile("unused", 1,
                        (ProtectionSlice("one", 1., StopRule(price=1.)),))},
                    outside_rth=False, qualify=True, policy_blocks=(block,), maximum_events=1000)
                assert rejected == (("assignment", {"status": "not_admitted", "reason": "fresh_entry_mask_rejected"}),)
                assert not actor._selection_clocks
                assert controller._journal.latest_sequence(controller.run_id) == before_probe
            transport_rows = []
            for offset in ((0, 1, 2) if partial_fills else (0, 2)):
                market, _ = block.row_for("listing", ordinal + offset)
                row = second_bar(market["boundary_at"])
                price = round(market["reference_close"] * 10000)
                if partial_fills:
                    price += offset
                row.update(ticker="ABC", ask_int=price, bid_int=price - 1, close_int=price,
                    high_int=price + 1, low_int=price - 1)
                if partial_fills:
                    row.update(ask_size=(2 if offset == 1 else 1), bid_size=1)
                if resolution == 100:
                    row.update(resolution_ms=100, bucket_index=(row["bucket_index"] + 1) * 10 - 1)
                transport_rows.append(row)
            first_bucket = pin.scope.first_bucket + ordinal
            calls = []
            async def read(*args, **kwargs):
                calls.append(kwargs)
                assert kwargs["concurrency"] == 2
                return {"ABC": tuple(transport_rows)}
            monkeypatch.setattr(liquidity, "read_fixed_liquidity_cohort_async" if resolution == 100
                else "read_liquidity_cohort_async", read)
            day = pin.scope.session_date.isoformat()
            if resolution == 100:
                attempt = "00000000-0000-0000-0000-000000000002"
                plan = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "a" * 64, "b" * 64,
                    (day,), ("ABC",), tuple(MarketDayUnit("a" * 64, day, "ABC", stage,
                        attempt, "c" * 64, 2, "d" * 64) for stage in ("bars", "technical", "broker_100ms")),
                    (100,), "e" * 64)
                prices = PriceLevelPlan(plan.build_id, (PriceLevelUnit(day, "ABC", attempt,
                    "00000000-0000-0000-0000-000000000003", 2, 2, 0., "f" * 64),), "0" * 64)
                authority = dict(market_plan=plan, price_plan=prices)
            else:
                unit = replace(liquidity_source().unit, session_date=pin.scope.session_date, ticker="ABC")
                authority = dict(liquidity_certificates=(liquidity.CertifiedLiquidityResolution(unit,
                    "00000000-0000-0000-0000-000000000003", "f" * 64, "e" * 64, 2, 0, 0.),))
            def protection(at):
                market, _ = block.row_for("listing", block.ordinal_for("listing", at))
                return {"assignment": ProtectionProfile("program-stop", 1,
                    (ProtectionSlice("one", 1., StopRule(price=market["history.swing_low.2.1"])),))}
            arguments = dict(
                source_publisher=publisher, catalogs=scope["catalogs"], reader_factory=lambda: None,
                first_bucket=first_bucket, stop_bucket=first_bucket + 3, concurrency=2,
                assignment_order=("assignment",), execution_policy=ExecutionPolicy(envelope=ExecutionEnvelope(deadline_ms=3000)),
                protection_profiles_at=protection, outside_rth=False, maximum_events=1000, **authority)
            with pytest.raises(ValueError, match="assignment coverage"):
                await controller.run_prepared_program_cohort({}, population, projection, **arguments)
            assert not calls and controller._program_signal_runtime.typed_source_cursor()[0] == int(survivor_probe)
            controller._stop_requested = True
            stopped = await controller.run_prepared_program_cohort({"assignment": block}, population, projection, **arguments)
            assert stopped.status == "stopped" and stopped.liquidity_boundaries == 0 and not calls
            controller._stop_requested = False
            before_sequence = controller._journal.latest_sequence(controller.run_id)
            before_clock = controller._runtime.last_event_time
            async def missing_read(*args, **kwargs):
                return {}
            with monkeypatch.context() as missing:
                missing.setattr(liquidity, "read_fixed_liquidity_cohort_async" if resolution == 100
                    else "read_liquidity_cohort_async", missing_read)
                with pytest.raises(ValueError, match="exact certified ticker coverage"):
                    await controller.run_prepared_program_cohort({"assignment": block}, population, projection, **arguments)
            assert controller._journal.latest_sequence(controller.run_id) == before_sequence
            assert controller._runtime.last_event_time == before_clock
            assert controller._program_dispatch_failure is None
            async def changed_owner_read(*args, **kwargs):
                controller._runtime.config = replace(controller._runtime.config)
                return {"ABC": tuple(transport_rows)}
            original_config = controller._runtime.config
            try:
                with monkeypatch.context() as changed_owner:
                    changed_owner.setattr(liquidity, "read_fixed_liquidity_cohort_async" if resolution == 100
                        else "read_liquidity_cohort_async", changed_owner_read)
                    with pytest.raises(ValueError, match="changed during liquidity I/O"):
                        await controller.run_prepared_program_cohort({"assignment": block}, population, projection, **arguments)
                assert controller._journal.latest_sequence(controller.run_id) == before_sequence
                assert controller._runtime.last_event_time == before_clock
                assert not controller._program_cohort_active
            finally:
                controller._runtime.config = original_config
            async def failed_publisher_read(*args, **kwargs):
                controller._journal_publisher._error = RuntimeError("synthetic writer failure during read")
                return {"ABC": tuple(transport_rows)}
            try:
                with monkeypatch.context() as failed_publisher:
                    failed_publisher.setattr(liquidity, "read_fixed_liquidity_cohort_async" if resolution == 100
                        else "read_liquidity_cohort_async", failed_publisher_read)
                    with pytest.raises(ValueError, match="changed during liquidity I/O"):
                        await controller.run_prepared_program_cohort({"assignment": block}, population, projection, **arguments)
                assert controller._runtime.last_event_time == before_clock
                assert controller._journal.latest_sequence(controller.run_id) == before_sequence
            finally:
                controller._journal_publisher._error = None
            entered, release_read = asyncio.Event(), asyncio.Event()
            async def held_read(*args, **kwargs):
                entered.set()
                await release_read.wait()
                return {"ABC": tuple(transport_rows)}
            with monkeypatch.context() as held:
                held.setattr(liquidity, "read_fixed_liquidity_cohort_async" if resolution == 100
                    else "read_liquidity_cohort_async", held_read)
                owner = asyncio.create_task(controller.run_prepared_program_cohort(
                    {"assignment": block}, population, projection, **arguments))
                await asyncio.wait_for(entered.wait(), timeout=2)
                try:
                    with pytest.raises(RuntimeError, match="already owns"):
                        await controller.run_prepared_program_cohort({"assignment": block}, population, projection, **arguments)
                    assert controller._program_cohort_active
                finally:
                    owner.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await owner
                assert not controller._program_cohort_active
                assert controller._runtime.last_event_time == before_clock
            if fenced:
                from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
                if fenced in ("session", "launch"):
                    from src.backend.backtest_program_session import PreparedProgramCohortWork
                    from src.backend.backtest_market_data import market_day_boundary
                    start_at = market_day_boundary(day, first_bucket * 1000 - 14400000)
                    stop_at = market_day_boundary(day, (first_bucket + 4) * 1000 - 14400000)
                    controller.definition = replace(controller.definition,
                        start_time=start_at.timetz().replace(tzinfo=None),
                        end_time=stop_at.timetz().replace(tzinfo=None))
                    unit_scope = {key: value for key, value in arguments.items()
                        if key not in ("first_bucket", "stop_bucket")}
                    async def session_read(*args, **kwargs):
                        if calls:
                            calls.append(kwargs)
                            return {"ABC": ()}
                        return await read(*args, **kwargs)
                    monkeypatch.setattr(liquidity, "read_fixed_liquidity_cohort_async" if resolution == 100
                        else "read_liquidity_cohort_async", session_read)
                    work = (PreparedProgramCohortWork(
                        first_bucket, first_bucket + 3, {"assignment": block}, population, projection, unit_scope),
                        PreparedProgramCohortWork(first_bucket + 3, first_bucket + 4,
                            {"assignment": block}, population, projection, unit_scope))
                    if fenced == "launch":
                        from hashlib import sha256
                        from src.trading_runtime.journal_contract import canonical_json
                        from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, project_broker_contract
                        from src.trading_runtime.keeper_session import ManagedKeeperSession
                        from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
                        from tests.test_live_signal_completion_keeper import FakeKazoo
                        from src.trading_runtime import arte_backtest_snapshot_anchor as anchors
                        from src.backend import bar_gpt_client
                        revision = controller.definition.configuration_revision
                        controller.definition = replace(controller.definition, configuration_revision={**revision,
                            "content_hash": sha256(canonical_json(revision["payload"]).encode()).hexdigest()})
                        kazoo = FakeKazoo()
                        kazoo.add_listener = lambda listener: None
                        kazoo.remove_listener = lambda listener: None
                        kazoo.stop = lambda: None
                        kazoo.close = lambda: None
                        keeper = ManagedKeeperSession(kazoo)
                        keeper._on_state("CONNECTED")
                        controller._fixed_keeper_session = keeper
                        controller._fixed_v4_lease = BacktestV4KeeperLease.acquire(keeper,
                            run_id=controller.run_id, owner_id="synthetic-programme-launch")
                        controller._program_broker_contract = BacktestBrokerContract(project_broker_contract(
                            controller.run_id, controller._runtime.broker.config, liquidity_plan_hash="c" * 64),
                            controller.definition.initial_cash)
                        controller._journal_writer = queued_writer
                        monkeypatch.setattr(writer_module, "load_typed_run_context",
                            lambda client, run: {"mode": "backtest", "account_ids": controller.account_ids})
                        anchored = []
                        monkeypatch.setattr(anchors, "_publish_terminal_snapshots_after_verified_prefix",
                            lambda client, prefix, captures, context: anchored.append((prefix, captures)))
                        monkeypatch.setattr(bar_gpt_client, "remove_bar_gpt_scope", lambda *args: None)
                        with pytest.raises(RuntimeError, match="prepared typed launch handoff"):
                            await controller.start()
                        assert controller.status == "created"
                        assert controller.current_time is None
                        assert controller._runtime.last_event_time == controller.definition.session_start
                        assert not controller._runtime_finished and not controller._stop_requested
                        from src.trading_runtime import arte_broker_match_snapshot as shared_broker
                        broker_checkpoints = []
                        from src.trading_runtime import arte_oms_observation_snapshot as shared_observations
                        observation_checkpoints = []
                        monkeypatch.setattr(shared_observations, "publish_shared_oms_observations",
                            lambda client, session, rows, **kwargs: observation_checkpoints.append(rows))
                        from src.trading_runtime import arte_portfolio_snapshot as portfolio_snapshot
                        account_checkpoints = []
                        def controlled_account(client, prepared):
                            account_checkpoints.append(prepared)
                            assert prepared.state_revision == broker_checkpoints[-1].snapshot["checkpoint_sequence"]
                            return "f" * 64
                        monkeypatch.setattr(portfolio_snapshot, "publish_prepared_portfolio_snapshot", controlled_account)
                        monkeypatch.setattr(persisted, "manager_keeper_session", keeper, raising=False)
                        monkeypatch.setattr(shared_broker, "publish_shared_broker_snapshot",
                            lambda client, session, rows, **kwargs: broker_checkpoints.append(rows))
                        await controller.bind_prepared_program_session(work,
                            checkpoint_connection_factory=lambda: None, checkpoint_concurrency=2)
                        player = None
                        if resolution in (100, 1000):
                            from src.backend import backtest_program_cohort as cohort_module
                            paused = asyncio.Event()
                            native_dispatch = controller.dispatch_prepared_program_clock
                            native_pause = cohort_module.fence_program_pause
                            dispatch_count = 0
                            async def pause_after_dispatch(*args, **kwargs):
                                nonlocal dispatch_count
                                result = await native_dispatch(*args, **kwargs)
                                dispatch_count += 1
                                if dispatch_count == 2:
                                    await controller.command("pause")
                                return result
                            async def acknowledged_pause(*args, **kwargs):
                                packet = await native_pause(*args, **kwargs)
                                assert controller.status == "paused"
                                assert not controller._journal.unfenced_records()
                                assert controller._journal_publisher.fenced_sequence == packet.journal_sequence
                                assert controller._checkpoint_projection_cache["resume_supported"] is False
                                paused.set()
                                return packet
                            monkeypatch.setattr(controller, "dispatch_prepared_program_clock", pause_after_dispatch)
                            monkeypatch.setattr(cohort_module, "fence_program_pause", acknowledged_pause)
                            async def play_after_pause():
                                await asyncio.wait_for(paused.wait(), timeout=20)
                                await controller.command("play")
                            player = asyncio.create_task(play_after_pause())
                        from src.backend.replay_run_service import ReplayRunService, ReplayRunCapacityError
                        service = ReplayRunService(runtime_root=controller.runtime_root)
                        service._runs[controller.run_id] = controller
                        with pytest.raises(ReplayRunCapacityError, match="duplicate launch rejected"):
                            await service.launch_prepared_program_controller(controller)
                        assert controller._task is None and controller.status == "created"
                        service._runs.pop(controller.run_id)
                        assert await service.launch_prepared_program_controller(controller) is controller
                        assert service.get(controller.run_id) is controller
                        await asyncio.wait_for(controller._task, timeout=20)
                        if player is not None:
                            await player
                            assert dispatch_count == 4 and paused.is_set()
                        assert controller.status == "completed", controller.error
                        assert persisted.tables.get("trading_execution_exact_price_v1")
                        assert not persisted.tables.get("trading_execution_v1")
                        listed = service.list()
                        assert len(listed) == 1 and listed[0]["run_id"] == controller.run_id
                        assert listed[0]["status"] == "completed"
                        assert controller._runtime_finished and controller._journal_writer is None
                        assert controller._program_launch_plan is None
                        prefix = load_verified_v4_prefix(persisted, controller.run_id)
                        assert prefix.status == "completed" and len(anchored) == 1
                        assert controller._program_dispatch_failure is None
                        assert len(broker_checkpoints) == 3
                        assert len(observation_checkpoints) == 3
                        assert [row.root["checkpoint_sequence"] for row in observation_checkpoints] == [
                            row.snapshot["checkpoint_sequence"] for row in broker_checkpoints]
                        assert len(account_checkpoints) == 3
                        assert [item.snapshot["boundary_ms"] for item in broker_checkpoints] == [
                            (first_bucket + offset) * 1000 - 14400000 for offset in (2, 3, 4)]
                        return
                    session = await controller.run_prepared_program_session(work, terminal=True)
                    assert session.status == "evaluated" and session.completed_cohorts == 2
                    assert persisted.tables.get("trading_execution_exact_price_v1")
                    assert not persisted.tables.get("trading_execution_v1")
                    assert session.source_clocks == session.qualified_decisions == 4
                    assert session.liquidity_boundaries == (40 if resolution == 100 else 4)
                    assert not controller._program_session_active
                    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
                    # Extend this in-memory SQL fixture with the cursor/event
                    # join used by the real cold reader; its hash and prefix
                    # validation below remain the production implementation.
                    import json
                    native_execute = persisted.execute
                    def cursor_read(query):
                        if isinstance(query, str) and query.startswith("SELECT c.*"):
                            rows = []
                            for item in persisted.tables["trading_backtest_cursor_v1"]:
                                for event in persisted.tables["trading_event_v1"]:
                                    if all(item[key] == event[key] for key in ("run_id", "batch_id", "record_id")):
                                        rows.append({**item, "event_sequence": event["sequence"],
                                            "event_category": event["category"], "event_entity_type": event["entity_type"],
                                            "event_entity_id": event["entity_id"]})
                            return "\n".join(json.dumps(row) for row in sorted(rows,
                                key=lambda row: row["event_sequence"], reverse=True)[:2])
                        return native_execute(query)
                    monkeypatch.setattr(persisted, "execute", cursor_read)
                    cursor = load_latest_backtest_cursor(persisted, load_verified_v4_prefix(persisted, controller.run_id))
                    assert controller._journal_publisher._source_cursor == f"{cursor['session_date']}:{cursor['boundary_ms']}"
                    assert cursor["boundary_ms"] == (first_bucket + 4) * 1000 - 14400000
                    assert cursor["market_sequence"] == cursor["boundary_ms"] // resolution
                    assert len(calls) == 2
                    assert controller._journal_publisher.fenced_sequence == session.policy_packet.journal_sequence
                    assert load_verified_v4_prefix(persisted, controller.run_id).last_sequence == session.policy_packet.journal_sequence
                    assert not controller._journal.unfenced_records()
                    executions = await controller._runtime.broker.trades()
                    assert executions and sum(row.commission for row in executions) >= 1.
                    if partial_fills == "multiple":
                        assert_multiple_entry_fill_geometry(actor, executions)
                    assert controller._program_dispatch_failure is None
                    from src.trading_runtime import arte_broker_match_snapshot as shared_broker
                    from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract, project_broker_contract
                    from threading import Event
                    controller._journal_writer = queued_writer
                    controller._program_broker_contract = BacktestBrokerContract(project_broker_contract(
                        controller.run_id, controller._runtime.broker.config, liquidity_plan_hash="c" * 64),
                        controller.definition.initial_cash)
                    checkpoint_entered, checkpoint_release = Event(), Event()
                    checkpoint_rows = []
                    from src.trading_runtime import arte_oms_observation_snapshot as shared_observations
                    observation_rows = []
                    monkeypatch.setattr(shared_observations, "publish_shared_oms_observations",
                        lambda client, session, rows, **kwargs: observation_rows.append(rows))
                    from src.trading_runtime import arte_portfolio_snapshot as portfolio_snapshot
                    account_rows = []
                    def controlled_account(client, prepared):
                        account_rows.append(prepared)
                        return "f" * 64
                    monkeypatch.setattr(portfolio_snapshot, "publish_prepared_portfolio_snapshot", controlled_account)
                    def controlled_checkpoint(client, session, rows, **kwargs):
                        checkpoint_rows.append(rows)
                        checkpoint_entered.set()
                        assert checkpoint_release.wait(timeout=5)
                    monkeypatch.setattr(shared_broker, "publish_shared_broker_snapshot", controlled_checkpoint)
                    monkeypatch.setattr(persisted, "manager_keeper_session", None, raising=False)
                    checkpoint = asyncio.create_task(controller.publish_program_broker_checkpoint(
                        connection_factory=lambda: None, concurrency=2))
                    try:
                        assert await asyncio.to_thread(checkpoint_entered.wait, 2)
                        with pytest.raises(RuntimeError, match="checkpoint already owns"):
                            await controller.run_prepared_program_session(())
                        with pytest.raises(RuntimeError, match="exclusive native"):
                            await controller.finish_prepared_program_session(status="completed")
                    finally:
                        checkpoint_release.set()
                    published = await checkpoint
                    assert checkpoint_rows == [published]
                    assert len(observation_rows) == 1
                    assert observation_rows[0].root["checkpoint_sequence"] == published.snapshot["checkpoint_sequence"]
                    assert len(account_rows) == 1
                    assert account_rows[0].state_revision == published.snapshot["checkpoint_sequence"]
                    assert published.snapshot["checkpoint_sequence"] == session.policy_packet.journal_sequence
                    assert published.snapshot["boundary_ms"] == cursor["boundary_ms"]
                    assert not controller._program_checkpoint_active and controller._checkpoint_io_task is None
                    # This transport fixture has no admitted run-context or
                    # account-anchor storage. Keep that external seam explicit;
                    # V4 commit/cold readback and broker captures remain real.
                    from src.trading_runtime import arte_backtest_snapshot_anchor as anchors
                    monkeypatch.setattr(writer_module, "load_typed_run_context",
                        lambda client, run: {"mode": "backtest", "account_ids": controller.account_ids})
                    anchored = []
                    monkeypatch.setattr(anchors, "_publish_terminal_snapshots_after_verified_prefix",
                        lambda client, prefix, captures, context: anchored.append((prefix, captures)))
                    evaluated = controller._program_evaluated_terminal
                    controller._program_evaluated_terminal = None
                    with pytest.raises(RuntimeError, match="evaluated terminal"):
                        await controller.finish_prepared_program_session(status="completed")
                    assert not controller._runtime_finished and not anchored
                    controller._program_evaluated_terminal = evaluated
                    native_finish = controller._runtime.finish
                    async def owned_finish(*args, **kwargs):
                        with pytest.raises(RuntimeError, match="terminal already owns"):
                            await controller.run_prepared_program_session(())
                        return await native_finish(*args, **kwargs)
                    monkeypatch.setattr(controller._runtime, "finish", owned_finish)
                    terminal = await controller.finish_prepared_program_session(status="completed")
                    assert terminal.status == controller.status == "completed"
                    assert controller._runtime_finished
                    assert terminal.last_sequence > session.policy_packet.journal_sequence
                    assert load_verified_v4_prefix(persisted, controller.run_id).last_sequence == terminal.last_sequence
                    assert not controller._journal.unfenced_records()
                    assert len(anchored) == 1
                    assert {capture.account_id for capture in anchored[0][1]} == set(controller.account_ids)
                    assert all(capture.state_revision == terminal.last_sequence for capture in anchored[0][1])
                    with pytest.raises(RuntimeError, match="acknowledged"):
                        await controller.finish_prepared_program_session(status="completed")
                    with pytest.raises(RuntimeError, match="already finished"):
                        await controller.run_prepared_program_session(())
                    return
                else:
                    result = await controller.run_fenced_program_cohort({"assignment": block}, population, projection, **arguments)
                assert result.status == "completed" and result.qualified_decisions == 3
                first_sequence = result.policy_packet.journal_sequence
                assert controller._journal_publisher.fenced_sequence == first_sequence
                assert not controller._journal.unfenced_records()
                assert load_verified_v4_prefix(persisted, controller.run_id).last_sequence == first_sequence
                executions = await controller._runtime.broker.trades()
                assert executions and sum(row.commission for row in executions) >= 1.
                if partial_fills == "multiple":
                    assert_multiple_entry_fill_geometry(actor, executions)
                elif partial_fills:
                    assert any(row.size == 2 for row in executions)
                    assert persisted.tables["trading_oms_entry_acquisition_freeze_v1"]
                # The exact same owners continue after pending records and
                # decision sidecars have been released by the acknowledged fence.
                async def empty_next(*args, **kwargs):
                    return {"ABC": ()}
                monkeypatch.setattr(liquidity, "read_fixed_liquidity_cohort_async" if resolution == 100
                    else "read_liquidity_cohort_async", empty_next)
                continued = await controller.run_fenced_program_cohort({"assignment": block}, population, projection,
                    **{**arguments, "first_bucket": first_bucket + 3, "stop_bucket": first_bucket + 4,
                       "initial_packet": result.policy_packet})
                assert continued.qualified_decisions == continued.source_clocks == 1
                assert continued.policy_packet.journal_sequence > first_sequence
                assert controller._journal_publisher.fenced_sequence == continued.policy_packet.journal_sequence
                assert load_verified_v4_prefix(persisted, controller.run_id).last_sequence == continued.policy_packet.journal_sequence
                assert not controller._journal.unfenced_records()
                assert controller._program_dispatch_failure is None
                return
            result = await controller.run_prepared_program_cohort({"assignment": block}, population, projection, **arguments)
            assert result.status == "completed" and result.source_clocks == result.qualified_decisions == 3
            assert result.liquidity_boundaries == (30 if resolution == 100 else 3)
            assert len(calls) == 1 and controller._program_signal_runtime.typed_source_cursor()[0] == 3 + int(survivor_probe)
            assert controller._journal_publisher.fenced_sequence == 0
            assert result.policy_packet.journal_sequence == controller._journal.latest_sequence(controller.run_id)
            executions = await controller._runtime.broker.trades()
            assert executions and sum(row.commission for row in executions) >= 1.
            # Exact Float32 stop prices currently exceed the V1 OMS decimal
            # scale. Projection must fail closed until the shared precision
            # contract is versioned; it must not silently round the program.
            with pytest.raises(ValueError, match="Number cannot fit typed Decimal"):
                controller._journal_publisher._prepare_batches(result.policy_packet.journal_sequence)
            controller._journal_publisher.exact_oms_prices = True
            try:
                if partial_fills == "multiple":
                    assert_multiple_entry_fill_geometry(actor, executions)
                elif partial_fills:
                    assert any(row.size == 2 for row in executions)
                    assert any(record.entity_type == "entry_acquisition_frozen_before_exit"
                        for record in controller._journal.records(controller.run_id))
                units = controller._journal_publisher._prepare_batches(result.policy_packet.journal_sequence)
                assert len(units) == 1
                from tests.test_arte_journal_commit_v4 import attached_v4_client
                from src.trading_runtime.arte_journal_compound_v4 import publish_compound_v4
                from src.trading_runtime import rule_decision_writer_preflight as audit
                from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
                persisted = attached_v4_client()
                with monkeypatch.context() as transport:
                    transport.setattr(audit, "require_rule_decision_writer_audit", lambda client: None)
                    publish_compound_v4(persisted, units[0])
                committed = load_verified_v4_prefix(persisted, controller.run_id)
                assert committed.last_sequence == result.policy_packet.journal_sequence
                from src.trading_runtime.arte_journal_projection import project_journal_record
                exit_command = next((record for record in controller._journal.records(controller.run_id)
                    if record.category == "command" and record.entity_type == "order"
                    and record.payload.get("action") == "exit"), None)
                projection_scope = dict(run_month=controller._journal_publisher.run_month,
                    attempt_id=controller._journal_publisher.attempt_id,
                    batch_id=units[0].base.batch_id, prior_batch_id=units[0].base.prior_batch_id,
                    source_cursor=units[0].base.source_cursor,
                    expected_config=controller._journal_publisher.expected_config,
                    expected_mode="backtest", exact_order_prices=True)
                if partial_fills != "multiple":
                    assert exit_command is not None
                for action in ("hold", "wait", "cancel_entry", "unknown"):
                    if partial_fills == "multiple":
                        assert exit_command is None
                        continue
                    with pytest.raises(ValueError, match="not an exact typed command"):
                        project_journal_record(replace(exit_command,
                            payload={**exit_command.payload, "action": action}), **projection_scope)
            finally:
                controller._journal_publisher.exact_oms_prices = False
            assert controller._journal_publisher.fenced_sequence == 0
            from src.backend import backtest_program_publication as publication
            proofs = publication.program_decision_projection_arguments(controller._journal_publisher,
                result.policy_packet.journal_sequence)["rule_decision_qualifications"]
            spans = [controller._journal.rule_decision_basis_for_record(identity)[0] for identity in proofs]
            first_parent = min((parent for parent in spans
                if parent["decision_sequence"] - parent["basis_sequence"] > 1),
                key=lambda parent: parent["basis_sequence"])
            start, end = first_parent["basis_sequence"], first_parent["decision_sequence"]
            owned_publisher = controller._journal_publisher
            assert publication.program_publication_cut(owned_publisher,
                result.policy_packet.journal_sequence) == result.policy_packet.journal_sequence
            with pytest.raises(ValueError, match="truncates"):
                publication.program_publication_cut(owned_publisher, start + 1)
            original_budget = owned_publisher.batch_size
            try:
                owned_publisher.batch_size = end - 1
                assert publication.program_publication_cut(owned_publisher,
                    result.policy_packet.journal_sequence) == start
                owned_publisher._sequence = start
                owned_publisher.batch_size = end - start - 1
                with pytest.raises(ValueError, match="existing writer event budget"):
                    publication.program_publication_cut(owned_publisher, result.policy_packet.journal_sequence)
            finally:
                owned_publisher._sequence = 0
                owned_publisher.batch_size = original_budget
            corrupt = dict(proofs)
            first = next(iter(corrupt))
            corrupt[first] = replace(corrupt[first], semantic_hash="0" * 64)
            with monkeypatch.context() as rejected_proof:
                rejected_proof.setattr(publication, "program_decision_projection_arguments",
                    lambda *_: {"rule_decision_qualifications": corrupt})
                with pytest.raises(ValueError, match="matching native qualification"):
                    controller._journal_publisher._prepare_batches(result.policy_packet.journal_sequence)
            assert controller._journal_publisher.fenced_sequence == 0
        finally:
            if not controller._runtime_finished:
                await controller._runtime.finish()
            if queued_writer is not None:
                queued_writer.close()
    try:
        asyncio.run(run_test())
    finally:
        publisher.close()
