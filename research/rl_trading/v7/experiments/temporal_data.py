"""Ordered causal windows on the sealed training-only five-minute entry grid."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import argparse
import json
import time
import numpy as np
from research.market_shards.v1 import data as shared_reader
from research.market_shards.v1.contract import origin_us, sha, digest, write_json
from research.rl_trading.v7.data import SPECS, summarize, execution_arrays, SESSION_SECONDS
from research.rl_trading.v7.experiments.predictive_data import PredictiveDataset, sampled_seconds
from research.rl_trading.v7.stage_data import DEFAULT_DEST
from src.runtime_paths import runtime_root

LENGTHS = (60, 30, 30, 16, 20, 60, 60)
SCALES = (100., 100., 50., 10., 10., 100., 100.)


def implementation():
    here = Path(__file__)
    shared = Path(shared_reader.__file__).parent
    return {str(p.relative_to(here.parents[4])): sha(p) for p in
            (here, here.with_name('predictive_data.py'), here.parent.parent/'data.py',
             shared/'data.py', shared/'source.py', shared/'contract.py')}


def sequence(window, marks, query_clocks, group, prior_days=(), day=None):
    """No imputation: native scheduled cells right-aligned, independent masks."""
    v, valid = np.asarray(window['values'], np.float64), np.asarray(window['valid'], bool)
    clocks = np.asarray(window['clocks'])
    fields = shared_reader.QUOTE_FIELDS if group >= 5 else shared_reader.TRADE_FIELDS
    if (tuple(window['fields']) != fields or v.shape != valid.shape or v.ndim != 3 or
            v.shape[1:] != (LENGTHS[group], len(fields)) or clocks.shape != v.shape[:2] or
            not np.isfinite(v).all() or np.asarray(marks).shape != (len(v),)):
        raise ValueError('Temporal window shape/field/value mismatch')
    if group == 4:
        if (not np.array_equal(clocks, np.broadcast_to(np.arange(-20, 0), clocks.shape)) or
                list(prior_days) != sorted(set(prior_days)) or any(d >= day for d in prior_days)):
            raise ValueError('Daily historical ordinal/availability contract mismatch')
    elif np.any(np.diff(clocks, axis=1) < 0) or np.any(clocks > np.asarray(query_clocks)[:, None]):
        raise ValueError('Unordered or future temporal window clock')
    out = np.zeros((len(v), 60, 14), np.float32)
    normalized = np.zeros_like(v)
    accepted = valid.copy()
    mark_ok = np.isfinite(marks) & (np.asarray(marks) > 0)
    accepted[..., :4] &= (v[..., :4] > 0) & mark_ok[:, None, None]
    normalized[..., :4] = (v[..., :4]/np.where(mark_ok, marks, 1)[:, None, None]-1)*SCALES[group]
    accepted[..., 4:] &= v[..., 4:] >= 0
    normalized[..., 4:] = np.log1p(np.maximum(v[..., 4:], 0))/10
    out[:, -LENGTHS[group]:, :len(fields)] = np.where(accepted, normalized, 0)
    out[:, -LENGTHS[group]:, 7:7+len(fields)] = accepted
    if not np.isfinite(out).all():
        raise ValueError('Nonfinite temporal normalized values')
    counts = dict(source_invalid=int((~valid).sum()), rejected_valid=int((valid & ~accepted).sum()),
                  valid=int(accepted.sum()), padding=int(len(v)*(60-LENGTHS[group])*7),
                  unavailable_quote_field=int(len(v)*LENGTHS[group]) if group >= 5 else 0)
    return out, counts


class TemporalDataset:
    def __init__(self, source_root, snapshots_root, output_root):
        self.predictive = PredictiveDataset(source_root, snapshots_root)
        self.days = list(self.predictive.days)
        self.fit_days, self.development_days = self.days[:120], self.days[120:]
        self.root = Path(output_root).resolve()
        runtime = runtime_root().resolve()
        if not runtime.is_dir() or not self.root.is_relative_to(runtime):
            raise ValueError('Temporal output requires available runtime root')
        self.binding = dict(recipe='ordered-native-windows-right-aligned-60-v1',
            predictive=self.predictive.binding, source=self.predictive.source.binding,
            implementation=implementation(), row_step=10, shape=[66,5,7,60,14],
            lengths=list(LENGTHS), price_scales=list(SCALES), specs=[list(x) for x in SPECS],
            fields=dict(trade=list(shared_reader.TRADE_FIELDS), quote=list(shared_reader.QUOTE_FIELDS)+['unavailable']),
            activity='log1p(nonnegative)/10', price='(price/current_execution_mark-1)*group_scale',
            masks='seven per-field masks; invalid and padding zero; no fill',
            daily_clock='historical ordinal -20..-1; shared available_at<=decision filter',
            summary_parity='exact stored float32 market98 for every admitted sampled candidate',
            validation_access=False)
        self.binding['contract_hash'] = digest(self.binding)
        path = self.root/'manifest.json'
        if path.exists():
            if json.loads(path.read_text()) != self.binding:
                raise ValueError('Temporal source/recipe binding mismatch')
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            if any(self.root.iterdir()): raise ValueError('Nonempty unbound temporal output')
            write_json(path, self.binding)

    def identity(self, day):
        if day not in self.days: raise ValueError('Only approved TRAINING temporal days')
        self.predictive.source._check_day(day)
        if implementation() != self.binding['implementation']:
            raise ValueError('Temporal implementation changed')
        return dict(day=day, contract_hash=self.binding['contract_hash'],
                    predictive_receipt_sha256=sha(self.predictive.root/day/'complete.json'))

    def prepare(self, day):
        identity = self.identity(day)
        target = self.root/day
        if target.exists():
            self.load(day)
            return json.loads((target/'complete.json').read_text())
        started = time.perf_counter()
        snapshots = self.predictive.load(day)
        rows = np.arange(0, len(sampled_seconds(day)), 10, dtype=np.int64)
        seconds = snapshots['seconds'][rows]
        selection = snapshots['selection'][rows]
        source = self.predictive.source
        tickers = source.items[day]['selected']
        estimated = SESSION_SECONDS*len(tickers)*49
        if estimated*1.4+512*2**20 > source.maximum_gib*2**30:
            raise MemoryError('Temporal preparation execution scratch exceeds budget')
        session = shared_reader.Session(source.root/'training.json', day, cache_bytes=512*2**20)
        clocks = origin_us(day)//1000000+np.arange(14401,72001,dtype=np.int64)
        ranked = session.selection(clocks[seconds]*1000000)
        admitted = selection >= 0
        if not np.array_equal(ranked['valid'], admitted): raise ValueError('Temporal admission parity failure')
        expected_names = np.asarray(tickers)[selection[admitted]]
        if not np.array_equal(np.asarray(ranked['tickers'])[admitted], expected_names):
            raise ValueError('Temporal selection ticker parity failure')
        execution = execution_arrays(session, tickers, clocks)
        r, s = np.nonzero(admitted)
        query_names = np.asarray(tickers)[selection[r,s]]
        query_clocks = clocks[seconds[r]]*1000000
        marks = execution['mark'][seconds[r],selection[r,s]]
        seq = np.zeros((len(rows),5,7,60,14), np.float32)
        counts = []
        for group, (resolution, side) in enumerate(SPECS):
            if not len(r):
                counts.append(dict(source_invalid=0,rejected_valid=0,valid=0,padding=0,unavailable_quote_field=0))
                continue
            window = session.windows(query_names, query_clocks, resolution, side)
            summary = summarize(window, marks, side)
            if not np.array_equal(summary, snapshots['features'][rows[r],s,group*14:(group+1)*14]):
                raise ValueError('Exact market-summary parity failure: '+str(group))
            values, count = sequence(window, marks, query_clocks, group, session.item['daily'], day)
            seq[r,s,group] = values
            counts.append(count)
        if self.identity(day) != identity: raise ValueError('Temporal authority changed during preparation')
        staging = self.root/(day+'.preparing-'+str(time.time_ns()))
        staging.mkdir()
        for name, array in (('seq',seq), ('rows',rows)):
            np.save(staging/(name+'.npy'),array,allow_pickle=False)
        receipt = dict(identity=identity, files={name:sha(staging/name) for name in ('seq.npy','rows.npy')},
            admitted_candidates=int(admitted.sum()), missing_candidates=int((~admitted).sum()),
            parity_scalar_count=int(admitted.sum()*98), parity_exact=True, groups=counts,
            elapsed_seconds=time.perf_counter()-started,
            bytes=sum((staging/name).stat().st_size for name in ('seq.npy','rows.npy')))
        write_json(staging/'complete.json',receipt)
        staging.rename(target)
        return receipt

    def load(self, day):
        identity = self.identity(day)
        target = self.root/day
        receipt = json.loads((target/'complete.json').read_text())
        if receipt['identity'] != identity or set(receipt['files']) != {'seq.npy','rows.npy'}:
            raise ValueError('Temporal receipt identity mismatch')
        arrays = {}
        for name in ('seq','rows'):
            path = target/(name+'.npy')
            if sha(path) != receipt['files'][path.name]: raise ValueError('Temporal shard hash mismatch')
            arrays[name] = np.load(path,mmap_mode='r',allow_pickle=False)
        rows, seq = arrays['rows'], arrays['seq']
        if (not np.array_equal(rows,np.arange(0,len(sampled_seconds(day)),10)) or rows.dtype!=np.int64 or
                seq.shape!=(len(rows),5,7,60,14) or seq.dtype!=np.float32 or not np.isfinite(seq).all() or
                not np.isin(seq[...,7:],[0,1]).all() or np.any(seq[...,:7][seq[...,7:]==0]!=0)):
            raise ValueError('Temporal array schema/mask mismatch')
        return arrays


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',default=DEFAULT_DEST)
    p.add_argument('--snapshots',default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    p.add_argument('--output',default='D:/TradingML/runtimes/rl-trading/v7/temporal-data-v1')
    p.add_argument('--dates',nargs='+')
    args=p.parse_args(argv)
    data=TemporalDataset(args.source,args.snapshots,args.output)
    days=args.dates or data.days
    if len(set(days))!=len(days) or any(d not in data.days for d in days):
        raise ValueError('Unique approved TRAINING dates required')
    completed=[]
    for index,day in enumerate(days):
        if (data.root/'STOP').exists():
            write_json(data.root/'status.json',dict(status='stopped',completed=completed,queued=len(days)-index,failed=0))
            return 2
        write_json(data.root/'status.json',dict(status='running',active=day,completed=completed,queued=len(days)-index-1,failed=0))
        try:
            receipt=data.prepare(day)
        except BaseException as exc:
            write_json(data.root/'status.json',dict(status='failed',active=day,completed=completed,error=repr(exc),failed=1))
            raise
        completed.append(day)
        print(json.dumps(dict(day=day,elapsed_seconds=receipt['elapsed_seconds'],bytes=receipt['bytes'],parity_exact=True)),flush=True)
    write_json(data.root/'status.json',dict(status='complete',completed=completed,queued=0,failed=0,validation_access=False))
    return 0


if __name__=='__main__': raise SystemExit(main())
