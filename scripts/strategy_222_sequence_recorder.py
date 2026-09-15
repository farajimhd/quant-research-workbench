"""Optional research recording of the backtest's native completed detector rows."""
import gzip
import hashlib
import json

from strategy_222_candle_sequences import CandleSequences


class SequenceRecorder:
    def __init__(self, controller, root):
        self.controller = controller
        self.path = root / f'{controller.run_id}-candle-sequences.jsonl.gz'
        self.file = gzip.open(self.path, 'xt', encoding='utf-8')
        self.streams = {}
        self.counts = {}
        self.original = controller._observe_episode_candle
        controller._observe_episode_candle = self.observe

    async def observe(self, frame):
        await self.original(frame)
        if frame.timeframe != '1s':
            return
        market = self.controller._candle_detector_states.get(frame.ticker, {}).get('structural_recovery')
        if not market or not market.get('row'):
            raise ValueError('Sequence recording requires the native structural detector')
        if frame.ticker not in self.streams:
            self.streams[frame.ticker] = CandleSequences(candle_seconds=1)
        stream = self.streams[frame.ticker]
        at = frame.as_of.timestamp()
        result = stream.observe(market['row'], session=market['session'], observed_at=at)
        row = market['row']
        record = dict(run_id=self.controller.run_id, symbol=frame.ticker, at=at,
            detector_sequence=row['sequence'], session=market['session'],
            candle=row['candle'], labels=row['labels'], candle_shape=row['candle_shape'], **result)
        self.file.write(json.dumps(record, allow_nan=False, separators=(',', ':'))+'\n')
        self.counts[frame.ticker] = self.counts.get(frame.ticker, 0)+1

    def close(self):
        self.controller._observe_episode_candle = self.original
        self.file.close()
        with self.path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        return dict(file=self.path.name, sha256=digest, rows=sum(self.counts.values()),
            by_symbol=self.counts, status='completed' if self.controller.status == 'completed' else 'partial',
            timeframe='1s', scope='Native completed detector rows; research features never enter strategy inputs.')
