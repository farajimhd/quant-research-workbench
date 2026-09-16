import asyncio
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.backend import replay_run_service as R
from src.trading_runtime.runtime import RunMode
from tests.test_replay_run_service import approved_configuration


def definition(**changes):
    return R.ReplayRunDefinition(session_date=date(2026, 8, 20), start_time=time(4),
        configuration_revision=approved_configuration(), **changes)


def test_preparation_requires_explicit_backtest_mode():
    assert not definition().prepare_frames_only
    with pytest.raises(ValueError, match='Backtest mode'):
        definition(prepare_frames_only=True)
    with pytest.raises(ValueError, match='boolean'):
        definition(mode=RunMode.BACKTEST, prepare_frames_only=1)
    assert definition(mode=RunMode.BACKTEST, prepare_frames_only=True).payload()['prepare_frames_only'] is True


@pytest.mark.parametrize('complete', [True, False])
def test_preparation_stops_before_level_stream_or_market_execution(tmp_path, monkeypatch, complete):
    async def run():
        monkeypatch.setattr(R, '_historical_watchlist_plans_for_configuration', lambda *a, **k: [])
        monkeypatch.setattr(R, '_historical_core_signal_plans_for_configuration', lambda *a, **k: [])
        monkeypatch.setattr(R, '_uses_source_native_identity_preparation', lambda *a: False)
        monkeypatch.setattr(R, '_historical_watchlist_plans_at_source_native_events', lambda *a, **k: [])
        c=R.ReplayRunController(definition(mode=RunMode.BACKTEST, prepare_frames_only=True), runtime_root=tmp_path)
        c.run_dir.mkdir(parents=True)
        for name in ('_publish', '_prepare_historical_watchlist_timeline', '_prepare_v7_coverage', '_initialize_runtime'):
            monkeypatch.setattr(c, name, AsyncMock())
        monkeypatch.setattr(c, '_record_historical_watchlist_authority', lambda: None)
        monkeypatch.setattr(c, '_load_historical_signal_events', AsyncMock(return_value=[]))
        async def frames():
            c._preparation_completed_units=3 if complete else 2
            c._preparation_total_units=3
            c._strategy_frame_cache_status='built'
            return SimpleNamespace(path=tmp_path/'durable.sqlite3')
        monkeypatch.setattr(c, '_load_strategy_frames', frames)
        forbidden=AsyncMock(side_effect=AssertionError('Execution capacity must not be acquired'))
        monkeypatch.setattr(c, '_prepare_v7_stream', forbidden)
        async def finish(status): c.status=status
        monkeypatch.setattr(c, '_finish', finish)
        try:
            await c._run_engine()
            forbidden.assert_not_called()
            assert c.processed_events == 0
            if complete:
                assert c.status == 'stopped'
                assert c._preparation_stage == 'strategy_frames_prepared'
                evidence=c._data_authority['frame_preparation_only']
                assert evidence['status']=='completed'
                assert evidence['completed_streams']==evidence['total_streams']==3
                assert evidence['execution_started'] is False
            else:
                assert c.status == 'failed'
                assert 'incomplete streams' in c.error
                assert 'frame_preparation_only' not in c._data_authority
        finally:
            if c._journal: c._journal.close()
    asyncio.run(run())
