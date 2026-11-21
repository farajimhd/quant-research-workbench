"""Paired short continuations testing all-visible exposure, not a full restart."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
import argparse
import gc
import json
import time
from pathlib import Path
import torch
import wandb
from .model import Autoencoder
from .experiment import audit, cache, evaluate
from ..phase1_v12.model import Config, positions, objective
from ..phase1_v12.data import load_teacher
from ..phase1_v11.data import verify
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash, write_json
from research.mlops.env import discover_env_files, load_env_files


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--revision', required=True)
    p.add_argument('--steps', type=int, default=5000)
    a = p.parse_args()
    if a.output.exists() or not 1 <= a.steps <= 10000:
        raise ValueError('New bounded experiment identity required')
    parent_result = json.loads((a.checkpoint.parent / 'result.json').read_text())
    if file_hash(a.checkpoint) != parent_result['checkpoint_sha']:
        raise ValueError('Parent checkpoint changed')
    saved = torch.load(a.checkpoint, map_location='cpu', weights_only=False)
    if saved['contract']['variant'] != 'stable_balanced':
        raise ValueError('Expected corrected parent')
    root = Path('D:/TradingML/runtimes/bar_gpt')
    cal = json.loads((root / 'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    dataset = root / 'phase1-v8-learned-mae-20261117/dataset'
    data = verify(dataset)
    if data['hash'] != cal['data_hash'] or cal['split_hash'] != saved['contract']['split_hash']:
        raise ValueError('Data/calibration mismatch')
    a.output.mkdir(parents=True)
    import psutil
    write_json(a.output / 'process.json', dict(pid=os.getpid(), created=psutil.Process().create_time()))
    torch.set_num_threads(4)
    torch.set_float32_matmul_precision('highest')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]), verbose=False)
    with lease(a.output):
        teacher, _ = load_teacher(Path(cal['teacher_path']), cal['split_hash'], 'cuda')
        started = time.perf_counter()
        train = cache(dataset / 'train.npy', teacher)
        dev = cache(dataset / 'development.npy', teacher)
        contract = dict(source=a.revision, parent_checkpoint=str(a.checkpoint), parent_sha=file_hash(a.checkpoint),
                        data_hash=data['hash'], split_hash=cal['split_hash'], steps=a.steps, batch=1024,
                        lr=3e-5, cache_seconds=time.perf_counter()-started, sampling_seed=559,
                        config=saved['contract']['config'], variant='stable_balanced',
                        scope='Paired subset continuation, same parent weights and AdamW state; not full training')
        write_json(a.output / 'manifest.json', contract)
        results = []
        for name in ('masked_only', 'mixed_visibility'):
            out = a.output / name
            out.mkdir()
            m = Autoencoder(Config(**contract['config']), cal['mean'], cal['scale'], 'stable_balanced').cuda()
            m.load_state_dict(saved['model'])
            opt = torch.optim.AdamW(m.parameters(), lr=3e-5, weight_decay=.01, fused=True)
            opt.load_state_dict(saved['optimizer'])
            g = torch.Generator(device='cuda').manual_seed(559)
            w = wandb.init(entity='mehdifaraji', project='bargpt-phase1-v15-visibility-fix',
                           name=a.output.name+'-'+name, dir=str(out), config=dict(contract, exposure=name), reinit=True)
            start = time.perf_counter()
            status = 'failed'
            step = 0
            curve = []
            try:
                m.train()
                for step in range(1, a.steps+1):
                    if time.perf_counter()-start > 900 or (a.output/'STOP').exists():
                        raise InterruptedError('15-minute deadline or human STOP')
                    ids = torch.randint(len(train[0]), (1024,), device='cuda', generator=g)
                    z, meta, valid = [x[ids] for x in train[:3]]
                    pos = positions(len(z), 'cuda', 16, g)
                    if name == 'mixed_visibility' and step % 4 == 0:
                        pos = positions(len(z), 'cuda', 64)
                    opt.zero_grad(set_to_none=True)
                    loss, _ = objective(m, m(z, meta, pos), z, valid, pos)
                    loss.backward()
                    norm = stable_clip(m.parameters())
                    if not bool(torch.isfinite(loss) & torch.isfinite(norm)):
                        raise FloatingPointError('Nonfinite loss/gradient')
                    opt.step()
                    if step % 100 == 0:
                        w.log({'train/loss':float(loss.detach()), 'train/gradient_norm':float(norm)}, step=step)
                    if step % 1000 == 0 or step == a.steps:
                        measured = evaluate(m, teacher, dev)
                        curve.append(dict(step=step, metrics=measured))
                        w.log({'development/'+k:v for k,v in measured.items()}, step=step)
                        write_json(out/'progress.json', dict(step=step, curve=curve))
                        print(name, step, measured, flush=True)
                detailed, scalars = audit(m, teacher, dev)
                write_json(out/'detailed-development.json', detailed)
                write_json(out/'detailed-scalars.json', scalars)
                w.log({'detailed_development/'+k:v for k,v in scalars.items()}, step=step)
                torch.save(dict(model=m.state_dict(), optimizer=opt.state_dict(), contract=dict(contract, exposure=name),
                                step=step, rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all(),
                                sampling_rng=g.get_state()), out/'checkpoint.pt')
                result = dict(name=name, curve=curve, seconds=time.perf_counter()-start,
                              checkpoint_sha=file_hash(out/'checkpoint.pt'), wandb_url=w.url)
                write_json(out/'result.json', result)
                results.append(result)
                write_json(a.output/'comparison.json', results)
                status = 'complete'
            except BaseException as exc:
                torch.save(dict(model=m.state_dict(), optimizer=opt.state_dict(), step=step,
                                contract=dict(contract, exposure=name), sampling_rng=g.get_state(),
                                error=repr(exc)), out/'checkpoint_partial.pt')
                raise
            finally:
                w.summary['status'] = status
                w.finish(exit_code=0 if status == 'complete' else 1)
                del m, opt
                gc.collect()
                torch.cuda.empty_cache()


if __name__ == '__main__':
    main()
