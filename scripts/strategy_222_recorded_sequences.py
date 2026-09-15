"""Read verified native sequence recordings, retaining only requested windows."""
from bisect import bisect_right
from collections import Counter, defaultdict
import gzip
import json
from math import isfinite
from pathlib import Path
import re

from strategy_222_supervised_research import digest


def recording(manifest, run_id):
    data = json.loads(manifest.read_text())
    matches = [r for r in data['trials'] if r['run_id'] == run_id]
    if len(matches) != 1 or matches[0]['status'] != 'completed':
        raise ValueError('Requires one completed recording trial')
    receipt = matches[0]['candle_sequences']
    if receipt['status'] != 'completed' or receipt['timeframe'] != '1s':
        raise ValueError('Incomplete native 1s recording')
    if Path(receipt['file']).name != receipt['file']:
        raise ValueError('Recording filename must be local to its manifest')
    path = manifest.parent / receipt['file']
    if digest(path) != receipt['sha256']:
        raise ValueError('Recording hash changed')
    return path, receipt


class RecordedSequences:
    def __init__(self, path, receipt, run_id, bounds):
        self.streams = defaultdict(list)
        counts = Counter()
        previous = {}
        with gzip.open(path, 'rt', encoding='utf-8') as source:
            for line in source:
                row = json.loads(line)
                at, symbol, evidence = row['at'], row['symbol'], row['evidence']
                if (row['run_id'] != run_id or not isfinite(at)
                        or at != row['candle']['end'] or at != evidence['through']
                        or at != evidence['observed_at'] or evidence['candle_seconds'] != 1
                        or at <= previous.get(symbol, float('-inf'))):
                    raise ValueError('Invalid native sequence clock or identity')
                for key, value in row['features'].items():
                    match = re.match(r'^candles_(\d+)\.', key)
                    if (not match or int(match[1]) not in evidence['complete_windows']
                            or type(value) not in (int, float) or not isfinite(value)):
                        raise ValueError('Invalid or incomplete sequence feature')
                previous[symbol] = at
                counts[symbol] += 1
                if any(start <= at <= end for start, end in bounds.get(symbol, ())):
                    self.streams[symbol].append((at, row['features'], evidence))
        if dict(counts) != receipt['by_symbol'] or sum(counts.values()) != receipt['rows']:
            raise ValueError('Recording coverage differs from receipt')

    def at(self, symbol, cutoff, maximum_age=2.):
        stream = self.streams.get(symbol, [])
        index = bisect_right(stream, cutoff, key=lambda row: row[0])-1
        if index < 0 or cutoff-stream[index][0] > maximum_age:
            return {}, dict(status='missing_fresh_native_sequence')
        at, features, evidence = stream[index]
        return dict(features), dict(evidence, status='measured', source_age_seconds=cutoff-at)


def complete_sparse_labels(samples):
    """A zero denotes absence in a complete window, never missing history."""
    names = {key for sample in samples for key in sample.get('features', {})
        if key.startswith('candles_') and ('.label_fraction.' in key or '.movement_transition_fraction.' in key)}
    for sample in samples:
        evidence = sample.get('sequence_authority') or {}
        if evidence.get('status') != 'measured':
            continue
        complete = evidence['complete_windows']
        for key in names:
            window = int(key.split('.', 1)[0].removeprefix('candles_'))
            if window in complete:
                sample['features'].setdefault(key, 0.)
