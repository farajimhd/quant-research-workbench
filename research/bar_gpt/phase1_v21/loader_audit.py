"""Bounded CPU audit of the exact full-training loader prefix, without updates."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
import argparse
import time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v11.pipeline import loader
from ..phase1_v8.runtime import preflight
from ..v5.data_v3 import load_split, close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import write_json


def describe(batch):
    frames = batch.frames.numpy()
    values = batch.grid.numpy()
    valid = batch.valid.numpy()
    groups = {}
    for frame in np.unique(frames):
        take = frames == frame
        fields = {}
        for channel in (3, 4, 9, 11, 15, 17):
            mask = valid[take, -1, channel]
            x = values[take, -1, channel][mask].astype(np.float64)
            fields[str(channel)] = dict(valid=int(mask.sum()), raw_sum=float(x.sum()),
                log1p_sum=float(np.log1p(x).sum()))
        groups[str(int(frame))] = dict(windows=int(take.sum()), last_bar=fields)
    return groups


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--batches', type=int, default=128)
    a = p.parse_args()
    if not 1 <= a.batches <= 256: raise ValueError('Bounded prefix required')
    if os.environ.get('COMPUTERNAME', '').upper() != 'DESKTOP-SAAI85T':
        raise RuntimeError('Workstation runtime authority required')
    a.device = 'cpu'; a.resume = False; preflight(a)
    a.batch_size = 8192; a.workers = 16; a.prefetch = 4; a.seed = 17
    a.preparation = 'raw_gpu'; a.shard_prefetch = 16; a.shard_prefetch_mib = 64
    torch.set_num_threads(2)
    split = load_split(Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    split = attach(split, Path(split['dataset'])); split['splits'] = {}
    source = loader(split, a, 'train', epoch=0)
    start = time.monotonic(); records = []
    try:
        for i, batch in enumerate(source):
            records.append(dict(batch=i, groups=describe(batch)))
            write_json(a.output/'progress.json', dict(batches=len(records), total=a.batches,
                seconds=time.monotonic()-start, state='auditing'))
            if len(records) >= a.batches: break
            if time.monotonic()-start > 300 or (a.output/'STOP').exists():
                raise InterruptedError('CPU audit deadline or STOP')
    finally:
        close_loader(source)
    write_json(a.output/'result.json', dict(source=a.source_revision, split_hash=split['manifest_hash'],
        seed=17, epoch=0, batch_size=a.batch_size, workers=a.workers, records=records,
        seconds=time.monotonic()-start, scope='Exact ordered full-loader prefix; CPU only; last-bar summaries are not whole-window metrics.'))
    write_json(a.output/'progress.json', dict(batches=len(records), total=a.batches,
        seconds=time.monotonic()-start, state='complete'))


if __name__ == '__main__': main()
