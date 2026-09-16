"""RVOL must be prepared before replay; no market event may fetch its history."""
import asyncio
from datetime import date, time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.backend.replay_run_service import ReplayRunController, ReplayRunDefinition
from test_replay_run_service import approved_configuration


def controller(tmp_path):
    value = ReplayRunController(ReplayRunDefinition(session_date=date(2026, 8, 19),
        start_time=time(4), configuration_revision=approved_configuration()), runtime_root=tmp_path)
    value._session_relative_volume_enabled = True
    value._resolved_tickers = MagicMock(return_value=('AAA', 'BBB'))
    value._publish = AsyncMock()
    return value


def test_prepares_entire_population_and_restored_artifacts_before_playback(tmp_path):
    value = controller(tmp_path)
    store = MagicMock()
    store.identities = {'RESTORED': 'pinned'}
    baselines = {}
    def prepare(tickers, *, progress, stopped):
        assert tickers == ['AAA', 'BBB', 'RESTORED']
        assert value._preparation_stage == 'session_relative_volume'
        for i, ticker in enumerate(tickers, 1):
            assert not stopped()
            baselines[ticker] = dict(contract='baseline', content_hash='hash', sessions=[], source_revision={})
            store.identities[ticker] = 'pinned'
            progress(i, len(tickers))
    store.prepare_many.side_effect = prepare
    store.cached.side_effect = baselines.__getitem__
    value._session_relative_volume_store = store
    asyncio.run(value._prepare_session_relative_volume())
    assert value._preparation_completed_units == value._preparation_total_units == 3
    assert len([key for key in value._data_authority if key.startswith('session_relative_volume_baseline:')]) == 3
    asyncio.run(value._ensure_session_relative_volume('AAA'))
    store.get.assert_not_called()


def test_playback_missing_baseline_fails_without_network_or_disk_preparation(tmp_path):
    value = controller(tmp_path)
    store = MagicMock()
    store.cached.side_effect = ValueError('RVOL baseline is not prepared')
    value._session_relative_volume_store = store
    with pytest.raises(ValueError, match='not prepared'):
        asyncio.run(value._ensure_session_relative_volume('MISSING'))
    store.get.assert_not_called()
    store.prepare_many.assert_not_called()


def test_preparation_stop_is_terminal_cancellation(tmp_path):
    value = controller(tmp_path)
    value._stop_requested = True
    store = MagicMock()
    store.identities = {}
    def prepare(tickers, *, progress, stopped):
        assert stopped()
        raise InterruptedError('Stopped')
    store.prepare_many.side_effect = prepare
    value._session_relative_volume_store = store
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(value._prepare_session_relative_volume())
