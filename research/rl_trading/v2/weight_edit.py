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


def edited_state(state, *, close_bias=0., reduce_bias=0., gate_bias=0., size_means=None, history_scale=1.):
    """Add explicit log-odds offsets; preserve every other tensor exactly."""
    values = (close_bias, reduce_bias, gate_bias)
    if not all(np.isfinite(v) and abs(v) <= 8 for v in values):
        raise ValueError('Bias offsets must be finite and within +/-8')
    if not np.isfinite(history_scale) or not .01 <= history_scale <= 10:
        raise ValueError('History scale must be finite and within [0.01,10]')
    result = {key: value.detach().cpu().clone() for key, value in state.items()}
    result['action_type.bias'][2] += close_bias
    result['action_type.bias'][1] += reduce_bias
    result['trade_gate.bias'][0] += gate_bias
    result['history.weight'].mul_(history_scale)
    result['history.bias'].mul_(history_scale)
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


def replay(policy, session, config, device, seed, root, *, max_steps=0, resume=False, history_scale=1.):
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
    layer_values = {}
    capture = [False]
    handles = []
    def hook(name):
        def record(module, inputs, result):
            if capture[0]:
                v = result.detach().float()
                if name in ('history', 'position'):
                    layer_values[name] = v
                activation[name] = dict(mean=float(v.mean()), std=float(v.std(correction=0)),
                                        maximum=float(v.abs().max()))
                if name == 'actor':
                    activation[name]['cross_listing_std'] = v.std(dim=1, correction=0)[0].cpu().tolist()
        return record
    for name in ('temporal', 'history', 'position', 'account', 'market',
                 'actor', 'action_type', 'trade_gate', 'size', 'critic'):
        handles.append(getattr(policy, name).register_forward_hook(hook(name)))
    def attention_probe(module, inputs):
        if not capture[0]:
            return
        tokens = inputs[0]
        attention = policy.market.layers[0].self_attn
        b, n, width = tokens.shape
        valid = torch.cat((torch.ones((b,1),dtype=torch.bool,device=tokens.device), batch['valid']),dim=1)
        def measure(value):
            q, k, _ = torch.nn.functional.linear(value, attention.in_proj_weight,
                                                 attention.in_proj_bias).chunk(3, dim=-1)
            heads = attention.num_heads
            q = q.reshape(b,n,heads,width//heads).transpose(1,2)
            k = k.reshape(b,n,heads,width//heads).transpose(1,2)
            logits = (q @ k.transpose(-1,-2))/((width//heads)**.5)
            probs = logits.masked_fill(~valid[:,None,None,:], -torch.inf).softmax(dim=-1)
            entropy = -(probs*probs.clamp_min(1e-30).log()).sum(dim=-1)
            denominator = valid.sum()*heads
            return dict(mean_entropy=float((entropy*valid[:,None,:]).sum()/denominator),
                mean_max_probability=float((probs.max(dim=-1).values*valid[:,None,:]).sum()/denominator),
                mean_account_probability=float((probs[...,0]*valid[:,None,:]).sum()/denominator),
                valid_keys=int(valid.sum()))
        activation['attention'] = measure(tokens)
        if history_scale != 1.:
            position = layer_values['position']
            unscaled = layer_values['history'].reshape_as(position)/history_scale+position
            activation['attention_reference_unscaled'] = measure(torch.cat((tokens[:,:1],unscaled),dim=1))
    handles.append(policy.market.register_forward_pre_hook(attention_probe))
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
    parser.add_argument('--history-scale', type=float, default=1.)
    parser.add_argument('--max-steps', type=int, default=0, help='Diagnostic only; never selection evidence')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--refined-run', type=Path, help='Frozen training-only head refinement to evaluate')
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
    refinement = None
    source_state = saved['policy']
    if args.refined_run:
        if any((args.close_bias, args.reduce_bias, args.gate_bias)) or args.size_means is not None or args.history_scale != 1.:
            raise ValueError('Evaluate a refined checkpoint without additional edits')
        refined_root = args.refined_run.resolve()
        if read(refined_root/'status.json').get('status') != 'complete':
            raise ValueError('Refinement must finish before frozen-policy evaluation')
        refined_manifest = read(refined_root/'manifest.json')
        refined_path = refined_root/'checkpoint_latest.pt'
        refined_hash = file_hash(refined_path)
        refined = torch.load(refined_path, map_location='cpu', weights_only=False)
        if (refined_manifest['contract_hash'] != digest({k:v for k,v in refined_manifest.items() if k != 'contract_hash'})
                or refined['contract_hash'] != refined_manifest['contract_hash']
                or refined_manifest['lineage']['parent_checkpoint_hash'] != file_hash(checkpoint)
                or refined_manifest.get('validation_access') is not False
                or refined_manifest['model'] != manifest['model']
                or refined_manifest['config'] != manifest['config']
                or file_hash(refined_path) != refined_hash
                or any(train.get(s['date']) != s for s in refined_manifest['train'])):
            raise ValueError('Refinement provenance mismatch')
        source_state = refined['policy']
        if not all(torch.isfinite(v).all() for v in source_state.values()):
            raise ValueError('Refinement contains nonfinite weights')
        refinement = dict(root=str(refined_root), checkpoint_hash=refined_hash,
                          contract_hash=refined['contract_hash'], iteration=refined['iteration'])
    config = Config(**{f.name: manifest['config'][f.name] for f in fields(Config)})
    policy = PortfolioPolicy(**manifest['model']).to(args.device)
    state = edited_state(source_state, close_bias=args.close_bias,
                         reduce_bias=args.reduce_bias, gate_bias=args.gate_bias, size_means=args.size_means,
                         history_scale=args.history_scale)
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
        history_scale=args.history_scale,
        refinement=refinement,
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
                   resume=args.resume and (target/'replay_state.pt').exists(), history_scale=args.history_scale)
            del session
        write(root/'status.json', dict(status='complete', completed=args.dates,
                                      diagnostic_only=bool(args.max_steps)))
    except BaseException as exc:
        write(root/'status.json', dict(status='failed', error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
