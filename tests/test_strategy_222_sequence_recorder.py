import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_sequence_recorder import SequenceRecorder
from src.market_engine.structural_detector import StructuralDetector


@pytest.mark.parametrize('status', ['completed', 'stopped'])
def test_records_native_completed_rows_and_restores_callback(tmp_path, status):
    asyncio.run(record_native(tmp_path, status))


async def record_native(tmp_path, status):
    engine = StructuralDetector()
    controller = SimpleNamespace(run_id='test-run', status=status, _candle_detector_states={})
    calls = []
    async def original(frame):
        calls.append(frame.timeframe)
        if frame.timeframe == '1s':
            end = frame.as_of.timestamp()
            row = engine.observe(dict(time=end-1, end=end, open=10., high=10.3, low=9.9, close=10.1))
            controller._candle_detector_states[frame.ticker] = dict(structural_recovery=dict(session='a', row=row))
    controller._observe_episode_candle = original
    recorder = SequenceRecorder(controller, tmp_path)
    for i in range(1, 7):
        await controller._observe_episode_candle(SimpleNamespace(ticker='TEST', timeframe='1s', as_of=datetime.fromtimestamp(i, timezone.utc)))
    await controller._observe_episode_candle(SimpleNamespace(ticker='TEST', timeframe='5s'))
    result = recorder.close()
    assert controller._observe_episode_candle is original
    assert calls == ['1s']*6+['5s']
    assert result['rows'] == 6
    assert result['status'] == ('completed' if status == 'completed' else 'partial')
    path = tmp_path/result['file']
    assert result['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    with gzip.open(path, 'rt') as source:
        rows = [json.loads(line) for line in source]
    assert [r['at'] for r in rows] == list(range(1, 7))
    assert rows[0]['features'] == {}
    assert rows[-1]['evidence']['complete_windows'] == [3, 5]
    assert rows[-1]['labels'] == controller._candle_detector_states['TEST']['structural_recovery']['row']['labels']


def test_missing_native_detector_fails_explicitly(tmp_path):
    asyncio.run(missing_native(tmp_path))


async def missing_native(tmp_path):
    async def original(frame):
        pass
    controller = SimpleNamespace(run_id='missing', status='failed',
        _candle_detector_states={}, _observe_episode_candle=original)
    recorder = SequenceRecorder(controller, tmp_path)
    try:
        with pytest.raises(ValueError, match='native structural detector'):
            await controller._observe_episode_candle(SimpleNamespace(ticker='TEST', timeframe='1s'))
    finally:
        result = recorder.close()
    assert result['status'] == 'partial'
    assert result['rows'] == 0
