"""Paired FP32/FP64 coordinate assembly evaluation; no checkpoint changes."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
from ..phase0_v3.experiment import cache
from ..phase1_v20.encoders import restore
from ..phase1_v11.data import verify
from ..phase1_v11.transforms import decode
from ..phase1_v12.model import positions
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash, write_json
from .raw_metrics import Metrics, scalars


def precise_logs(code):
    c = code.double()
    c = torch.stack([c[..., j].clamp_min(0) if j%6 in (2, 3, 4, 5) else c[..., j]
                     for j in range(18)], -1)
    return decode(c, c.new_zeros(len(c), 9), constrain=False)


@torch.no_grad()
def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--checkpoint', type=Path, required=True)
    a = p.parse_args(); a.device = 'cuda'; a.resume = False; preflight(a)
    torch.set_num_threads(4); torch.set_float32_matmul_precision('highest')
    root = Path('D:/TradingML/runtimes/bar_gpt')
    data = root/'phase1-v8-learned-mae-20261117/dataset'
    verified = verify(data, parts=('development',))
    sha = file_hash(a.checkpoint); proof = a.checkpoint.with_suffix('.proof.json')
    expected = (json.loads(proof.read_text())['sha256'] if proof.exists()
                else json.loads((a.checkpoint.parent/'result.json').read_text())['checkpoint_sha'])
    if sha != expected: raise ValueError('Checkpoint hash mismatch')
    saved = torch.load(a.checkpoint, map_location='cpu', weights_only=False)
    if saved['contract']['split_hash'] != verified['source_split_hash']: raise ValueError('Split mismatch')
    with lease(a.output):
        started = time.monotonic(); model = restore(saved).cuda().eval()
        x, v, raw, frames = cache(np.load(data/'development.npy', mmap_mode='r'))
        metrics = {k: Metrics() for k in ('fp32/masked', 'fp32/all_visible', 'fp64/masked', 'fp64/all_visible')}
        generator = torch.Generator(device='cuda').manual_seed(917)
        for start in range(0, len(x), 512):
            end = min(start+512, len(x)); xx=x[start:end]; vv=v[start:end]
            b = SimpleNamespace(grid=raw[start:end], frames=frames[start:end])
            pos = positions(len(xx), 'cuda', 16, generator)
            hidden = ~torch.zeros_like(vv[..., 0]).scatter(1, pos, True)
            for mode, ids in (('masked', pos), ('all_visible', positions(len(xx), 'cuda', 64))):
                _, _, out = model(xx, vv, ids)
                mask = vv & hidden[..., None] if mode=='masked' else vv
                metrics['fp32/'+mode].add(model, out, xx, mask, b)
                metrics['fp64/'+mode].add(model, dict(out, logs=precise_logs(out['code'])), xx, mask, b)
        result = {name+'/'+key: value for name, metric in metrics.items() for key, value in scalars(metric.result()).items()}
        write_json(a.output/'result.json', dict(source=a.source_revision, checkpoint_sha=sha,
            data_hash=verified['hash'], split_hash=verified['source_split_hash'], windows=len(x),
            metrics=result, seconds=time.monotonic()-started,
            scope='Paired diagnostic only: same FP32 decoder codes, masks and examples; only subsequent log-coordinate assembly differs. No training or checkpoint modification.'))


if __name__ == '__main__': main()
