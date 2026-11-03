"""Training-only, bounded direct-weight experiments on a preserved PPO checkpoint.

No validation bank or validation metric is opened by this command. Execution and
observations use the original V2 environment. Candidates are separate artifacts.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import argparse
from dataclasses import fields
import json
import time
import numpy as np
import torch

from research.rl_trading.v2.config import Config
from research.rl_trading.v2.data import MarketSession
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.io import read, write, file_hash, code_identity, output_root, digest
from research.rl_trading.v2.model import PortfolioPolicy, collate


def edited_state(state, *, close_bias=0., reduce_bias=0., gate_bias=0., size_means=None):
    """Add explicit log-odds offsets; preserve every other tensor exactly."""
    values = (close_bias, reduce_bias, gate_bias)
    if not all(np.isfinite(v) and abs(v) <= 8 for v in values):
        raise ValueError('Bias offsets must be finite and within +/-8')
    result = {key: value.detach().cpu().clone() for key, value in state.items()}
    result['action_type.bias'][2] += close_bias
    result['action_type.bias'][1] += reduce_bias
    result['trade_gate.bias'][0] += gate_bias
    if size_means is not None:
        means = np.asarray(size_means, dtype=np.float64)
        if means.shape != (3,) or not np.isfinite(means).all() or np.any(means <= .002) or np.any(means >= .998):
            raise ValueError('Require three interior normalized sizing means')
        # Concentrated but stochastic legal Beta outputs; never clip executed fills.
        shapes = np.stack((means, 1-means), axis=-1)*1000.
        softplus_values = shapes.reshape(-1)-1.01
        raw = softplus_values+np.log(-np.expm1(-softplus_values))
        result['size.weight'].zero_()
        result['size.bias'].copy_(torch.as_tensor(raw, dtype=result['size.bias'].dtype))
    return result


def replay(policy, session, config, device, seed, root, *, max_steps=0, resume=False):
    """Replay the unchanged stochastic policy contract, retaining diagnostic rows."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    if device == 'cuda':
        torch.cuda.manual_seed_all(seed)
    policy.eval()
    env = TradingEnv(session, config)
    obs = env.observe()
    start = time.monotonic()
    sampled = []
    prior_elapsed = 0.
    restart = root/'replay_state.pt'
    fills_offset = 0
    if resume:
        saved = torch.load(restart, map_location='cpu', weights_only=False)
        if saved['plan_hash'] != session.plan['plan_hash'] or saved['seed'] != seed:
            raise ValueError('Replay restart contract mismatch')
        env.load_state_dict(saved['environment'])
        obs = env.observe()
        torch.set_rng_state(saved['torch_rng'])
        if device == 'cuda':
            torch.cuda.set_rng_state(saved['cuda_rng'])
        np.random.set_state(saved['numpy_rng'])
        sampled = saved['samples']
        fills_offset = saved['fills_offset']
        prior_elapsed = saved['elapsed_seconds']
    activation = {}
    capture = [False]
    handles = []
    def hook(name):
        def record(module, inputs, result):
            if capture[0]:
                v = result.detach().float()
                activation[name] = dict(mean=float(v.mean()), std=float(v.std(correction=0)),
                                        maximum=float(v.abs().max()))
                if name == 'actor':
                    activation[name]['cross_listing_std'] = v.std(dim=1, correction=0)[0].cpu().tolist()
        return record
    for name in ('temporal', 'history', 'position', 'account', 'market',
                 'actor', 'action_type', 'trade_gate', 'size', 'critic'):
        handles.append(getattr(policy, name).register_forward_hook(hook(name)))
    fills_path = root/'fills.jsonl'
    try:
        with fills_path.open('r+' if resume else 'w+', encoding='utf-8') as fills, torch.inference_mode():
            if resume:
                fills.seek(fills_offset)
                fills.truncate()
            while not env.done:
                if (root.parent.parent/'STOP').exists():
                    raise InterruptedError('Operator STOP requested')
                capture[0] = env.t % 300 == 0
                batch = collate([obs], device)
                # One forward pass, with the same sampling order as policy.action.
                _, sizing, value, gate, choice = policy(batch, scheduler=True)
                trade = gate.sample().long()
                chosen = choice.sample()
                modes = torch.zeros(batch['valid'].shape, dtype=torch.long, device=device)
                modes.scatter_(1, (chosen//3).unsqueeze(1), ((chosen%3+1)*trade).unsqueeze(1))
                sizes = sizing.sample().clamp(1e-6, 1-1e-6)
                if capture[0]:
                    probabilities = choice.probs.reshape(1, -1, 3).sum(dim=1)[0]
                    sampled.append(dict(second=env.t, equity=env.equity,
                        visible=int(obs['valid'].sum()), holdings=int(np.count_nonzero(env.quantity)),
                        eligible=obs['action_mask'][:, 1:].sum(axis=0).tolist(),
                        gate=float(gate.probs[0]), action_probability=probabilities.cpu().tolist(),
                        size_mean=sizing.mean.mean(dim=(0, 1)).cpu().tolist(),
                        value=float(value[0]), activations=dict(activation)))
                obs, reward, done, summary = env.step(modes[0].cpu().numpy(), sizes[0].cpu().numpy())
                for fill in env.last_fills:
                    fills.write(json.dumps(fill)+'\n')
                if env.t % 600 == 0 or done:
                    progress = dict(status='running', date=session.plan['date'], second=env.t,
                        total_seconds=session.seconds-1, elapsed_seconds=prior_elapsed+time.monotonic()-start,
                        summary=summary)
                    write(root/'progress.json', progress)
                    write(root/'activation_samples.json', sampled)
                    fills.flush()
                    temporary = restart.with_suffix('.tmp')
                    torch.save(dict(environment=env.state_dict(), samples=sampled,
                        torch_rng=torch.get_rng_state(), numpy_rng=np.random.get_state(),
                        cuda_rng=torch.cuda.get_rng_state() if device == 'cuda' else None,
                        fills_offset=fills.tell(), elapsed_seconds=progress['elapsed_seconds'],
                        plan_hash=session.plan['plan_hash'], seed=seed), temporary)
                    os.replace(temporary, restart)
                    print(json.dumps({k:v for k,v in progress.items() if k != 'summary'}), flush=True)
                if max_steps and env.t >= max_steps:
                    break
        result = dict(date=session.plan['date'], seed=seed, full_session=env.done,
            elapsed_seconds=prior_elapsed+time.monotonic()-start, summary=env.summary(), samples=sampled)
        write(root/'result.json', result)
        return result
    finally:
        for handle in handles:
            handle.remove()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--dates', nargs='+', required=True)
    parser.add_argument('--seed', type=int, default=1917)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    parser.add_argument('--close-bias', type=float, default=0.)
    parser.add_argument('--reduce-bias', type=float, default=0.)
    parser.add_argument('--gate-bias', type=float, default=0.)
    parser.add_argument('--size-means', type=float, nargs=3,
                        help='Normalized allocation/stop/target Beta means; concentration 1000')
    parser.add_argument('--max-steps', type=int, default=0, help='Diagnostic only; never selection evidence')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if Path(args.name).name != args.name or args.name in ('.', '..') or args.max_steps < 0:
        raise ValueError('Invalid experiment name or step count')
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    parent = args.parent.resolve()
    manifest = read(parent/'run_manifest.json')
    train = {s['date']: s for s in manifest['train']}
    if len(set(args.dates)) != len(args.dates) or any(d not in train for d in args.dates):
        raise ValueError('Only unique parent TRAINING dates are allowed')
    if manifest['contract_hash'] != digest({k:v for k,v in manifest.items() if k != 'contract_hash'}):
        raise ValueError('Parent manifest integrity mismatch')
    checkpoint = parent/'checkpoint_latest.pt'
    saved = torch.load(checkpoint, map_location='cpu', weights_only=False)
    if saved['contract_hash'] != manifest['contract_hash']:
        raise ValueError('Parent checkpoint contract mismatch')
    if not all(torch.isfinite(v).all() for v in saved['policy'].values()):
        raise ValueError('Parent policy contains nonfinite weights')
    config = Config(**{f.name: manifest['config'][f.name] for f in fields(Config)})
    policy = PortfolioPolicy(**manifest['model']).to(args.device)
    state = edited_state(saved['policy'], close_bias=args.close_bias,
                         reduce_bias=args.reduce_bias, gate_bias=args.gate_bias, size_means=args.size_means)
    policy.load_state_dict(state)
    root = output_root()/'weight-edit-20261103'/args.name
    root.mkdir(parents=True, exist_ok=args.resume)
    changes = {k: int((v != saved['policy'][k].cpu()).sum()) for k,v in state.items()
               if not torch.equal(v, saved['policy'][k].cpu())}
    receipt = dict(parent=str(parent), parent_checkpoint_hash=file_hash(checkpoint),
        parent_iteration=saved['iteration'], model=manifest['model'], config=manifest['config'],
        train=[train[d] for d in args.dates], validation_access=False,
        offsets=dict(close=args.close_bias, reduce=args.reduce_bias, gate=args.gate_bias),
        size_means=args.size_means, size_concentration=1000 if args.size_means is not None else None,
        changed_parameters=changes, seed=args.seed, device=args.device,
        max_steps=args.max_steps, code=code_identity())
    if args.resume:
        if read(root/'manifest.json') != json.loads(json.dumps(receipt)):
            raise ValueError('Resume requires identical source, data and edit contract')
    else:
        write(root/'manifest.json', receipt)
        (root/'runner_source.py').write_bytes(Path(__file__).read_bytes())
        torch.save(dict(policy=state, provenance=receipt), root/'candidate.pt')
    try:
        for day in args.dates:
            target = root/day
            if args.resume and (target/'result.json').exists():
                continue
            target.mkdir(exist_ok=args.resume)
            write(root/'status.json', dict(status='verifying', active=day))
            print('Verifying training bank '+day, flush=True)
            session = MarketSession.load(train[day]['root'])
            if (session.plan['date'] != day or session.plan['plan_hash'] != train[day]['plan_hash'] or
                    file_hash(Path(train[day]['root'])/'complete.json') != train[day]['complete_hash']):
                raise ValueError('Parent data binding changed')
            write(root/'status.json', dict(status='replaying', active=day))
            replay(policy, session, config, args.device, args.seed, target, max_steps=args.max_steps,
                   resume=args.resume and (target/'replay_state.pt').exists())
            del session
        write(root/'status.json', dict(status='complete', completed=args.dates,
                                      diagnostic_only=bool(args.max_steps)))
    except BaseException as exc:
        write(root/'status.json', dict(status='failed', error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
