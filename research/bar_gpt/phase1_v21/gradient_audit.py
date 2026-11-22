"""Read-only training-cache gradient audit; no optimizer or validation selection."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
import argparse
import itertools
import json
import time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
from .model import initialize
from .objectives import loss
from .run_full import configure_activity
from ..phase0_v3.experiment import cache
from ..phase1_v12.data import load_teacher
from ..phase1_v12.model import positions
from ..phase1_v11.data import verify
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash, write_json


def geometry(vectors):
    norms = {k: float(v.double().norm()) for k, v in vectors.items()}
    if not all(torch.isfinite(v).all() for v in vectors.values()):
        raise FloatingPointError('Nonfinite gradient')
    cosines = {}
    for a, b in itertools.combinations(vectors, 2):
        denominator = norms[a] * norms[b]
        cosines[a + ':' + b] = (float(torch.dot(vectors[a].double(), vectors[b].double())) / denominator
                                if denominator else None)
    return dict(norms=norms, cosines=cosines)


def audit(model, inputs):
    total, parts = loss(model, *inputs, torch.ones(6, 6, device=inputs[0].device))
    families = dict(joint_base=parts['joint_base'],
                    activity=parts['activity'] + .1 * parts['activity_direction'],
                    range=.1 * parts['range'] + .05 * parts['range_bins'],
                    candle=.5 * parts['candle_direction'])
    torch.testing.assert_close(sum(families.values()), total)
    params = list(model.named_parameters())
    vectors = {group: {} for group in ('bar', 'temporal', 'decoder')}
    for name, value in families.items():
        grads = torch.autograd.grad(value, [p for _, p in params], retain_graph=True, allow_unused=True)
        for group in vectors:
            vectors[group][name] = torch.cat([
                (torch.zeros_like(p) if g is None else g).detach().flatten().float()
                for (n, p), g in zip(params, grads) if n.startswith(group + '.')])
    return dict(loss=float(total.detach()), families={k: float(v.detach()) for k, v in families.items()},
                gradients={k: geometry(v) for k, v in vectors.items()})


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--checkpoint', type=Path, action='append', required=True)
    p.add_argument('--activity-frame-counts', type=Path, required=True)
    p.add_argument('--batch-size', type=int, default=256)
    p.add_argument('--batches', type=int, default=2)
    a = p.parse_args()
    if not 1 <= a.batch_size <= 512 or not 1 <= a.batches <= 4 or len(a.checkpoint) > 3:
        raise ValueError('Bounded audit required')
    a.device = 'cuda'; a.resume = False
    preflight(a)
    root = Path('D:/TradingML/runtimes/bar_gpt')
    data = root/'phase1-v8-learned-mae-20261117/dataset'
    verified = verify(data, parts=('train',))
    counts = json.loads(a.activity_frame_counts.read_text())
    if counts['split_hash'] != verified['source_split_hash']:
        raise ValueError('Training frame calibration split mismatch')
    torch.set_num_threads(4); torch.set_float32_matmul_precision('highest')
    torch.manual_seed(917)
    started = time.monotonic()
    rows = np.load(data/'train.npy', mmap_mode='r')
    ids = np.random.default_rng(917).choice(len(rows), a.batches*a.batch_size, replace=False)
    records = []
    with lease(a.output):
        teacher, identity = load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',
                                        verified['source_split_hash'], 'cuda')
        x, v, raw, frames = cache(rows[ids])
        with torch.no_grad(): old = teacher.encode(x, v)
        for cp in a.checkpoint:
            sha = file_hash(cp)
            proof = cp.with_suffix('.proof.json')
            expected = (json.loads(proof.read_text())['sha256'] if proof.exists()
                        else json.loads((cp.parent/'result.json').read_text())['checkpoint_sha'])
            if sha != expected: raise ValueError('Checkpoint integrity')
            saved = torch.load(cp, map_location='cpu', weights_only=False)
            if saved['contract']['split_hash'] != verified['source_split_hash']:
                raise ValueError('Checkpoint split mismatch')
            if saved['contract'].get('bar_parent') != identity and saved['contract'].get('teacher') != identity:
                raise ValueError('Anchor teacher mismatch')
            for balanced in (False, True):
                model = initialize(None, saved, None, 'joint_repaired').cuda().train()
                configure_activity(model, SimpleNamespace(activity_weight=1., activity_frame_counts=a.activity_frame_counts if balanced else None))
                for i in range(a.batches):
                    s = slice(i*a.batch_size, (i+1)*a.batch_size)
                    for visible in (16, 64):
                        if time.monotonic()-started > 600: raise TimeoutError('Audit deadline')
                        generator = torch.Generator(device='cuda').manual_seed(917+i)
                        pos = positions(a.batch_size, 'cuda', visible, generator)
                        result = audit(model, (x[s], v[s], raw[s], frames[s], pos, old[s]))
                        records.append(dict(checkpoint=str(cp), sha256=sha, balanced=balanced,
                                            batch=i, visible=visible, **result))
                del model
        write_json(a.output/'result.json', dict(source=a.source_revision, data_hash=verified['hash'],
            split_hash=verified['source_split_hash'], teacher=identity, ids=ids.tolist(),
            frame_counts_sha=file_hash(a.activity_frame_counts),
            frames={str(int(f)): int((frames==f).sum()) for f in frames.unique()},
            records=records, seconds=time.monotonic()-started,
            limitation='Training-cache diagnostic only. Raw gradients before clipping/Adam; not causal proof or validation. joint_base includes the original raw/coordinate activity term.'))


if __name__ == '__main__': main()
