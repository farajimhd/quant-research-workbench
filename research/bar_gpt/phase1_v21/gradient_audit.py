"""Training-cache gradient audit, with optional disposable tiny-fit gate."""
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
from .joint_base import loss as base_loss
from ..phase0_v1.model import objective as raw_loss
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


def adam_scaling(model, saved):
    """Fixed bias-corrected second-moment preconditioner, not an Adam step."""
    if saved['contract'].get('version') != 'phase1-v21-full20':
        raise ValueError('Only the verified full-training parameter-group contract is supported')
    groups = saved['optimizer']['param_groups']; states = saved['optimizer']['state']
    if len(groups) != 3: raise ValueError('Optimizer group count')
    result = {}
    for prefix, group in zip(('bar.', 'temporal.', 'decoder.'), groups):
        params = [(n, p) for n, p in model.named_parameters() if n.startswith(prefix)]
        if len(params) != len(group['params']): raise ValueError('Optimizer parameter order')
        for (name, parameter), key in zip(params, group['params']):
            state = states[key]; variance = state['exp_avg_sq'].to(parameter.device)
            step = float(state['step'])
            if step <= 0 or variance.shape != parameter.shape: raise ValueError('Optimizer moment state')
            result[name] = group['lr'] / ((variance/(1-group['betas'][1]**step)).sqrt()+group['eps'])
    return result


def audit(model, inputs, precondition=None):
    captured = []
    hook = model.register_forward_hook(lambda module, args, output: captured.append(output))
    scale = torch.ones(6, 6, device=inputs[0].device)
    try:
        total, parts = loss(model, *inputs, scale)
    finally:
        hook.remove()
    families = dict(joint_base=parts['joint_base'],
                    activity=parts['activity'] + .1 * parts['activity_direction'],
                    range=.1 * parts['range'] + .05 * parts['range_bins'],
                    candle=.5 * parts['candle_direction'])
    torch.testing.assert_close(sum(families.values()), total)
    latent, _, out = captured[0]
    _, details = base_loss(model, *inputs, scale, latent, out, include_activity=False)
    _, components = raw_loss(model, out, inputs[0], inputs[1])
    breakdown = dict(raw_price=.5*components[0], raw_activity=.5*components[1],
                     coordinate=.1*components[2], body=.5*details['body'],
                     wick=.05*details['wick'], anchor=details['bar_latent_anchor'])
    torch.testing.assert_close(sum(breakdown.values()), parts['joint_base'])
    params = list(model.named_parameters())
    vectors = {group: {} for group in ('bar', 'temporal', 'decoder')}
    conditioned = {group: {} for group in vectors}
    for name, value in dict(families, **{'base_'+k: v for k, v in breakdown.items()}).items():
        grads = torch.autograd.grad(value, [p for _, p in params], retain_graph=True, allow_unused=True)
        for group in vectors:
            vectors[group][name] = torch.cat([
                (torch.zeros_like(p) if g is None else g).detach().flatten().float()
                for (n, p), g in zip(params, grads) if n.startswith(group + '.')])
            if precondition is not None:
                conditioned[group][name] = torch.cat([
                    ((torch.zeros_like(p) if g is None else g).detach()*precondition[n]).flatten().float()
                    for (n, p), g in zip(params, grads) if n.startswith(group + '.')])
    return dict(loss=float(total.detach()), families={k: float(v.detach()) for k, v in families.items()},
                gradients={k: geometry(v) for k, v in vectors.items()},
                fixed_adam_preconditioner={k: geometry(v) for k, v in conditioned.items()} if precondition is not None else None)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--checkpoint', type=Path, action='append', required=True)
    p.add_argument('--activity-frame-counts', type=Path, required=True)
    p.add_argument('--batch-size', type=int, default=256)
    p.add_argument('--batches', type=int, default=2)
    p.add_argument('--stratified', action='store_true', help='Equal-count timeframe diagnostic, not population estimate')
    p.add_argument('--tiny-price-weight', type=float, help='Optional separate disposable-model overfit gate')
    p.add_argument('--adam-precondition', action='store_true')
    a = p.parse_args()
    if not 1 <= a.batch_size <= 512 or not 1 <= a.batches <= 4 or len(a.checkpoint) > 3:
        raise ValueError('Bounded audit required')
    if a.tiny_price_weight is not None and not .001 <= a.tiny_price_weight <= 1:
        raise ValueError('Price weight bounds')
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
    rng = np.random.default_rng(917)
    if a.stratified:
        choices = []
        for f in np.unique(rows['timeframe_us']):
            pool = np.flatnonzero(rows['timeframe_us'] == f)
            choices.extend(rng.choice(pool, a.batches*a.batch_size//6, replace=False).tolist())
        rng.shuffle(choices)
        ids = np.array(choices)
        a.batch_size = len(ids)//a.batches
    else:
        ids = rng.choice(len(rows), a.batches*a.batch_size, replace=False)
    records = []; tiny_results = []
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
            if a.tiny_price_weight is not None:
                tiny = initialize(None, saved, None, 'joint_repaired').cuda().train()
                tiny.price_loss_weight = a.tiny_price_weight
                opt = torch.optim.AdamW(tiny.parameters(), lr=1e-5, weight_decay=0.)
                pos = positions(min(16, len(x)), 'cuda', 64)
                args = (x[:16], v[:16], raw[:16], frames[:16], pos, old[:16], torch.ones(6, 6, device='cuda'))
                curve = []
                for step in range(101):
                    value, _ = loss(tiny, *args)
                    if step % 25 == 0: curve.append(float(value.detach()))
                    if step < 100:
                        opt.zero_grad(set_to_none=True); value.backward()
                        torch.nn.utils.clip_grad_norm_(tiny.parameters(), 1., error_if_nonfinite=True)
                        opt.step()
                tiny_results.append(dict(checkpoint=str(cp), loss=curve, passed=curve[-1]<curve[0]))
                del tiny, opt
            for balanced in (False, True):
                model = initialize(None, saved, None, 'joint_repaired').cuda().train()
                configure_activity(model, SimpleNamespace(activity_weight=1., activity_frame_counts=a.activity_frame_counts if balanced else None))
                precondition = adam_scaling(model, saved) if a.adam_precondition else None
                for i in range(a.batches):
                    s = slice(i*a.batch_size, (i+1)*a.batch_size)
                    for visible in (16, 64):
                        if time.monotonic()-started > 600: raise TimeoutError('Audit deadline')
                        generator = torch.Generator(device='cuda').manual_seed(917+i)
                        pos = positions(a.batch_size, 'cuda', visible, generator)
                        result = audit(model, (x[s], v[s], raw[s], frames[s], pos, old[s]), precondition)
                        records.append(dict(checkpoint=str(cp), sha256=sha, balanced=balanced,
                                            batch=i, visible=visible, **result))
                del model
        write_json(a.output/'result.json', dict(source=a.source_revision, data_hash=verified['hash'],
            split_hash=verified['source_split_hash'], teacher=identity, ids=ids.tolist(),
            frame_counts_sha=file_hash(a.activity_frame_counts),
            frames={str(int(f)): int((frames==f).sum()) for f in frames.unique()},
            records=records, tiny=tiny_results, stratified=a.stratified, seconds=time.monotonic()-started,
            limitation='Training-cache diagnostic only, not causal proof or validation. joint_base includes original raw/coordinate activity. Fixed Adam preconditioning uses saved bias-corrected second moments and group LR, without momentum, next-step variance or global clipping; it is not the actual optimizer update.'))


if __name__ == '__main__': main()
