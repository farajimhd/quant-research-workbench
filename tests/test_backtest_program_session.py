"""Session orchestration contracts; native cohort/fence tests cover execution."""
import asyncio
from dataclasses import replace
from datetime import time

import polars as pl
import pytest

from test_columnar_history_publication import prepared_source, Store, publish, consume
from tests.test_qmd_columnar_history_transport import response_for
from tests.test_backtest_program_inputs import release_for
from tests.test_program_controller_runtime import definition_and_actor
from src.backend.replay_run_service import ReplayRunController
from src.backend.backtest_program_inputs import prepare_program_block, prepare_program_scanner_projection
from src.backend.qmd_columnar_population import CertifiedPopulationWindow
from src.backend.backtest_program_session import PreparedProgramCohortWork
from src.backend.backtest_program_cohort import ProgramCohortResult
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.market_engine.volume_rank_rules import select_volume_universe
from src.market_engine import columnar_history_product as history


@pytest.mark.parametrize("stop_after_first", [False, True])
def test_session_carries_packet_and_rejects_gaps_before_dispatch(prepared_source, tmp_path, monkeypatch, stop_after_first):
    store = Store()
    publish(store, prepared_source)
    certificate = consume(store, prepared_source)
    pin = certificate.pin()
    release = release_for(pin)
    definition, old_actor = definition_and_actor()
    payload = {**definition.configuration_revision["payload"], "strategy": {
        "strategy_id": release.header["strategy_id"], "revision": release.header["strategy_number"]}}
    definition = replace(definition, session_date=pin.scope.session_date, start_time=time(9, 30), end_time=time(9, 30, 4),
        configuration_revision={**definition.configuration_revision, "payload": payload})
    actor = AssignedProgramStrategy(release, old_actor.assignments(), {"assignment": "listing"})
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=1000)
    def post(path, request, timeout):
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"])

    async def run():
        block = await prepare_program_block(release, {"listing": pin}, first_ordinal=0, stop_ordinal=4,
            evaluation_block="origin", as_of=history.completed_clock(pin.scope, pin.scope.first_bucket + 3), concurrency=1, history_post=post)
        rows = block.market.with_columns(pl.lit("ABC").alias("ticker"),
            (pl.col("boundary_at") - pl.duration(days=1)).alias("prior_close_available_at"),
            pl.lit(10.).alias("adjusted_prior_close"), pl.lit(30).alias("volume_window_seconds"))
        population = CertifiedPopulationWindow("source", rows, select_volume_universe(rows, release.universe), 1, 1, 0, 0, 0)
        projection = prepare_program_scanner_projection(block, population, release)
        scope = dict(source_publisher=object(), catalogs={}, reader_factory=lambda: None, concurrency=1,
            assignment_order=("assignment",), execution_policy=object(), protection_profiles_at=lambda at: {},
            outside_rth=False, maximum_events=1000)
        work = tuple(PreparedProgramCohortWork(34200 + index * 2, 34202 + index * 2,
            {"assignment": block}, population, projection, scope) for index in range(2))
        calls = []
        async def cohort(self, blocks, population, projection, **kwargs):
            assert self._program_session_active
            calls.append(kwargs)
            if len(calls) == 1:
                assert kwargs["initial_packet"] == "initial"
                outsider = asyncio.create_task(self.run_prepared_program_cohort({}, None, None))
                with pytest.raises(RuntimeError, match="session already owns"):
                    await outsider
            else:
                assert kwargs["initial_packet"] == "packet-1"
            if stop_after_first:
                self._stop_requested = True
            return ProgramCohortResult("completed", 2, 2, 1, f"packet-{len(calls)}")
        monkeypatch.setattr(ReplayRunController, "run_fenced_program_cohort", cohort)
        bad = (work[0], replace(work[1], first_bucket=34203))
        with pytest.raises(ValueError, match="gap"):
            await controller.run_prepared_program_session(bad, initial_packet="initial")
        assert not calls
        result = await controller.run_prepared_program_session(work, initial_packet="initial")
        assert result.status == ("stopped" if stop_after_first else "evaluated")
        assert result.completed_cohorts == len(calls) == (1 if stop_after_first else 2)
        assert result.policy_packet == f"packet-{len(calls)}"
        assert result.source_clocks == 2 * len(calls)
        assert result.qualified_decisions == len(calls)
        assert calls[0]["terminal"] is False
        if not stop_after_first:
            assert calls[1]["terminal"] is True
        assert not controller._program_session_active and controller._program_session_task is None
    asyncio.run(run())
