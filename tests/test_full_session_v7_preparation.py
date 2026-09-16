import hashlib
import json
import os
from pathlib import Path
import tempfile
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from unittest.mock import AsyncMock, Mock

from src.backend.historical_signal_occurrence_service import historical_source_native_signal_occurrences
from src.backend.replay_run_service import ReplayFrameSpool, ReplayDerivedFrame, _stream_historical_bar_derived_frames, _structural_recovery_projection_tickers


class CoveragePreflightTests(IsolatedAsyncioTestCase):
    async def test_missing_book_is_warned_and_guarded_before_signal_activation(self):
        from src.backend.replay_run_service import ReplayRunController
        from src.trading_runtime.runtime import RunMode
        with tempfile.TemporaryDirectory() as folder:
            controller = object.__new__(ReplayRunController)
            controller.definition = SimpleNamespace(mode=RunMode.BACKTEST,
                experimental_structure_book='level-book-v7', execution_mode='strategy',
                session_date=datetime(2026, 8, 21).date(), final_session_date=None,
                requested_start=datetime(2026, 8, 21, 8, tzinfo=UTC))
            controller.run_dir = Path(folder)
            controller.run_id = 'test'
            controller._v7_excluded_tickers = set()
            controller._selected_assignments = lambda: [dict(ticker='GOOD'), dict(ticker='LGHL')]
            controller._publish = AsyncMock()
            controller._record_data_authority = Mock()
            controller._journal = Mock()
            response = dict(catalog_hash='pinned', rows=[dict(ticker='GOOD', eligible=True,
                checkpoint_session='2026-08-20', checkpoint_hash='one'),
                dict(ticker='LGHL', eligible=False, reason='ambiguous published ticker identity')])
            with patch('src.backend.qmd_gateway_client.qmd_history_post_json', return_value=response):
                await controller._prepare_v7_coverage()
            assert controller._v7_excluded_tickers == {'LGHL'}
            report = json.loads((Path(folder) / 'level-book-coverage.json').read_text())
            assert report['eligible_ticker_count'] == 1
            assert report['excluded'][0]['ticker'] == 'LGHL'
            assert controller._journal.append.call_args.kwargs['category'] == 'warning'
            # No configuration/cache access or activation is possible for an excluded ticker.
            await controller._process_external_signal_event(SimpleNamespace(
                ticker='LGHL', available_at=controller.definition.requested_start))


class ArtifactTests(TestCase):
    def test_preparation_cleanup_is_indexed_and_preserves_other_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frames.sqlite3'
            spool = ReplayFrameSpool(path)
            at = datetime(2026, 8, 21, 8, tzinfo=UTC)
            spool.append([ReplayDerivedFrame(as_of=at, bar={'close': 3}, indicator={}, sequence=1,
                ticker=ticker, timeframe=timeframe)
                for ticker, timeframe in [('AAA', '1s'), ('AAA', '5s'), ('BBB', '1s')]])
            with closing(sqlite3.connect(path)) as connection:
                plan = connection.execute('EXPLAIN QUERY PLAN DELETE FROM strategy_frames WHERE ticker=? AND timeframe=?',
                    ('AAA', '1s')).fetchall()
                self.assertTrue(any('strategy_frames_stream' in row[-1] for row in plan))
            spool.delete_stream('AAA', '1s')
            self.assertEqual({(row.ticker, row.timeframe) for row in spool}, {('AAA', '5s'), ('BBB', '1s')})
            # Existing partial spools receive the index when reopened, without
            # resetting retained data or requiring a finished replay index.
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('DROP INDEX strategy_frames_stream')
                connection.commit()
            restored = ReplayFrameSpool(path, reset=False)
            self.assertEqual(len(list(restored)), 2)
            with closing(sqlite3.connect(path)) as connection:
                self.assertIn('strategy_frames_stream', [row[1] for row in connection.execute('PRAGMA index_list(strategy_frames)')])

    def test_dynamic_population_requires_native_session_activation(self):
        config = dict(strategy=dict(parameters=dict(historical_hod_contract=True)),
            run_plan=dict(activation=dict(watch_duration='session', watchlist_policy='not_required')),
            signal_activation=dict(signal_streams=[dict(enabled=True, occurrence_source='qmd_squeeze_episode')]))
        self.assertIsNone(_structural_recovery_projection_tickers(config, ()))
        self.assertEqual(_structural_recovery_projection_tickers(config, ('SUGP',)), ['SUGP'])
        config['run_plan']['activation']['watch_duration'] = 'episode'
        with self.assertRaisesRegex(ValueError, 'selected ticker'):
            _structural_recovery_projection_tickers(config, ())

    def test_r1_full_market_requires_source_native_session_activation(self):
        from src.trading_runtime.r1_ladder import CONTRACT
        config = dict(strategy=dict(parameters=dict(
            historical_hod_contract=True, r1_ladder_contract=CONTRACT)),
            run_plan=dict(activation=dict(
                watch_duration='session', watchlist_policy='not_required')),
            signal_activation=dict(signal_streams=[dict(
                enabled=True, occurrence_source='qmd_squeeze_episode')]))
        self.assertIsNone(_structural_recovery_projection_tickers(config, ()))
        self.assertEqual(_structural_recovery_projection_tickers(
            config, (' bbb ', 'AAA', 'aaa', '')), ['AAA', 'BBB'])
        config['run_plan']['activation']['watchlist_policy'] = 'any_selected'
        with self.assertRaisesRegex(ValueError, 'selected ticker'):
            _structural_recovery_projection_tickers(config, ())

    def test_complete_pinned_artifact_and_hash_drift(self):
        start = datetime(2026, 8, 21, 8, tzinfo=UTC)
        end = start + timedelta(hours=16)
        stream = dict(signal_stream_id='early', occurrence_source='qmd_squeeze_episode')
        event = dict(event_id='one', ticker='ABC', signal_stream_id='early',
            available_at=(start + timedelta(seconds=1)).isoformat(), event_time=(start + timedelta(seconds=1)).isoformat())
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, TRADINGML_RUNTIME_ROOT=directory):
            root = Path(directory)
            data = (json.dumps(event) + '\n').encode()
            (root / 'occurrences.jsonl').write_bytes(data)
            manifest = dict(schema_version=1, complete=True, authority='qmd_canonical_sip_squeeze_replay_v1',
                available_start=start.isoformat(), available_end=end.isoformat(), source_revision=dict(complete_for_history=True, request_complete=True),
                stream_definitions=[stream.copy()], row_count=1, occurrences_sha256=hashlib.sha256(data).hexdigest())
            path = root / 'manifest.json'
            path.write_text(json.dumps(manifest))
            stream['historical_occurrence_artifact'] = dict(manifest_path=str(path), manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            result = historical_source_native_signal_occurrences(stream, start=start, end=end)
            self.assertEqual(result['occurrences'], [event])
            with self.assertRaisesRegex(RuntimeError, 'cover'):
                historical_source_native_signal_occurrences(stream, start=start, end=end + timedelta(seconds=1))
            (root / 'occurrences.jsonl').write_bytes(data + data)
            with self.assertRaisesRegex(RuntimeError, 'repeated'):
                historical_source_native_signal_occurrences(stream, start=start, end=end)


class FullDayBarTests(IsolatedAsyncioTestCase):
    async def test_all_16_hours_are_requested_and_streamed_once(self):
        start = datetime(2026, 8, 21, 8, tzinfo=UTC)
        end = start + timedelta(hours=16)
        requests = []
        received = []
        authorities = []

        def fetch(request):
            requests.append(request)
            clock = datetime.fromisoformat(request.start)
            bar = dict(sym='ABC', timeframe='100ms', bar_start=clock.isoformat(),
                bar_end=(clock + timedelta(milliseconds=100)).isoformat(), open=4, high=4, low=4, close=4, volume=100)
            return SimpleNamespace(payload=dict(bars=[bar], indicators=[dict(bar)],
                indicator_provenance=dict(source=dict(complete_for_history=True, revision_token=clock.isoformat()))))

        async def sink(batch):
            received.extend(batch)

        with patch('src.backend.replay_run_service.qmd_product_request', side_effect=fetch):
            await _stream_historical_bar_derived_frames(ticker='ABC', timeframe='100ms', start=start, end=end,
                frame_sink=sink, authority_sink=lambda key, value: authorities.append(value), indicator_columns=('close',))
        self.assertEqual(len(requests), 15)
        self.assertEqual(len(received), 15)
        self.assertEqual(datetime.fromisoformat(requests[0].start), start)
        self.assertEqual(datetime.fromisoformat(requests[-1].end), end)
        for previous, current in zip(requests, requests[1:]):
            self.assertEqual(previous.end, current.start)
        self.assertTrue(authorities[0]['complete_for_history'])
        self.assertEqual(len(authorities[0]['chunks']), 15)
        for timeframe, expected in [('1s', 2), ('5s', 1)]:
            requests.clear()
            with patch('src.backend.replay_run_service.qmd_product_request', side_effect=fetch):
                await _stream_historical_bar_derived_frames(ticker='ABC', timeframe=timeframe, start=start, end=end,
                    frame_sink=sink, authority_sink=None, indicator_columns=('close',))
            self.assertEqual(len(requests), expected)
            self.assertEqual(datetime.fromisoformat(requests[0].start), start)
            self.assertEqual(datetime.fromisoformat(requests[-1].end), end)
