import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from src.backend.historical_signal_occurrence_service import historical_source_native_signal_occurrences
from src.backend.replay_run_service import _stream_historical_bar_derived_frames


class ArtifactTests(TestCase):
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
        self.assertEqual(len(requests), 32)
        self.assertEqual(len(received), 32)
        self.assertEqual(datetime.fromisoformat(requests[0].start), start)
        self.assertEqual(datetime.fromisoformat(requests[-1].end), end)
        for previous, current in zip(requests, requests[1:]):
            self.assertEqual(previous.end, current.start)
        self.assertTrue(authorities[0]['complete_for_history'])
        self.assertEqual(len(authorities[0]['chunks']), 32)
