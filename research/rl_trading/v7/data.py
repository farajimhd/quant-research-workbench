"""Training-only causal summaries over the sealed shared ARTE scanner shards.

The session ticker union is storage scope only. Each observation contains the
five admitted tickers at that completed clock; execution-only tail data never
enters feature windows. Missing values are zero with explicit validity channels.
"""
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import time

import numpy as np

from research.market_shards.v1.contract import CONTRACT, TRADE_WINDOWS, digest, origin_us, sha
from research.market_shards.v1.data import Session
from research.market_shards.v1 import data as shared_data
from src.runtime_paths import runtime_root

DECISION_SECONDS = 19800
SESSION_SECONDS = 57600
SCANNER_SLOTS = 5
RECIPE = 'v7-causal-multiscale-summary-98-v1'
TRADE_NAMES = ('close_mark_relative', 'first_last_return', 'range_mark_relative',
               'log_volume_sum', 'log_trade_count_sum', 'close_valid_fraction', 'activity_valid_fraction')
QUOTE_NAMES = ('close_mark_relative', 'first_last_return', 'range_mark_relative',
               'log_latest_close_size', 'log_event_count_sum', 'close_valid_fraction', 'activity_valid_fraction')
SPECS = tuple((resolution, None) for resolution in TRADE_WINDOWS) + ((1000, 'bid'), (1000, 'ask'))
FEATURE_NAMES = tuple(f'{side or "trade"}.{resolution}ms.{name}{suffix}'
    for resolution, side in SPECS for suffix in ('', '.valid')
    for name in (QUOTE_NAMES if side else TRADE_NAMES))
EXECUTION_KEYS = ('mark', 'bid', 'ask', 'quote_us', 'bid_size', 'ask_size', 'quote_valid')


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@dataclass
class PreparedSession:
    day: str
    tickers: list
    clocks: np.ndarray
    selection: np.ndarray
    features: np.ndarray
    feature_names: tuple
    execution: dict
    binding: dict


def summarize(window, marks, side=None):
    """Seven summaries and masks. Sums exclude missing cells and expose coverage.

    Close/size use the latest valid scheduled cell, never a future cell. Returns
    require two valid closes; high/low range uses only paired valid extremes.
    Fractions use the full scheduled window, including unavailable early history.
    """
    values = np.asarray(window['values'], dtype=np.float64)
    valid = np.asarray(window['valid'], dtype=bool)
    fields = {name: index for index, name in enumerate(window['fields'])}
    if values.ndim != 3 or valid.shape != values.shape or not np.isfinite(values).all():
        raise ValueError('Invalid shared feature-window values or masks')
    n, length, _ = values.shape
    marks = np.asarray(marks, dtype=np.float64)
    if marks.shape != (n,) or not length:
        raise ValueError('Feature normalization shape mismatch')
    columns = np.arange(length)[None, :]
    rows = np.arange(n)
    close = values[..., fields['close']]
    close_valid = valid[..., fields['close']] & (close > 0)
    count = close_valid.sum(axis=1)
    first_index = np.argmax(close_valid, axis=1)
    last_index = np.maximum(np.where(close_valid, columns, -1).max(axis=1), 0)
    first, last = close[rows, first_index], close[rows, last_index]
    mark_valid = np.isfinite(marks) & (marks > 0)
    high, low = values[..., fields['high']], values[..., fields['low']]
    paired = valid[..., fields['high']] & valid[..., fields['low']] & (high >= low) & (low > 0)
    hi = np.where(paired, high, -np.inf).max(axis=1)
    lo = np.where(paired, low, np.inf).min(axis=1)
    activity_names = ('close_size', 'event_count') if side else ('volume', 'trade_count')
    activity = [values[..., fields[key]] for key in activity_names]
    activity_valid = [valid[..., fields[key]] & (value >= 0) for key, value in zip(activity_names, activity)]
    activity_sum = [np.where(mask, value, 0).sum(axis=1) for mask, value in zip(activity_valid, activity)]
    if side:
        size_index = np.maximum(np.where(activity_valid[0], columns, -1).max(axis=1), 0)
        activity_sum[0] = activity[0][rows, size_index]
    output = np.zeros((n, 7), dtype=np.float64)
    masks = np.zeros((n, 7), dtype=bool)
    masks[:, 0] = (count > 0) & mark_valid
    masks[:, 1] = count >= 2
    masks[:, 2] = paired.any(axis=1) & mark_valid
    masks[:, 3] = activity_valid[0].any(axis=1)
    masks[:, 4] = activity_valid[1].any(axis=1)
    masks[:, 5:] = True
    output[:, 0] = last / np.where(mark_valid, marks, 1.) - 1.
    output[:, 1] = last / np.where(first > 0, first, 1.) - 1.
    output[:, 2] = (np.where(masks[:, 2], hi, 0.) - np.where(masks[:, 2], lo, 0.)) / np.where(mark_valid, marks, 1.)
    output[:, 3] = np.log1p(np.maximum(activity_sum[0], 0.))
    output[:, 4] = np.log1p(np.maximum(activity_sum[1], 0.))
    output[:, 5] = count / length
    output[:, 6] = (activity_valid[0] & activity_valid[1]).mean(axis=1)
    output = np.where(masks, output, 0.)
    if not np.isfinite(output).all():
        raise ValueError('Nonfinite summarized feature')
    return np.concatenate((output, masks.astype(np.float64)), axis=1).astype(np.float32)


def execution_arrays(session, tickers, clocks):
    """Chronological mark/book carry; an invalid new book overwrites an old one."""
    n = len(tickers)
    result = {key: np.zeros((SESSION_SECONDS, n), dtype=np.int64 if key == 'quote_us' else
              bool if key == 'quote_valid' else np.float64) for key in EXECUTION_KEYS}
    lookup = {ticker: i for i, ticker in enumerate(tickers)}
    last = np.zeros((n, 7), dtype=np.float64)
    columns = ('mark', 'bid', 'ask', 'quote_us', 'bid_size', 'ask_size', 'book_valid')
    pages = 0
    for page_index, frame in enumerate(session.execution_pages()):
        if page_index >= 16 or frame is None:
            raise ValueError('Missing or extra execution page')
        pages += 1
        start, end = page_index * 3600, (page_index + 1) * 3600
        updates = np.full((3600, n, 7), np.nan, dtype=np.float64)
        if frame.height:
            stamps = frame['time_us'].to_numpy().astype(np.int64)
            if np.any(stamps % 1000000):
                raise ValueError('Execution clock is not a completed second')
            rows = stamps // 1000000 - clocks[0] - start
            names = frame['ticker'].to_list()
            if set(names) - lookup.keys():
                raise ValueError('Execution ticker outside sealed union')
            cols = np.asarray([lookup[t] for t in names], dtype=np.int64)
            if np.any(rows < 0) or np.any(rows >= 3600) or len(np.unique(rows*n+cols)) != len(rows):
                raise ValueError('Execution page ordering, scope or duplicate key mismatch')
            values = frame.select(columns).to_numpy().astype(np.float64)
            if not np.isfinite(values).all() or np.any(values < 0) or np.any(values[:, 3] > stamps):
                raise ValueError('Invalid execution values or future quote timestamp')
            marked = values[:, 0] > 0
            updates[rows[marked], cols[marked], 0] = values[marked, 0]
            quoted = values[:, 3] > 0
            updates[rows[quoted], cols[quoted], 1:] = values[quoted, 1:]
        bank = np.concatenate((last[None], updates), axis=0)
        indices = np.maximum.accumulate(np.where(np.isfinite(bank), np.arange(3601)[:, None, None], 0), axis=0)
        carried = np.take_along_axis(bank, indices, axis=0)[1:]
        last = carried[-1].copy()
        for index, name in enumerate(columns[:-1]):
            result[name][start:end] = carried[..., index]
        result['quote_valid'][start:end] = ((carried[..., 6] > 0) & (carried[..., 1] > 0) &
            (carried[..., 2] >= carried[..., 1]) & (carried[..., 3] > 0) &
            (carried[..., 3] <= clocks[start:end, None]*1000000))
    if pages != 16:
        raise ValueError('Execution tail must contain all sixteen hourly pages')
    return result


class TrainingDataset:
    def __init__(self, root, *, maximum_gib=4.):
        self.root = Path(root).resolve()
        self.manifest = _json(self.root/'training.json')
        complete = _json(self.root/'complete.json')
        source = _json(self.root/'source.json')
        if (complete.get('status') not in ('complete', 'complete_with_blocks') or
                complete['manifests'].get('training') != sha(self.root/'training.json') or
                self.manifest.get('role') != 'training' or
                self.manifest.get('version') != CONTRACT['version'] or
                digest(self.manifest['contract']) != digest(CONTRACT) or
                source.get('hash') != digest({k: v for k, v in source.items() if k != 'hash'}) or
                source['hash'] != self.manifest['source_hash'] or source['hash'] != complete['source_hash'] or
                self.manifest['shard_seals'] != complete['shard_seals']):
            raise ValueError('Training dataset manifest/source seal mismatch')
        self.days = [row['day'] for row in self.manifest['sessions']]
        if len(self.days) != 150 or self.days != sorted(set(self.days)) or any(date.fromisoformat(d).isoformat() != d for d in self.days):
            raise ValueError('Require exactly 150 unique chronological training dates')
        if not np.isfinite(maximum_gib) or maximum_gib <= 0:
            raise ValueError('Positive bounded preparation memory required')
        self.maximum_gib = maximum_gib
        self.items = {row['day']: row for row in self.manifest['sessions']}
        scanner_hashes = {}
        for day, item in self.items.items():
            if (not item['selected'] or item['selected'] != sorted(set(item['selected'])) or
                    item['daily'] != sorted(set(item['daily'])) or item['hourly'] != sorted(set(item['hourly'])) or
                    any(d >= day for d in item['daily']) or any(d > day for d in item['hourly'])):
                raise ValueError('Invalid storage union or future context reference')
            scanner = _json(self.root/'scanner'/day/'complete.json')
            if scanner['source_hash'] != source['hash'] or scanner['day'] != day or scanner['selected'] != item['selected']:
                raise ValueError('Scanner metadata binding mismatch')
            scanner_hashes[day] = sha(self.root/'scanner'/day/'complete.json')
        self.binding = dict(recipe=RECIPE, training_sha256=sha(self.root/'training.json'),
            source_sha256=sha(self.root/'source.json'), complete_sha256=sha(self.root/'complete.json'),
            source_hash=source['hash'], scanner_sha256=scanner_hashes,
            feature_names=list(FEATURE_NAMES), implementation_sha256=sha(Path(__file__)),
            shared_implementation={name: sha(Path(shared_data.__file__).with_name(name))
                                   for name in ('data.py', 'contract.py', 'source.py')})

    def _check_day(self, day):
        if day not in self.items:
            raise ValueError('Only manifest TRAINING dates may be loaded')
        for filename, key in (('training.json', 'training_sha256'), ('source.json', 'source_sha256'), ('complete.json', 'complete_sha256')):
            if sha(self.root/filename) != self.binding[key]:
                raise ValueError('Dataset metadata changed after binding')
        if sha(self.root/'scanner'/day/'complete.json') != self.binding['scanner_sha256'][day]:
            raise ValueError('Scanner metadata changed after binding')

    def load(self, day, cache_root=None):
        self._check_day(day)
        identity = dict(dataset=self.binding, day=day)
        target = None
        if cache_root is not None:
            cache_root = Path(cache_root).resolve()
            runtime = runtime_root().resolve()
            if not runtime.is_dir() or not cache_root.is_relative_to(runtime):
                raise ValueError('Prepared cache must stay under available runtime root')
            target = cache_root/day
            if target.exists():
                prepared = _read_cache(target, identity)
                if prepared.tickers != self.items[day]['selected']:
                    raise ValueError('Prepared cache ticker identity differs from training manifest')
                return prepared
        tickers = list(self.items[day]['selected'])
        estimated = SESSION_SECONDS*len(tickers)*49 + DECISION_SECONDS*5*len(FEATURE_NAMES)*4
        if estimated*1.4 + 512*2**20 > self.maximum_gib*2**30:
            raise MemoryError('Prepared session exceeds declared memory budget')
        session = Session(self.root/'training.json', day, cache_bytes=512*2**20)
        clocks = origin_us(day)//1000000 + np.arange(14401, 72001, dtype=np.int64)
        ranked = session.selection(clocks[:DECISION_SECONDS]*1000000)
        names, valid = np.asarray(ranked['tickers']), np.asarray(ranked['valid'], dtype=bool)
        if names.shape != (DECISION_SECONDS, 5) or valid.shape != names.shape:
            raise ValueError('Scanner selection shape mismatch')
        selection = np.full(names.shape, -1, dtype=np.int32)
        lookup = {ticker: index for index, ticker in enumerate(tickers)}
        if set(names[valid]) - lookup.keys():
            raise ValueError('Scanner admitted ticker outside storage union')
        for ticker, index in lookup.items():
            selection[valid & (names == ticker)] = index
        for left in range(5):
            for right in range(left + 1, 5):
                if np.any((selection[:, left] >= 0) & (selection[:, left] == selection[:, right])):
                    raise ValueError('Duplicate scanner membership')
        execution = execution_arrays(session, tickers, clocks)
        features = np.zeros((DECISION_SECONDS, 5, len(FEATURE_NAMES)), dtype=np.float32)
        for begin in range(0, DECISION_SECONDS, 512):
            end = min(begin + 512, DECISION_SECONDS)
            rows, slots = np.nonzero(selection[begin:end] >= 0)
            rows = rows + begin
            indices = selection[rows, slots]
            if not len(rows):
                continue
            query_names = np.asarray(tickers, dtype=object)[indices]
            query_clocks = clocks[rows]*1000000
            marks = execution['mark'][rows, indices]
            for index, (resolution, side) in enumerate(SPECS):
                window = session.windows(query_names, query_clocks, resolution, side)
                features[rows, slots, index*14:(index+1)*14] = summarize(window, marks, side)
        self._check_day(day)
        prepared = PreparedSession(day, tickers, clocks, selection, features, FEATURE_NAMES, execution, identity)
        _validate(prepared)
        if target is not None:
            _write_cache(target, prepared)
            return _read_cache(target, identity)
        return prepared


def _validate(prepared):
    n = len(prepared.tickers)
    if (prepared.clocks.shape != (SESSION_SECONDS,) or np.any(np.diff(prepared.clocks) != 1) or
            prepared.selection.shape != (DECISION_SECONDS, 5) or prepared.selection.dtype != np.int32 or
            np.any(prepared.selection < -1) or np.any(prepared.selection >= n) or
            prepared.features.shape != (DECISION_SECONDS, 5, len(FEATURE_NAMES)) or
            prepared.features.dtype != np.float32 or not np.isfinite(prepared.features).all() or
            np.any(prepared.features[prepared.selection < 0] != 0)):
        raise ValueError('Prepared observation contract mismatch')
    for key in EXECUTION_KEYS:
        value = prepared.execution[key]
        if value.shape != (SESSION_SECONDS, n) or not np.isfinite(value).all() or np.any(value < 0):
            raise ValueError('Prepared execution contract mismatch: '+key)
    for value in (prepared.clocks, prepared.selection, prepared.features, *prepared.execution.values()):
        value.flags.writeable = False


def _write_cache(target, prepared):
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name+'.preparing-'+str(time.time_ns()))
    staging.mkdir()
    arrays = dict(clocks=prepared.clocks, selection=prepared.selection, features=prepared.features,
                  **{'execution_'+key: value for key, value in prepared.execution.items()})
    hashes = {}
    for key, value in arrays.items():
        path = staging/(key+'.npy')
        np.save(path, value, allow_pickle=False)
        hashes[path.name] = sha(path)
    receipt = dict(identity=prepared.binding, tickers=prepared.tickers, files=hashes)
    (staging/'complete.json').write_text(json.dumps(receipt, sort_keys=True, allow_nan=False), encoding='utf-8')
    staging.rename(target)


def _read_cache(target, identity):
    receipt = _json(target/'complete.json')
    expected = {'clocks', 'selection', 'features', *('execution_'+key for key in EXECUTION_KEYS)}
    if receipt['identity'] != identity or set(receipt['files']) != {key+'.npy' for key in expected}:
        raise ValueError('Prepared cache source/recipe binding mismatch')
    arrays = {}
    for key in expected:
        path = target/(key+'.npy')
        if sha(path) != receipt['files'][path.name]:
            raise ValueError('Prepared cache hash mismatch')
        arrays[key] = np.load(path, mmap_mode='r', allow_pickle=False)
    prepared = PreparedSession(identity['day'], receipt['tickers'], arrays['clocks'], arrays['selection'],
        arrays['features'], FEATURE_NAMES, {key: arrays['execution_'+key] for key in EXECUTION_KEYS}, identity)
    _validate(prepared)
    return prepared
