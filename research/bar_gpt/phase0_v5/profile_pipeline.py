"""Bounded same-checkpoint qualification of metrics replay and transfer overlap."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
import argparse
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
from .model import Autoencoder, Config
from .metrics import Metrics
from ..phase0_v4.metrics import Metrics as OriginalMetrics
from .execution import Replay, EvaluationReplay
from .data import forward_batch
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v11.pipeline import loader, DeviceBatches
from ..v5.data_v3 import load_split, close_loader
from ..v5.loader_index_v1 import attach
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.phase1_contract import write_json, file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.steps <= 500 or not args.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError("Bounded runtime profiling only")
    args.output.mkdir(exist_ok=False)
    torch.set_num_threads(4)
    saved = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
    saved = load_checkpoint(args.checkpoint, saved['contract'])
    parameters = dict(mean=saved['model']['mean'], scale=saved['model']['scale'], **saved['model_extra'])

    def create():
        model = Autoencoder(parameters, Config(**saved['contract']['config'])).cuda()
        model.load_state_dict({**saved['model'], '_extra_state': saved['model_extra']})
        return model

    root = Path('D:/TradingML/runtimes/bar_gpt')
    rows = np.load(root/'phase1-v8-learned-mae-20261117/dataset/development.npy', mmap_mode='r')
    batch = prepare_raw(rows[:1024], native=True).to('cuda')
    model = create().eval()
    reference, candidate = OriginalMetrics('cuda'), Metrics('cuda')
    with torch.no_grad():
        replay = EvaluationReplay(model, batch, candidate)
        # More than one input batch detects stale captured targets or counters.
        for selected in (batch, prepare_raw(rows[1024:2048], native=True).to('cuda')):
            out, values, valid = forward_batch(model, selected)
            reference.add(model, out, values, valid, selected)
            replay(selected)
        expected, actual = reference.result(), candidate.result()
    assert expected['windows'] == actual['windows']
    assert expected['metrics'].keys() == actual['metrics'].keys()
    for key in expected['metrics']:
        np.testing.assert_allclose(actual['metrics'][key], expected['metrics'][key], rtol=1e-8, atol=1e-7, err_msg=key)
    np.testing.assert_allclose(actual['loss'], expected['loss'], rtol=1e-10)
    write_json(args.output/'parity.json', dict(metrics=len(actual['metrics']), windows=actual['windows'], passed=True))
    del replay, model, reference, candidate, batch
    torch.cuda.empty_cache()
    manifest = load_split(root/'phase1-v6-cohort-balanced-v4/split.json')
    manifest = attach(manifest, Path(manifest['dataset']))
    manifest['splits'] = {}
    results = []
    for mode in ('validation_eager', 'validation_graph', 'train_serial', 'train_overlap'):
        validation = mode.startswith('validation')
        model = create()
        stream_args = SimpleNamespace(output=args.output/mode, batch_size=4096,
            validation_batch_size=1024, workers=8, validation_workers=8, prefetch=4,
            seed=17, preparation='raw_gpu', shard_prefetch=16, shard_prefetch_mib=64)
        began = time.perf_counter()
        source = loader(manifest, stream_args, 'validation' if validation else 'train', saved['epoch'])
        device = DeviceBatches(iter(source), 'cuda')
        startup = time.perf_counter()-began
        metric = Metrics('cuda') if mode=='validation_graph' else OriginalMetrics('cuda')
        replay = None
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0., fused=True)
        optimizer.load_state_dict(copy.deepcopy(saved['optimizer']))
        try:
            for step in range(args.steps+10):
                if step==10:
                    torch.cuda.synchronize(); began=time.perf_counter(); waiting=device.wait_seconds
                batch = device.pop()
                if validation:
                    with torch.no_grad():
                        if mode=='validation_graph':
                            if replay is None: replay=EvaluationReplay(model, batch, metric)
                            replay(batch)
                        else:
                            out, values, valid = forward_batch(model, batch)
                            metric.add(model, out, values, valid, batch)
                    device.advance()
                else:
                    optimizer.zero_grad(set_to_none=True)
                    if replay is None: replay=Replay(model, batch)
                    loss, norm, _, _=replay(batch)
                    if mode=='train_overlap': device.advance()
                    if not bool(torch.isfinite(loss)&torch.isfinite(norm)): raise FloatingPointError('Nonfinite update')
                    optimizer.step()
                    if mode=='train_serial': device.advance()
            torch.cuda.synchronize()
            seconds=time.perf_counter()-began
            result=dict(mode=mode, seconds=seconds, windows_per_second=args.steps*(1024 if validation else 4096)/seconds,
                        startup_seconds=startup, loader_wait_seconds=device.wait_seconds-waiting)
            results.append(result); print(json.dumps(result), flush=True)
            write_json(args.output/'profiles.json', dict(checkpoint_sha256=file_hash(args.checkpoint), results=results))
        finally:
            device.close(); close_loader(source)
        del model, optimizer, replay, metric, batch
        torch.cuda.empty_cache()


if __name__=='__main__':
    main()
