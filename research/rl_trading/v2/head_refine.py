"""One training-only pass with a frozen encoder and cached-context PPO heads.

Committed checkpoints retain account/RNG state for inspection; automatic resume
is intentionally not exposed. Returns describe a changing training policy.
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

from research.rl_trading.v1.common import digest
from research.rl_trading.v2.config import Config
from research.rl_trading.v2.data import MarketSession
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.io import read, write, file_hash, code_identity, output_root
from research.rl_trading.v2.model import PortfolioPolicy, collate
from research.rl_trading.v2.objectives import advantages, ppo_loss
from research.rl_trading.v2.weight_edit import edited_state

HEADS = ('actor', 'action_type', 'trade_gate', 'size', 'critic')


def freeze_encoder(policy):
    policy.eval()
    for name, parameter in policy.named_parameters():
        parameter.requires_grad_(name.split('.')[0] in HEADS)
    return [p for p in policy.parameters() if p.requires_grad]


def cached_action(policy, observation, device):
    """Keep detached context and action masks, never the market-history tensor."""
    with torch.no_grad():
        batch = collate([observation], device)
        context = policy.encode(batch)
        modes, sizes, logprob, _, value = policy.action_from_context(context, batch)
        row = dict(context=context[0].cpu().clone(), valid=batch['valid'][0].cpu().clone(),
            action_mask=batch['action_mask'][0].cpu().clone(),
            position=batch['position'][0, :, :1].cpu().clone(),
            modes=modes[0].cpu().clone(), sizes=sizes[0].cpu().clone(),
            logprob=float(logprob[0]), value=float(value[0]))
    return row


def collate_cached(rows, device):
    count = max(len(row['valid']) for row in rows)
    width = rows[0]['context'].shape[-1]
    context = torch.zeros(len(rows), count + 1, width)
    batch = dict(valid=torch.zeros(len(rows), count, dtype=torch.bool),
        action_mask=torch.zeros(len(rows), count, 4, dtype=torch.bool),
        position=torch.zeros(len(rows), count, 1))
    batch['action_mask'][:, :, 0] = True
    modes = torch.zeros(len(rows), count, dtype=torch.long)
    sizes = torch.full((len(rows), count, 3), .5)
    for index, row in enumerate(rows):
        n = len(row['valid'])
        context[index, :n+1] = row['context']
        for key in batch:
            batch[key][index, :n] = row[key]
        modes[index, :n], sizes[index, :n] = row['modes'], row['sizes']
    return context.to(device), {k: v.to(device) for k, v in batch.items()}, modes.to(device), sizes.to(device)


def update_heads(policy, optimizer, rows, bootstrap, rng, device, *, epochs=2, batch_size=16):
    adv, targets = advantages([r['reward'] for r in rows], [r['value'] for r in rows],
        [r['done'] for r in rows], bootstrap, gae_lambda=1.)
    normalized = (adv - adv.mean()) / max(float(adv.std()), 1e-8)
    measures = []
    stopped = False
    for _ in range(epochs):
        order = rng.permutation(len(rows))
        for start in range(0, len(rows), batch_size):
            indices = order[start:start+batch_size]
            selected = [rows[i] for i in indices]
            context, batch, modes, sizes = collate_cached(selected, device)
            _, _, logprob, entropy, value = policy.action_from_context(context, batch, modes, sizes)
            loss, measure = ppo_loss(logprob,
                torch.tensor([r['logprob'] for r in selected], device=device),
                torch.as_tensor(normalized[indices], device=device), value,
                torch.as_tensor(targets[indices], device=device), entropy,
                clip=.1, entropy_weight=.0001)
            if measure['approx_kl'] > .01:
                stopped = True
                break
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in policy.parameters() if p.requires_grad],
                                          .5, error_if_nonfinite=True)
            optimizer.step()
            measures.append(measure)
        if stopped:
            break
    return dict(updates=len(measures), kl_early_stop=stopped,
        losses={k: float(np.mean([m[k] for m in measures])) for k in measures[0]} if measures else {})


def training_bindings(manifest, dates):
    train = {row['date']: row for row in manifest['train']}
    if len(train) != len(manifest['train']) or len(set(dates)) != len(dates) or any(d not in train for d in dates):
        raise ValueError('Only unique parent TRAINING dates are allowed')
    return [train[d] for d in dates]


def initial_policy(parent, manifest, candidate):
    checkpoint = parent/'checkpoint_latest.pt'
    parent_hash = file_hash(checkpoint)
    saved = torch.load(checkpoint, map_location='cpu', weights_only=False)
    if saved['contract_hash'] != manifest['contract_hash'] or file_hash(checkpoint) != parent_hash:
        raise ValueError('Parent checkpoint contract mismatch or concurrent change')
    state = saved['policy']
    candidate_hash = None
    if candidate is not None:
        candidate_hash = file_hash(candidate)
        edited = torch.load(candidate, map_location='cpu', weights_only=False)
        provenance = edited['provenance']
        if (provenance['parent_checkpoint_hash'] != parent_hash or
                Path(provenance['parent']).resolve() != parent or
                provenance['model'] != manifest['model'] or provenance['config'] != manifest['config'] or
                provenance.get('validation_access') is not False or
                file_hash(candidate) != candidate_hash):
            raise ValueError('Candidate provenance mismatch')
        bindings = training_bindings(manifest, [r['date'] for r in provenance['train']])
        if bindings != provenance['train']:
            raise ValueError('Candidate training binding mismatch')
        offsets = provenance['offsets']
        reconstructed = edited_state(state, close_bias=offsets['close'], reduce_bias=offsets['reduce'],
            gate_bias=offsets['gate'], size_means=provenance.get('size_means'))
        if reconstructed.keys() != edited['policy'].keys() or any(
                not torch.equal(v, edited['policy'][k].cpu()) for k, v in reconstructed.items()):
            raise ValueError('Candidate weights do not match declared edit')
        state = edited['policy']
    if not all(torch.isfinite(v).all() for v in state.values()):
        raise ValueError('Nonfinite initial policy')
    policy = PortfolioPolicy(**manifest['model'])
    policy.load_state_dict(state)
    return policy, dict(parent=str(parent), parent_checkpoint_hash=parent_hash,
        parent_iteration=saved['iteration'], candidate=str(candidate) if candidate else None,
        candidate_hash=candidate_hash)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', required=True, type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--name', required=True)
    parser.add_argument('--dates', nargs='+', required=True)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    parser.add_argument('--seed', type=int, default=1917)
    parser.add_argument('--lr', type=float, default=1e-5)
    parser.add_argument('--epochs', type=int, default=2)
    parser.add_argument('--rollout', type=int, default=512)
    parser.add_argument('--batch-size', type=int, default=16)
    args = parser.parse_args(argv)
    if (Path(args.name).name != args.name or args.name in ('.', '..') or args.seed < 0 or
            not np.isfinite(args.lr) or args.lr <= 0 or not 1 <= args.epochs <= 8 or
            not 1 <= args.rollout <= 512 or not 1 <= args.batch_size <= 32):
        raise ValueError('Invalid name, seed, learning rate or bounded update dimensions')
    parent = args.parent.resolve()
    manifest = read(parent/'run_manifest.json')
    bindings = training_bindings(manifest, args.dates)
    if manifest['contract_hash'] != digest({k: v for k, v in manifest.items() if k != 'contract_hash'}):
        raise ValueError('Parent manifest integrity mismatch')
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    rng = np.random.default_rng(args.seed)
    policy, lineage = initial_policy(parent, manifest, args.candidate)
    policy.to(args.device)
    optimizer = torch.optim.Adam(freeze_encoder(policy), lr=args.lr)
    config = Config(**{f.name: manifest['config'][f.name] for f in fields(Config)})
    root = output_root()/'head-refine-20261103'/args.name
    root.mkdir(parents=True, exist_ok=False)
    receipt = dict(version='v2-frozen-encoder-head-ppo-1', lineage=lineage,
        model=manifest['model'], config=manifest['config'], train=bindings,
        arguments={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        code=code_identity(), validation_access=False, teacher_supervision=False,
        evidence='changing-policy-training-returns', frozen_encoder=True,
        gamma=1., gae_lambda=1., clip=.1, entropy_weight=.0001, value_weight=.5,
        target_kl=.01, gradient_norm=.5, automatic_resume_supported=False)
    receipt['contract_hash'] = digest(receipt)
    write(root/'manifest.json', receipt)
    (root/'runner_source.py').write_bytes(Path(__file__).read_bytes())
    completed, iteration = [], 0
    started = time.monotonic()
    last_report = started
    try:
        for binding in bindings:
            day = binding['date']
            write(root/'status.json', dict(status='verifying', active=day, completed=completed))
            session = MarketSession.load(binding['root'])
            if (session.plan['date'] != day or session.plan['plan_hash'] != binding['plan_hash'] or
                    file_hash(Path(binding['root'])/'complete.json') != binding['complete_hash'] or
                    session.plan['feature_names'] != manifest['feature_names']):
                raise ValueError('Training bank binding changed')
            env = TradingEnv(session, config)
            obs = env.observe()
            while not env.done:
                if (root/'STOP').exists() or (root.parent/'STOP').exists():
                    write(root/'status.json', dict(status='stopped', active=day,
                        completed=completed, iteration=iteration, second=env.t))
                    return 2
                rows = []
                for _ in range(args.rollout):
                    row = cached_action(policy, obs, args.device)
                    obs, reward, done, _ = env.step(row['modes'].numpy(), row['sizes'].numpy())
                    row.update(reward=reward, done=done)
                    rows.append(row)
                    if time.monotonic()-last_report >= 20:
                        progress = dict(status='collecting', active=day, second=env.t,
                            iteration=iteration, rollout_steps=len(rows), completed=completed)
                        write(root/'status.json', progress)
                        print(json.dumps(progress), flush=True)
                        last_report = time.monotonic()
                    if done or (root/'STOP').exists() or (root.parent/'STOP').exists():
                        break
                with torch.no_grad():
                    bootstrap = 0. if env.done else float(policy(collate([obs], args.device))[2][0])
                measure = update_heads(policy, optimizer, rows, bootstrap, rng, args.device,
                                       epochs=args.epochs, batch_size=args.batch_size)
                iteration += 1
                if env.done and not env.summary()['valid_terminal']:
                    raise ValueError('Unresolved terminal holdings')
                if env.done:
                    completed.append(day)
                result = dict(iteration=iteration, date=day, second=env.t, steps=len(rows),
                    summary=env.summary(), **measure, elapsed_seconds=time.monotonic()-started,
                    evidence='changing-policy-training-returns')
                temporary = root/'checkpoint_latest.tmp'
                torch.save(dict(policy=policy.state_dict(), optimizer=optimizer.state_dict(),
                    environment=env.state_dict(), date=day, completed=list(completed), iteration=iteration,
                    torch_rng=torch.get_rng_state(), numpy_rng=np.random.get_state(),
                    shuffle_rng=rng.bit_generator.state,
                    cuda_rng=torch.cuda.get_rng_state() if args.device == 'cuda' else None,
                    contract_hash=receipt['contract_hash']), temporary)
                os.replace(temporary, root/'checkpoint_latest.pt')
                write(root/'rollouts'/f'{iteration:06d}.json', result)
                del rows
                stopped = (root/'STOP').exists() or (root.parent/'STOP').exists()
                progress = dict(status='stopped' if stopped else 'running', active=day,
                    second=env.t, completed=completed, queued=[d for d in args.dates if d not in completed and d != day],
                    iteration=iteration, elapsed_seconds=time.monotonic()-started)
                write(root/'status.json', progress)
                print(json.dumps(progress), flush=True)
                if stopped:
                    return 2
            del obs, env, session
        write(root/'status.json', dict(status='complete', completed=completed, iteration=iteration,
                                      evidence='changing-policy-training-returns'))
    except BaseException as exc:
        write(root/'status.json', dict(status='failed', completed=completed, iteration=iteration,
                                      error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
