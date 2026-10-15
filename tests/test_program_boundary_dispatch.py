"""Completed program dispatch stays on native market, OMS and journal owners."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.backend.program_boundary_dispatch import dispatch_program_boundary
from src.backend.replay_run_service import ReplayRunController
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.runtime import RunMode
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_columnar_history_publication import prepared_source
from tests.test_prepared_program_dispatch import (
    test_prepared_qmd_scanner_source_boundary_reaches_actual_controller_broker as native_dispatch_scenario,
)


@pytest.mark.parametrize('resolution', [100, 1000])
@pytest.mark.parametrize('failure', [None, 'submission', 'completion_record'])
def test_adapter_retains_actual_source_clock_oms_fills_and_uncertainty_fence(
        prepared_source, tmp_path, resolution, failure, monkeypatch):
    # Reuse the established full integration scenario with this adapter as the
    # dispatch entry point, including typed source prefetch and decision basis.
    monkeypatch.setattr(ReplayRunController, 'dispatch_prepared_program_boundary', dispatch_program_boundary)
    native_dispatch_scenario(prepared_source, tmp_path, resolution, failure,
                             True, False, monkeypatch)


def test_overlapping_dispatch_rejected_without_queue_and_lock_released():
    async def run():
        owner = SimpleNamespace(_program_boundary_dispatch_lock=asyncio.Lock(), _runtime=object())
        await owner._program_boundary_dispatch_lock.acquire()
        kwargs = dict(ordinal=0, assignment_id='assignment', execution_policy=None,
                      protection_profile=None, outside_rth=False)
        with pytest.raises(RuntimeError, match='already active'):
            await dispatch_program_boundary(owner, None, None, **kwargs)
        owner._program_boundary_dispatch_lock.release()
        with pytest.raises(TypeError, match='native trading runtime'):
            await dispatch_program_boundary(owner, None, None, **kwargs)
        assert not owner._program_boundary_dispatch_lock.locked()
    asyncio.run(run())


@pytest.mark.parametrize('mode', [RunMode.REPLAY, RunMode.PAPER, RunMode.LIVE])
def test_mode_change_cannot_bypass_native_assembly_receipt_gate(tmp_path, mode):
    definition, actor = definition_and_actor()
    owner = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
                                program_liquidity_resolution_ms=100)
    owner._journal = BacktestMemoryJournal(run_id=owner.run_id)
    async def run():
        await owner._initialize_runtime(record_configuration=False, record_lifecycle=False)
        runtime = owner._runtime
        original_config = runtime.config
        try:
            runtime.config = replace(runtime.config, mode=mode)
            with pytest.raises(RuntimeError, match='typed|production|Backtest|normalized|receipt'):
                await dispatch_program_boundary(owner, None, None, ordinal=0,
                    assignment_id='assignment', execution_policy=None,
                    protection_profile=None, outside_rth=False)
            assert actor.position_for('assignment').quantity == 0
            assert not owner._program_boundary_dispatch_lock.locked()
        finally:
            runtime.config = original_config
            await runtime.finish()
    asyncio.run(run())
