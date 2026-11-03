"""Sparse causal inputs and separate future-mark targets, TRAINING dates only.

Targets are supervised price-return labels, never observations or realized account
profit. Marks retain the shared execution reader's chronological forward fill.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import argparse
import hashlib
import json
import time
from types import SimpleNamespace

import numpy as np

from research.market_shards.v1.contract import digest, origin_us, sha, write_json
from research.market_shards.v1.data import Session
from research.rl_trading.v7 import data as market_data
from research.rl_trading.v7.config import Config
from research.rl_trading.v7.data import TrainingDataset, SPECS, summarize, execution_arrays
from research.rl_trading.v7.experiments import cost_aware
from research.rl_trading.v7.experiments.cost_aware import current_costs, COST_FIELDS
from src.runtime_paths import runtime_root

RECIPE = 'training-only-sparse-forward-mark-returns-v1'
HORIZONS = (60, 300, 900)
STRIDE = 30
ARRAY_NAMES = ('features', 'candidate_valid', 'account', 'selection', 'seconds', 'y', 'target_valid')
FEATURE_NAMES = market_data.FEATURE_NAMES + tuple('cost.'+name+suffix
    for suffix in ('', '.valid') for name in COST_FIELDS)


def sampled_seconds(day):
    offset = int.from_bytes(hashlib.sha256(day.encode('ascii')).digest()[:8], 'big') % STRIDE
    return np.arange(offset, market_data.DECISION_SECONDS, STRIDE, dtype=np.int64)


def forward_targets(mark, seconds, selection):
    """Current admission only: no future scanner or quote-validity conditioning."""
    shape = (*selection.shape, len(HORIZONS))
    y = np.zeros(shape, dtype=np.float32)
    valid = np.zeros(shape, dtype=bool)
    rows, slots = np.nonzero(selection >= 0)
    tickers = selection[rows, slots]
    current = mark[seconds[rows], tickers]
    for column, horizon in enumerate(HORIZONS):
        future_second = seconds[rows] + horizon
        in_range = future_second < len(mark)
        future = mark[np.minimum(future_second, len(mark)-1), tickers]
        ok = in_range & np.isfinite(current) & (current > 0) & np.isfinite(future) & (future > 0)
        target = np.zeros(len(rows), dtype=np.float64)
        target[ok] = 100*(np.log(future[ok])-np.log(current[ok]))
        if not np.isfinite(target).all():
            raise ValueError('Nonfinite unbounded forward-return target')
        y[rows, slots, column] = target
        valid[rows, slots, column] = ok
    return y, valid


def snapshot_arrays(dataset, day):
    dataset._check_day(day)  # Reject nontraining dates before Session or market reads.
    tickers = list(dataset.items[day]['selected'])
    seconds = sampled_seconds(day)
    clocks = origin_us(day)//1000000 + np.arange(14401, 72001, dtype=np.int64)
    estimated = market_data.SESSION_SECONDS*len(tickers)*49
    if estimated*1.4 + 512*2**20 > dataset.maximum_gib*2**30:
        raise MemoryError('Snapshot execution scratch exceeds preparation budget')
    session = Session(dataset.root/'training.json', day, cache_bytes=512*2**20)
    ranked = session.selection(clocks[seconds]*1000000)
    names, admitted = np.asarray(ranked['tickers']), np.asarray(ranked['valid'], dtype=bool)
    if names.shape != (len(seconds), 5) or admitted.shape != names.shape:
        raise ValueError('Sparse scanner dimensions mismatch')
    selection = np.full(names.shape, -1, dtype=np.int32)
    lookup = {ticker: i for i, ticker in enumerate(tickers)}
    if set(names[admitted]) - lookup.keys():
        raise ValueError('Scanner ticker outside training storage union')
    for ticker, i in lookup.items():
        selection[admitted & (names == ticker)] = i
    for left in range(5):
        for right in range(left+1, 5):
            if np.any((selection[:, left] >= 0) & (selection[:, left] == selection[:, right])):
                raise ValueError('Duplicate admitted candidate')
    execution = execution_arrays(session, tickers, clocks)
    features = np.zeros((len(seconds), 5, 112), dtype=np.float32)
    for begin in range(0, len(seconds), 512):
        end = min(begin+512, len(seconds))
        rows, slots = np.nonzero(selection[begin:end] >= 0)
        rows += begin
        indices = selection[rows, slots]
        if not len(rows):
            continue
        query_names = np.asarray(tickers, dtype=object)[indices]
        query_clocks = clocks[seconds[rows]]*1000000
        marks = execution['mark'][seconds[rows], indices]
        for group, (resolution, side) in enumerate(SPECS):
            window = session.windows(query_names, query_clocks, resolution, side)
            features[rows, slots, group*14:(group+1)*14] = summarize(window, marks, side)
    cfg = Config()
    flat = SimpleNamespace(e=execution, config=cfg, cash=cfg.initial_cash,
                           used={}, session=SimpleNamespace(clocks=clocks), t=0)
    # Account-dependent costs are computed for an explicitly flat initial account.
    for row, second in enumerate(seconds):
        flat.t = int(second)
        for slot, ticker in enumerate(selection[row]):
            if ticker >= 0:
                features[row, slot, 98:] = current_costs(flat, int(ticker))
    account = np.zeros((len(seconds), 8), dtype=np.float32)
    account[:, 0] = 1.
    account[:, 3] = seconds / market_data.SESSION_SECONDS
    y, target_valid = forward_targets(execution['mark'], seconds, selection)
    dataset._check_day(day)
    arrays = dict(features=features, candidate_valid=selection >= 0, account=account,
                  selection=selection, seconds=seconds, y=y, target_valid=target_valid)
    validate_arrays(arrays, day)
    return arrays


def validate_arrays(arrays, day):
    seconds = sampled_seconds(day)
    n = len(seconds)
    shapes = dict(features=(n, 5, 112), candidate_valid=(n, 5), account=(n, 8),
                  selection=(n, 5), seconds=(n,), y=(n, 5, 3), target_valid=(n, 5, 3))
    dtypes = dict(features=np.float32, candidate_valid=np.bool_, account=np.float32,
                  selection=np.int32, seconds=np.int64, y=np.float32, target_valid=np.bool_)
    if set(arrays) != set(ARRAY_NAMES):
        raise ValueError('Snapshot array keys mismatch')
    for name, shape in shapes.items():
        value = arrays[name]
        if value.shape != shape or value.dtype != dtypes[name] or not np.isfinite(value).all():
            raise ValueError('Snapshot array contract mismatch: '+name)
    if (not np.array_equal(arrays['seconds'], seconds) or
            not np.array_equal(arrays['candidate_valid'], arrays['selection'] >= 0) or
            np.any(arrays['features'][~arrays['candidate_valid']] != 0) or
            np.any(arrays['target_valid'] & ~arrays['candidate_valid'][..., None]) or
            np.any(arrays['y'][~arrays['target_valid']] != 0)):
        raise ValueError('Snapshot clock, admission or missing-target mismatch')


class PredictiveDataset:
    def __init__(self, source_root, output_root):
        self.source = TrainingDataset(source_root)
        self.days = list(self.source.days)
        self.fit_days, self.development_days = self.days[:120], self.days[120:]
        self.root = Path(output_root).resolve()
        runtime = runtime_root().resolve()
        if not runtime.is_dir() or not self.root.is_relative_to(runtime):
            raise ValueError('Predictive artifacts require the available runtime root')
        code_root = Path(market_data.__file__).parent
        self.binding = dict(recipe=RECIPE, source=self.source.binding, stride_seconds=STRIDE,
            offset='big-endian first-eight SHA256(day ASCII) bytes modulo 30',
            horizons_seconds=list(HORIZONS), features=list(FEATURE_NAMES),
            cost_config=Config().manifest(), flat_account=True, holding='zero; not persisted',
            target='100 * (log(future_mark) - log(current_mark)); not account reward',
            target_validity='admitted now and finite positive current/future marks only',
            mark_semantics='shared chronological forward-filled mark; no freshness or future-membership filter',
            clipping=False, row_dropping=False, validation_access=False,
            fit_days=self.fit_days, development_days=self.development_days,
            development_scope='last30 of approved150 TRAINING dates; external38 remain unopened',
            implementation={
                'predictive_data.py': sha(Path(__file__)), 'cost_aware.py': sha(Path(cost_aware.__file__)),
                'config.py': sha(code_root/'config.py'), 'fees.py': sha(code_root.parent/'v2'/'fees.py')})
        self.binding['contract_hash'] = digest(self.binding)
        manifest = self.root/'manifest.json'
        if manifest.exists():
            if json.loads(manifest.read_text()) != self.binding:
                raise ValueError('Predictive dataset source/recipe contract mismatch')
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            if any(self.root.iterdir()):
                raise ValueError('Refuse nonempty unbound predictive output')
            write_json(manifest, self.binding)

    def _identity(self, day):
        self.source._check_day(day)
        return dict(contract_hash=self.binding['contract_hash'], day=day,
                    tickers=list(self.source.items[day]['selected']))

    def prepare(self, day):
        identity = self._identity(day)
        target = self.root/day
        if target.exists():
            self.load(day)
            return json.loads((target/'complete.json').read_text())
        began = time.perf_counter()
        arrays = snapshot_arrays(self.source, day)
        staging = self.root/(day+'.preparing-'+str(time.time_ns()))
        staging.mkdir()
        files = {}
        for name, values in arrays.items():
            path = staging/(name+'.npy')
            np.save(path, values, allow_pickle=False)
            files[path.name] = sha(path)
        candidate_count = int(arrays['candidate_valid'].sum())
        valid = arrays['target_valid'].sum(axis=(0, 1)).tolist()
        receipt = dict(identity=identity, files=files, rows=len(arrays['seconds']),
            candidate_slots=arrays['candidate_valid'].size, admitted_candidates=candidate_count,
            target_valid_counts=valid, target_missing_admitted_counts=[candidate_count-v for v in valid],
            elapsed_seconds=time.perf_counter()-began)
        write_json(staging/'complete.json', receipt)
        staging.rename(target)
        return receipt

    def load(self, day):
        identity = self._identity(day)
        target = self.root/day
        receipt = json.loads((target/'complete.json').read_text())
        if receipt['identity'] != identity or set(receipt['files']) != {n+'.npy' for n in ARRAY_NAMES}:
            raise ValueError('Predictive shard binding mismatch')
        arrays = {}
        for name in ARRAY_NAMES:
            path = target/(name+'.npy')
            if sha(path) != receipt['files'][path.name]:
                raise ValueError('Predictive shard hash mismatch: '+name)
            arrays[name] = np.load(path, mmap_mode='r', allow_pickle=False)
        validate_arrays(arrays, day)
        if np.any(arrays['selection'] >= len(identity['tickers'])):
            raise ValueError('Predictive ticker identity outside bound storage union')
        return arrays

    def iter_days(self, split='fit'):
        if split not in ('fit', 'development'):
            raise ValueError('Only fit/development within TRAINING are supported')
        for day in self.fit_days if split == 'fit' else self.development_days:
            yield day, self.load(day)


def verify_first_day(dataset):
    """Exact parity against the full causal adapter, including flat cost inputs."""
    day = dataset.days[0]
    sparse = dataset.load(day)
    full = dataset.source.load(day)
    env = cost_aware.CostEnvironment(full, Config())
    chosen = np.unique(np.linspace(0, len(sparse['seconds'])-1, 9, dtype=int))
    for row in chosen:
        env.t = int(sparse['seconds'][row])
        observation = env.observe()
        for name in ('features', 'candidate_valid', 'account'):
            if not np.array_equal(observation[name], sparse[name][row]):
                raise ValueError('Sparse/full current-input parity failed: '+name)
    report = dict(day=day, sampled_clocks=len(chosen), exact=True, validation_access=False)
    write_json(dataset.root/'first-day-parity.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--days', nargs='+')
    parser.add_argument('--verify-first', action='store_true')
    args = parser.parse_args(argv)
    dataset = PredictiveDataset(args.source, args.output)
    days = args.days or dataset.days
    if len(set(days)) != len(days) or any(day not in dataset.days for day in days):
        raise ValueError('Only unique approved TRAINING days may be prepared')
    completed = []
    try:
        for day in days:
            if (dataset.root/'STOP').exists():
                write_json(dataset.root/'status.json', dict(status='stopped', completed=completed))
                return 2
            write_json(dataset.root/'status.json', dict(status='preparing', active=day,
                completed=completed, queued=len(days)-len(completed)-1))
            receipt = dataset.prepare(day)
            completed.append(day)
            print(json.dumps(dict(day=day, rows=receipt['rows'], admitted=receipt['admitted_candidates'],
                target_valid=receipt['target_valid_counts'], seconds=receipt['elapsed_seconds'])), flush=True)
            if args.verify_first and day == dataset.days[0]:
                print(json.dumps(verify_first_day(dataset)), flush=True)
        write_json(dataset.root/'status.json', dict(status='complete', completed=completed,
                                                   validation_access=False))
    except BaseException as exc:
        write_json(dataset.root/'status.json', dict(status='failed', completed=completed, error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
