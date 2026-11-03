"""Batched own-account PPO with immutable forecast/encoder and CPU head caches.

An update follows complete independent episodes under one policy snapshot.
Restart repeats an uncommitted batch from its boundary checkpoint. Stochastic
sampling is reproducible within this batching contract, not serial RNG parity.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
os.environ.setdefault('POLARS_MAX_THREADS', '2')
import sys
sys.dont_write_bytecode = True
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import argparse
from concurrent.futures import ThreadPoolExecutor, wait
import gc
import json
import random
import subprocess
import time

import numpy as np
import psutil
import torch
from torch.distributions import Categorical

from research.market_shards.v1.contract import digest, sha, write_json
from research.rl_trading.v7.config import Config
from research.rl_trading.v7.data import TrainingDataset
from research.rl_trading.v7.model import tensor_batch
from research.rl_trading.v7.stage_data import DEFAULT_DEST
from research.rl_trading.v7.train import save_checkpoint, source_identity
from research.rl_trading.v7.experiments.cost_aware import CostEnvironment
from research.rl_trading.v7.experiments.predictive_model import PredictivePolicy
from research.rl_trading.v7.experiments.predictive_ppo import load_forecast

HEADS = ('buy', 'control', 'value')


def freeze(policy):
    policy.eval()
    for name, parameter in policy.named_parameters():
        parameter.requires_grad_(name.split('.')[0] in HEADS)
    return [p for p in policy.parameters() if p.requires_grad]


def cached_inputs(policy, batch):
    tokens = policy.encode(**{k: batch[k] for k in ('features', 'account', 'holding', 'candidate_valid')})
    prediction = policy.forecast(tokens[:, :5])
    costs = batch['features'][:, :, -14:]
    ratio = torch.exp(prediction.clamp(-50, 50)/100).mean(-1)
    net = 100*((1-costs[:, :, 4]/100)*ratio-1)-costs[:, :, 2]-costs[:, :, 3]
    return torch.cat((tokens[:, :5], tokens[:, -1:]), dim=1), 10*net


def head_forward(policy, tokens, offset, mask):
    buy = policy.buy(tokens[:, :5]).squeeze(-1) + offset
    control = policy.control(tokens[:, -1])
    logits = torch.cat((control[:, :1], buy, control[:, 1:]), dim=1)
    return Categorical(logits=logits.masked_fill(~mask, -1e9)), policy.value(tokens[:, -1]).squeeze(-1)


def memory_estimate(dataset, days, workers, width=64):
    sessions = [57600*len(dataset.items[d]['selected'])*49 + 19800*5*98*4 for d in days]
    # Full execution/feature grids; two cache copies during concatenation; bounded
    # reader scratch/cache for the largest simultaneous loaders; 1GiB reserve.
    cache = len(days)*57600*(6*width*4+5*4+7+8+4+4+8+1)
    scratch = sum(sorted(sessions, reverse=True)[:workers])*.5 + workers*512*2**20
    return int(sum(sessions) + 2*cache + scratch + 2**30)


def peak_memory(device):
    memory = psutil.Process().memory_info()
    return dict(cpu_peak_working_set_bytes=int(getattr(memory, 'peak_wset', memory.rss)),
                cuda_peak_allocated_bytes=int(torch.cuda.max_memory_allocated()) if device == 'cuda' else 0)


def prepare(dataset, days, workers, notify):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(dataset.load, day) for day in days]
        pending = set(futures)
        last = time.monotonic()
        while pending:
            _, pending = wait(pending, timeout=1)
            if time.monotonic()-last >= 20:
                notify('preparing', dict(loaded=len(futures)-len(pending), active_dates=days))
                last = time.monotonic()
        return [future.result() for future in futures]


def collect(policy, sessions, config, device, notify, *, greedy=False, retain=True):
    envs = [CostEnvironment(session, config) for session in sessions]
    fills = [[] for _ in sessions]
    trajectories = []
    width = policy.value.in_features
    for session in sessions:
        n = len(session.clocks)
        trajectories.append(dict(tokens=np.empty((n, 6, width), np.float32),
            offset=np.empty((n, 5), np.float32), mask=np.empty((n, 7), bool),
            action=np.empty(n, np.int64), logprob=np.empty(n, np.float32),
            value=np.empty(n, np.float32), reward=np.empty(n, np.float64), done=np.empty(n, bool), count=0)
            if retain else None)
    active = list(range(len(envs)))
    last = time.monotonic()
    while active:
        observations = [envs[i].observe() for i in active]
        batch = tensor_batch(observations, device)
        with torch.no_grad():
            tokens, offset = cached_inputs(policy, batch)
            dist, values = head_forward(policy, tokens, offset, batch['action_mask'])
            actions = dist.probs.argmax(-1) if greedy else dist.sample()
            logprob = dist.log_prob(actions)
            actions_cpu = actions.cpu().numpy()
            if retain:
                tc, oc = tokens.cpu().numpy(), offset.cpu().numpy()
                lc, vc = logprob.cpu().numpy(), values.cpu().numpy()
        following = []
        for slot, index in enumerate(active):
            env = envs[index]
            _, reward, done, _ = env.step(int(actions_cpu[slot]))
            fills[index].extend(env.last_fills)
            if retain:
                row = trajectories[index]
                t = row['count']
                row['tokens'][t], row['offset'][t] = tc[slot], oc[slot]
                row['mask'][t] = observations[slot]['action_mask']
                row['action'][t], row['logprob'][t], row['value'][t] = actions_cpu[slot], lc[slot], vc[slot]
                row['reward'][t], row['done'][t], row['count'] = reward, done, t+1
            if not done:
                following.append(index)
            elif not env.result()['valid_terminal']:
                raise ValueError('Unliquidated terminal account: '+session_day(env))
        active = following
        if time.monotonic()-last >= 20:
            notify('collecting', dict(active_dates=[session_day(envs[i]) for i in active],
                seconds={session_day(e): e.t for e in envs}, batch_terminal=len(envs)-len(active)))
            last = time.monotonic()
    return trajectories, [dict(env.result(), fill_records=fills[i]) for i, env in enumerate(envs)]


def session_day(env):
    return env.session.day


def update(policy, optimizer, trajectories, rng, device, minibatch, epochs, notify):
    keys = ('tokens', 'offset', 'mask', 'action', 'logprob', 'value')
    arrays = {key: np.concatenate([row[key][:row['count']] for row in trajectories]) for key in keys}
    targets = []
    for row in trajectories:
        n = row['count']
        if not n or not row['done'][n-1] or np.any(row['done'][:n-1]):
            raise ValueError('Expected exactly one complete account trajectory')
        targets.append(np.cumsum(row['reward'][:n][::-1], dtype=np.float64)[::-1].astype(np.float32))
    returns = np.concatenate(targets)
    advantage = returns-arrays['value']
    advantage = (advantage-advantage.mean())/max(float(advantage.std()), 1e-8)
    records, stopped = [], False
    last = time.monotonic()
    n = len(returns)
    for epoch in range(epochs):
        order = rng.permutation(n)
        for begin in range(0, n, minibatch):
            ix = order[begin:begin+minibatch]
            batch = {key: torch.as_tensor(value[ix], device=device) for key, value in arrays.items()}
            dist, value = head_forward(policy, batch['tokens'], batch['offset'], batch['mask'])
            logratio = dist.log_prob(batch['action'])-batch['logprob']
            ratio = logratio.exp()
            kl = ((ratio-1)-logratio).mean()
            if not torch.isfinite(kl):
                raise ValueError('Nonfinite joint policy KL')
            if float(kl.detach()) > .02:
                stopped = True
                break
            adv = torch.as_tensor(advantage[ix], device=device)
            actor = -torch.minimum(ratio*adv, ratio.clamp(.8, 1.2)*adv).mean()
            critic = (value-torch.as_tensor(returns[ix], device=device)).square().mean()
            entropy = dist.entropy().mean()
            loss = actor+.5*critic-.001*entropy
            if not torch.isfinite(loss):
                raise ValueError('Nonfinite own-reward PPO loss')
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in policy.parameters() if p.requires_grad], .5, error_if_nonfinite=True)
            optimizer.step()
            records.append(dict(actor=float(actor.detach()), critic=float(critic.detach()),
                entropy=float(entropy.detach()), kl=float(kl.detach()),
                clip_fraction=float(((ratio < .8) | (ratio > 1.2)).float().mean().detach())))
            if time.monotonic()-last >= 20:
                notify('updating', dict(epoch=epoch+1, minibatches=len(records), transitions=n))
                last = time.monotonic()
        if stopped:
            break
    return dict(transitions=n, updates=len(records), kl_early_stop=stopped,
        mean={key: float(np.mean([r[key] for r in records])) for key in records[0]} if records else {})


def identity(forecast_hash, forecast_contract, dataset, config):
    here = Path(__file__).parent
    return dict(dataset=dataset.binding, risk=config.manifest(), forecast_sha256=forecast_hash,
        forecast_contract=forecast_contract, base_source=source_identity(),
        source={name: sha(here/name) for name in ('batched_ppo.py', 'predictive_model.py', 'predictive_ppo.py', 'cost_aware.py')})


def publish(root, state):
    keep = {f'{i+1:06d}.json': row for i, row in enumerate(state['metrics'])}
    suffix = []
    for path in (root/'batches').glob('*.json'):
        if path.name not in keep:
            suffix.append(path)
        elif json.loads(path.read_text()) != keep[path.name]:
            raise ValueError('Committed batch metric differs from checkpoint')
    if suffix:
        archive = root/'uncommitted'/str(time.time_ns())
        archive.mkdir(parents=True)
        for path in suffix:
            path.rename(archive/path.name)
    for name, row in keep.items():
        path = root/'batches'/name
        if not path.exists():
            write_json(path, row)
    write_json(root/'results.json', state['results'])


def state_dict(policy, optimizer, rng, contract, index, results, metrics, device):
    return dict(policy=policy.state_dict(), optimizer=optimizer.state_dict(), contract=contract,
        contract_hash=digest(contract), index=index, results=results, metrics=metrics,
        torch_rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all() if device == 'cuda' else [],
        numpy_rng=np.random.get_state(), python_rng=random.getstate(), shuffle_rng=rng.bit_generator.state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default=DEFAULT_DEST)
    parser.add_argument('--forecast', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dates', nargs='+')
    parser.add_argument('--environments', type=int, default=4)
    parser.add_argument('--prepare-workers', type=int, default=2)
    parser.add_argument('--memory-gib', type=float, default=8.)
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    parser.add_argument('--seed', type=int, default=1917)
    parser.add_argument('--learning-rate', type=float, default=1e-4)
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--minibatch', type=int, choices=(256, 512), default=512)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--evaluate', type=Path)
    parser.add_argument('--greedy', action='store_true')
    args = parser.parse_args(argv)
    if (not 1 <= args.environments <= 8 or not 1 <= args.prepare_workers <= 2 or
            not np.isfinite(args.memory_gib) or not 0 < args.memory_gib <= 8 or
            not 1 <= args.epochs <= 3 or not np.isfinite(args.learning_rate) or args.learning_rate <= 0 or
            args.seed < 0 or (args.greedy and args.evaluate is None)):
        raise ValueError('Invalid bounded run configuration; greedy requires frozen evaluation')
    root = args.output.resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Runtime output required')
    dataset = TrainingDataset(args.dataset)
    dates = args.dates or list(dataset.days)
    if (not dates or len(set(dates)) != len(dates) or any(d not in dataset.days for d in dates) or
            dates != [d for d in dataset.days if d in set(dates)]):
        raise ValueError('Require unique chronologically ordered TRAINING dates')
    for begin in range(0, len(dates), args.environments):
        estimate = memory_estimate(dataset, dates[begin:begin+args.environments], args.prepare_workers)
        if estimate > args.memory_gib*2**30:
            raise MemoryError(f'Batch requires estimated {estimate/2**30:.3f}GiB; declared budget insufficient')
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA requested but unavailable; CPU requires explicit --device cpu')
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    rng = np.random.default_rng(args.seed)
    saved, forecast_hash = load_forecast(args.forecast, dataset)
    config = Config()
    policy = PredictivePolicy().to(args.device)
    policy.load_state_dict(saved['policy'])
    optimizer = torch.optim.Adam(freeze(policy), lr=args.learning_rate)
    authority = identity(forecast_hash, saved['contract_hash'], dataset, config)
    evaluation_hash = None
    if args.evaluate:
        evaluation_hash = sha(args.evaluate)
        frozen = torch.load(args.evaluate, map_location='cpu', weights_only=False)
        if (frozen['contract_hash'] != digest(frozen['contract']) or frozen['contract']['authority'] != authority or
                sha(args.evaluate) != evaluation_hash):
            raise ValueError('Frozen checkpoint model/source/data/risk/forecast contract mismatch')
        policy.load_state_dict(frozen['policy'])
    contract = dict(version='v7-batched-frozen-encoder-own-reward-ppo-1', authority=authority,
        dates=dates, environments=args.environments, prepare_workers=args.prepare_workers,
        memory_gib=args.memory_gib, device=args.device, seed=args.seed,
        learning_rate=args.learning_rate, epochs=args.epochs, minibatch=args.minibatch,
        gamma=1., gae_lambda=1., clip=.2, entropy_weight=.001, value_weight=.5, max_grad_norm=.5, target_kl=.02,
        optimizer='Adam', trainable_heads=list(HEADS), torch_version=torch.__version__, numpy_version=np.__version__,
        mode='frozen-replay' if args.evaluate else 'own-reward-head-ppo', decision_rule='greedy' if args.greedy else 'stochastic',
        evaluation_checkpoint_sha256=evaluation_hash,
        labels=dict(initialization='supervised future-mark prediction within approved TRAINING dates',
                    ppo='own net account rewards only; no supervised targets read during PPO',
                    observations='causal current/past inputs only'),
        evidence='frozen-policy-account-replay' if args.evaluate else 'changing-policy-training-accounts')
    contract_hash = digest(contract)
    index, results, metrics = 0, [], []
    if args.resume:
        manifest = json.loads((root/'manifest.json').read_text())
        if manifest['contract_hash'] != contract_hash or digest(manifest['contract']) != contract_hash:
            raise ValueError('Exact run contract required for resume')
        restored = torch.load(root/'checkpoint_latest.pt', map_location='cpu', weights_only=False)
        if restored['contract_hash'] != contract_hash or digest(restored['contract']) != contract_hash:
            raise ValueError('Checkpoint resume contract mismatch')
        index, results, metrics = restored['index'], restored['results'], restored['metrics']
        if index != len(results) or [r['day'] for r in results] != dates[:index] or not 0 <= index <= len(dates):
            raise ValueError('Invalid committed date cursor')
        policy.load_state_dict(restored['policy'])
        optimizer.load_state_dict(restored['optimizer'])
        torch.set_rng_state(restored['torch_rng'])
        if args.device == 'cuda':
            torch.cuda.set_rng_state_all(restored['cuda_rng'])
        np.random.set_state(restored['numpy_rng'])
        random.setstate(restored['python_rng'])
        rng.bit_generator.state = restored['shuffle_rng']
        publish(root, restored)
    else:
        root.mkdir(parents=True, exist_ok=False)
        write_json(root/'manifest.json', dict(contract=contract, contract_hash=contract_hash,
            git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                cwd=Path(__file__).resolve().parents[4], text=True).strip(),
            resolved_paths=dict(dataset=str(Path(args.dataset).resolve()), output=str(root),
                forecast=str(args.forecast.resolve()),
                evaluate=str(args.evaluate.resolve()) if args.evaluate else None)))
        (root/'driver_source.py').write_bytes(Path(__file__).read_bytes())
        initial = state_dict(policy, optimizer, rng, contract, 0, [], [], args.device)
        save_checkpoint(root/'checkpoint_initial.pt', initial)
        save_checkpoint(root/'checkpoint_latest.pt', initial)
        publish(root, initial)
    if any(not torch.isfinite(v).all() for v in policy.state_dict().values()):
        raise ValueError('Nonfinite policy checkpoint')
    if args.device == 'cuda':
        torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    active_batch, batch_terminal = [], 0

    def notify(phase, extra=None):
        nonlocal batch_terminal
        if extra and 'batch_terminal' in extra:
            batch_terminal = extra['batch_terminal']
        payload = dict(status=phase, completed=index, queued=len(dates)-index-len(active_batch),
            active_batch_dates=list(active_batch), uncommitted_terminal=batch_terminal,
            failed=0, elapsed_seconds=time.monotonic()-started, **peak_memory(args.device))
        payload.update(extra or {})
        write_json(root/'status.json', payload)
        print(json.dumps(payload), flush=True)

    try:
        while index < len(dates):
            if (root/'STOP').exists():
                notify('stopped')
                return 2
            current = dates[index:index+args.environments]
            active_batch, batch_terminal = list(current), 0
            estimate = memory_estimate(dataset, current, args.prepare_workers)
            available = psutil.virtual_memory().available
            if estimate > available:
                raise MemoryError(f'Batch estimate {estimate} bytes exceeds available RAM {available} bytes')
            notify('preparing', dict(active_dates=current, estimated_host_bytes=estimate,
                                    available_host_bytes=available))
            batch_started = time.monotonic()
            sessions = prepare(dataset, current, args.prepare_workers, notify)
            prepared = time.monotonic()
            notify('collecting', dict(active_dates=current, preparation_seconds=prepared-batch_started))
            trajectories, outcomes = collect(policy, sessions, config, args.device, notify,
                                            greedy=args.greedy, retain=args.evaluate is None)
            collected = time.monotonic()
            notify('replay-finished' if args.evaluate else 'updating',
                   dict(active_dates=[], batch_terminal=len(current)))
            measures = {} if args.evaluate else update(policy, optimizer, trajectories, rng, args.device,
                                                       args.minibatch, args.epochs, notify)
            results.extend(outcomes)
            index += len(current)
            active_batch, batch_terminal = [], 0
            metrics.append(dict(batch=len(metrics)+1, dates=current, completed=index,
                preparation_seconds=prepared-batch_started, collection_seconds=collected-prepared,
                update_seconds=time.monotonic()-collected, outcomes=outcomes, optimization=measures,
                estimated_host_bytes=estimate, **peak_memory(args.device)))
            committed = state_dict(policy, optimizer, rng, contract, index, results, metrics, args.device)
            save_checkpoint(root/'checkpoint_latest.pt', committed)
            publish(root, committed)
            del sessions, trajectories, committed
            gc.collect()
            if (root/'STOP').exists():
                notify('stopped')
                return 2
            notify('committed', dict(active_dates=[]))
        notify('complete', dict(evidence=contract['evidence']))
    except BaseException as exc:
        notify('failed', dict(failed=1, error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
