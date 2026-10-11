import asyncio
from copy import deepcopy
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
        finally:
            await controller._runtime.finish()
    try:
        asyncio.run(run())
    finally:
        publisher.close()
