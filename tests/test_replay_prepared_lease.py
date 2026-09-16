import asyncio
import json
import sqlite3
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.backend import replay_run_service as R
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.runtime import RunMode


def read_leases(path):
    with sqlite3.connect(path) as db:
        return [json.loads(raw) for raw, in db.execute(
            "select payload_json from journal where category='resource_lease' order by sequence")]


@pytest.mark.parametrize('release_fails', [False, True])
def test_failed_preparation_has_durable_owner_before_remote_acquisition(tmp_path, monkeypatch, release_fails):
    c=R.ReplayRunController.__new__(R.ReplayRunController)
    at=datetime(2026, 8, 19, 8, tzinfo=UTC)
    c.definition=SimpleNamespace(mode=RunMode.BACKTEST, experimental_structure_book='level-book-v7',
        debug_fixture=None, final_session_date=None, session_date=date(2026, 8, 19),
        session_start=at, session_end=at, experimental_structure_fingerprint='fixture')
    c.run_id='owned-run'; c.current_time=None; c._stop_requested=False; c._data_authority={}
    c._publish=AsyncMock()
    path=tmp_path/'journal.sqlite3'
    c._journal=TradingJournal(path)
    c._journal.enable_write_batching()
    frames=R.ReplayFrameSpool(tmp_path/'frames.sqlite3')
    monkeypatch.setattr(frames, 'completed_streams', lambda: [('TEST', '1s')])
    monkeypatch.setattr('src.backend.experimental_structure_book.resolve', lambda *a: {'fingerprint':'fixture'})
    calls=[]
    def post(url, request, **kwargs):
        records=read_leases(path)
        assert records[0]['phase']=='acquiring'
        assert records[0]['owner_run_id']=='owned-run'
        assert records[0]['stream_id']==request['stream_id']
        assert records[0]['owner_pid']>0
        calls.append(request['operation'])
        if request['operation']=='prepare':
            raise OSError('preparation transport failed')
        if release_fails:
            raise RuntimeError('release transport failed')
        return {'rows':[]}
    monkeypatch.setattr('src.backend.qmd_gateway_client.qmd_history_post_json', post)
    try:
        with pytest.raises((OSError, RuntimeError), match='transport failed'):
            asyncio.run(c._prepare_v7_stream(frames))
        assert calls==['prepare', 'release']
        records=read_leases(path)
        assert [r['phase'] for r in records]==['acquiring', 'release_failed' if release_fails else 'released']
        assert records[-1]['stream_id']==records[0]['stream_id']
        assert records[-1]['error_type']==('RuntimeError' if release_fails else None)
    finally:
        c._journal.close()


def test_prepared_ownership_fails_closed_without_journal():
    c=R.ReplayRunController.__new__(R.ReplayRunController)
    c._journal=None
    with pytest.raises(RuntimeError, match='durable journal'):
        c._record_prepared_v7_lease(SimpleNamespace(stream_id='test'), 'acquiring')
