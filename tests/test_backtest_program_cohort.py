"""Native cohort scheduling with synthetic sealed-product transport fixtures."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

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


@pytest.mark.parametrize("resolution", [100, 1000])
def test_cohort_schedules_native_liquidity_sources_and_qualified_decisions(prepared_source, tmp_path, monkeypatch, resolution):
    store = Store()
    publish(store, prepared_source)
    certificate = consume(store, prepared_source)
    pin = certificate.pin()
    release = release_for(pin)
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
            controller._journal_publisher = BacktestTypedJournalPublisher(controller._journal, writer,
                attempt_id="00000000-0000-0000-0000-000000000001", run_month=pin.scope.session_date.replace(day=1),
                expected_config=typed_run_config_payload(controller._runtime.config))
            transport_rows = []
            for offset in (0, 2):  # The middle certified window has no liquidity row.
                market, _ = block.row_for("listing", ordinal + offset)
                row = second_bar(market["boundary_at"])
                price = round(market["reference_close"] * 10000)
                row.update(ticker="ABC", ask_int=price, bid_int=price - 1, close_int=price,
                    high_int=price + 1, low_int=price - 1)
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
            assert not calls and controller._program_signal_runtime.typed_source_cursor()[0] == 0
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
            result = await controller.run_prepared_program_cohort({"assignment": block}, population, projection, **arguments)
            assert result.status == "completed" and result.source_clocks == result.qualified_decisions == 3
            assert result.liquidity_boundaries == (30 if resolution == 100 else 3)
            assert len(calls) == 1 and controller._program_signal_runtime.typed_source_cursor()[0] == 3
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
                # The OMS price contract is now exact. Shared order-command
                # projection is the next independently confirmed precision gap.
                with pytest.raises(ValueError, match="losslessly for order.aux_price"):
                    controller._journal_publisher._prepare_batches(result.policy_packet.journal_sequence)
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
            await controller._runtime.finish()
    try:
        asyncio.run(run_test())
    finally:
        publisher.close()
