"""Cross-request cache reuse must preserve causal rows and source authority."""
import asyncio
import json
import sqlite3
import threading
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

from src.backend.prepared_frame_reuse import (
    frame_identity, identity_name, register_identity, compatible_artifacts,
    revalidate, copy_completed_stream, joined_thread, register_legacy_identity,
)
from src.backend.replay_run_service import ReplayFrameSpool, ReplayDerivedFrame, ReplayRunController, ReplayRunDefinition
from test_replay_run_service import approved_configuration


def test_controller_reuses_shorter_subset_and_builds_only_new_stream(tmp_path):
    async def exercise():
        configuration = approved_configuration()
        configuration['payload']['signal_activation'] = {'signal_streams': [
            {'enabled': True, 'occurrence_source': 'qmd_squeeze_episode'}]}
        calls = []
        source_calls = []

        def source(**kwargs):
            source_calls.append(kwargs)
            return dict(token='source:' + kwargs['end'], source_plan_hash=str(kwargs['tickers']),
                        calculation_revision='v1', corporate_action_revision='split-v1',
                        complete_for_history=True, request_complete=True)

        def controller(tickers, end):
            result = ReplayRunController(ReplayRunDefinition(
                session_date=date(2026, 8, 10), start_time=time(4), end_time=end,
                configuration_revision=configuration), runtime_root=tmp_path)
            result._strategy = MagicMock()
            result._strategy.assignments.return_value = [MagicMock(ticker=t, parameters={}) for t in tickers]
            result._strategy_registration = MagicMock()
            result._strategy_registration.timeframe_resolver.return_value = {'1s'}
            return result

        async def fetch(**kwargs):
            calls.append(kwargs['ticker'])
            frames = [ReplayDerivedFrame(as_of=kwargs['start'] + timedelta(hours=h),
                bar={'close': 10.123456789}, indicator={'macd': 0.123456789},
                sequence=h, ticker=kwargs['ticker'], timeframe='1s') for h in (1, 2, 5)]
            await kwargs['frame_sink']([f for f in frames if f.as_of <= kwargs['end']])
            kwargs['authority_sink']('derived:' + kwargs['ticker'] + ':1s', {'original': True})

        with patch('src.backend.replay_run_service.qmd_historical_source_revision', side_effect=source), \
             patch('src.backend.replay_run_service._stream_historical_derived_frames', side_effect=fetch):
            first = controller(['AAA', 'BBB'], time(10))
            original = list(await first._load_strategy_frames())
            second = controller(['AAA', 'CCC'], time(6))
            reused = list(await second._load_strategy_frames())
            assert calls.count('AAA') == 1 and calls.count('BBB') == 1 and calls.count('CCC') == 1
            assert second._strategy_frame_cache_status == 'reused_and_built'
            assert [f for f in reused if f.ticker == 'AAA'] == [
                f for f in original if f.ticker == 'AAA' and f.as_of <= second.definition.session_end]
            assert {f.ticker for f in reused} == {'AAA', 'CCC'}
            assert all(f.as_of <= second.definition.session_end for f in reused)
            assert source_calls[2] == source_calls[0]  # Revalidate ORIGINAL scope, not new scope.
            third = controller(['AAA'], time(5))
            assert len(list(await third._load_strategy_frames())) == 1
            assert third._strategy_frame_cache_status == 'reused'
            assert len(calls) == 3

    asyncio.run(exercise())


def test_identity_scope_and_revision_fail_closed(tmp_path):
    start = datetime(2026, 8, 10, 8, tzinfo=UTC)
    revision = dict(token='source', source_plan_hash='plan', calculation_revision='calc', corporate_action_revision='split')
    identity = frame_identity(schema_version=9, start=start, end=start+timedelta(hours=6),
        requests=[('AAA', '1s'), ('BBB', '1s')], indicator_columns=('macd',), source_revision=revision)
    path = tmp_path / identity_name(identity)
    ReplayFrameSpool(path)
    register_identity(path, identity, warmup_days=7)
    target = dict(identity, requests=[['AAA', '1s']], end=(start+timedelta(hours=2)).isoformat())
    assert len(list(compatible_artifacts(tmp_path, target, warmup_days=7))) == 1
    for changes in ({'indicator_columns': ['rsi']}, {'schema_version': 10},
                    {'start': (start+timedelta(seconds=1)).isoformat()},
                    {'end': (start+timedelta(hours=7)).isoformat()}):
        assert not list(compatible_artifacts(tmp_path, dict(target, **changes), warmup_days=7))
    for key in revision:
        assert not revalidate(identity, warmup_days=7, revision_fetch=lambda **_: dict(
            revision, **{key: 'changed'}, complete_for_history=True, request_complete=True))
    assert not revalidate(identity, warmup_days=7, revision_fetch=lambda **_: dict(
        revision, complete_for_history=False, request_complete=True))


def test_copy_only_completed_streams_preserves_exact_json_and_empty_completion(tmp_path):
    source = ReplayFrameSpool(tmp_path / 'source.sqlite3')
    target = ReplayFrameSpool(tmp_path / 'target.sqlite3')
    end = datetime(2026, 8, 10, 10, tzinfo=UTC)
    source.append([ReplayDerivedFrame(as_of=end, ticker='AAA', timeframe='1s', sequence=1,
        bar={'close': 0.123456789012345}, indicator={'value': 3.123456789012345})])
    assert copy_completed_stream(target.path, source.path, 'AAA', '1s', end=end) is None
    assert not list(target)
    source.mark_stream_complete('AAA', '1s', {'token': 'original'})
    assert copy_completed_stream(target.path, source.path, 'AAA', '1s', end=end) == {'token': 'original'}
    assert list(target) == list(source)
    # Repeating a copy is idempotent, and a certified empty stream is reusable.
    copy_completed_stream(target.path, source.path, 'AAA', '1s', end=end)
    source.mark_stream_complete('BBB', '1s', {})
    assert copy_completed_stream(target.path, source.path, 'BBB', '1s', end=end) == {}
    assert target.completed_streams() == {('AAA', '1s'), ('BBB', '1s')}
    assert len(list(target)) == 1
    with sqlite3.connect(source.path) as a, sqlite3.connect(target.path) as b:
        assert a.execute('select bar_json,indicator_json from strategy_frames').fetchall() == b.execute(
            'select bar_json,indicator_json from strategy_frames').fetchall()


def test_cancelled_copy_keeps_lock_until_worker_exits():
    async def exercise():
        entered, release = threading.Event(), threading.Event()
        lock = asyncio.Lock()
        def write():
            entered.set()
            assert release.wait(5)
        async def owner():
            async with lock:
                await joined_thread(write)
        task = asyncio.create_task(owner())
        try:
            await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # A second stop must not abandon the writer either.
            await asyncio.sleep(0)
            assert lock.locked() and not task.done()
        finally:
            release.set()
        try:
            await task
            assert False, 'cancellation was swallowed'
        except asyncio.CancelledError:
            pass
        assert not lock.locked()
    asyncio.run(exercise())


def test_legacy_promotion_requires_exact_original_hash(tmp_path):
    from zoneinfo import ZoneInfo
    start = datetime(2026, 8, 10, 4, tzinfo=ZoneInfo('America/New_York'))
    end = start + timedelta(hours=6)
    revision = dict(token='token', source_plan_hash='plan', calculation_revision='calc', corporate_action_revision='split')
    identity = frame_identity(schema_version=9, start=start, end=end,
        requests=[('AAA', '1s')], indicator_columns=('macd',), source_revision=revision)
    path = tmp_path / identity_name(identity)
    spool = ReplayFrameSpool(path)
    spool.mark_stream_complete('AAA', '1s', {'indicator_columns': ['macd']})
    summary = dict(session_start=start.isoformat(), session_end=end.isoformat(), data_authority={'sources': {
        'prepared_strategy_frame_source': dict(revision, revision_token='token', indicator_warmup_days=7,
                                              complete_for_history=True, request_complete=True)}})
    wrong = json.loads(json.dumps(summary))
    wrong['data_authority']['sources']['prepared_strategy_frame_source']['revision_token'] = 'other'
    assert not register_legacy_identity(path, wrong, schema_version=9)
    assert not path.with_suffix('.identity.json').exists()
    assert register_legacy_identity(path, summary, schema_version=9)
    assert list(compatible_artifacts(tmp_path, identity, warmup_days=7))
