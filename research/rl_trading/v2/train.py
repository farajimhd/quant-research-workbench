"""PPO from agent-generated portfolio trajectories; CPU and CUDA, no teacher loss."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))

import argparse
from dataclasses import fields
import json
import random
import time
import numpy as np
import torch

from research.rl_trading.v2.config import Config, VERSION
from research.rl_trading.v2.data import MarketSession, chronological
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.model import PortfolioPolicy, collate
from research.rl_trading.v2.objectives import advantages, ppo_loss
from research.rl_trading.v2.io import code_identity, digest, exclusive, file_hash, output_root, read, write


def config_arguments(p):
    for field in fields(Config):
        p.add_argument('--'+field.name.replace('_','-'),type=field.type,default=field.default)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--train-sessions',type=Path,nargs='+',required=True)
    p.add_argument('--val-sessions',type=Path,nargs='+',required=True)
    p.add_argument('--run-name',required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--iterations',type=int,default=1000)
    p.add_argument('--rollout-steps',type=int,default=256)
    p.add_argument('--environments',type=int,default=4)
    p.add_argument('--epochs',type=int,default=4)
    p.add_argument('--batch-size',type=int,default=32)
    p.add_argument('--width',type=int,default=64)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--seed',type=int,default=17)
    p.add_argument('--threads',type=int,default=2)
    p.add_argument('--learning-rate',type=float,default=3e-4)
    p.add_argument('--gae-lambda',type=float,default=.95)
    p.add_argument('--clip',type=float,default=.2)
    p.add_argument('--entropy-weight',type=float,default=.001)
    p.add_argument('--target-kl',type=float,default=.03)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--capital-multipliers',type=float,nargs='+',default=[.5,1.,2.])
    p.add_argument('--resume',action='store_true')
    p.add_argument('--allow-segment',action='store_true')
    config_arguments(p)
    return p


def evaluate(policy, sessions, config, device):
    result = []
    policy.eval()
    with torch.no_grad():
        for session in sessions:
            env = TradingEnv(session,config)
            obs = env.observe()
            last_report = time.monotonic()
            while not env.done:
                modes,sizes,_,_,_ = policy.action(collate([obs],device),deterministic=True)
                obs,_,_,_ = env.step(modes[0].cpu().numpy(),sizes[0].cpu().numpy())
                if time.monotonic()-last_report > 20:
                    print(f"Validation {session.plan['date']} second={env.t}/{session.seconds-1}",flush=True)
                    last_report = time.monotonic()
            summary = dict(date=session.plan['date'],**env.summary())
            if not summary['valid_terminal']:
                raise ValueError(f"Unresolved terminal liquidation: {summary}")
            result.append(summary)
    policy.train()
    return result


def _save(path, payload):
    temporary = path.with_suffix('.tmp')
    torch.save(payload,temporary)
    os.replace(temporary,path)


def train(args):
    for name in ('iterations','rollout_steps','environments','epochs','batch_size','eval_every','threads'):
        if getattr(args,name) < 1:
            raise ValueError(name+' must be positive')
    if (not 0 < args.gae_lambda <= 1 or not 0 < args.clip < 1
            or not np.isfinite(args.learning_rate) or args.learning_rate <= 0
            or not np.isfinite(args.entropy_weight) or args.entropy_weight < 0
            or not np.isfinite(args.target_kl) or args.target_kl <= 0
            or any(not np.isfinite(x) or x <= 0 for x in args.capital_multipliers)):
        raise ValueError('Invalid PPO parameters')
    if not args.run_name or Path(args.run_name).name != args.run_name or args.run_name in ('.','..'):
        raise ValueError('Run name must be a single directory name')
    config = Config(**{field.name:getattr(args,field.name) for field in fields(Config)})
    root = output_root()/'train'/args.run_name
    root.mkdir(parents=True,exist_ok=True)
    with exclusive(root/'train.lock'):
        return _train_locked(args,config,root)


def _train_locked(args, config, root):
    torch.set_num_threads(args.threads)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA requested but unavailable')
    torch.use_deterministic_algorithms(True)
    sessions = [MarketSession.load(p,allow_segment=args.allow_segment) for p in args.train_sessions]
    validation = [MarketSession.load(p,allow_segment=args.allow_segment) for p in args.val_sessions]
    chronological(sessions,validation)
    contract_args = {k:v for k,v in vars(args).items() if k not in ('resume','iterations','run_name','train_sessions','val_sessions')}
    manifest = dict(version=VERSION,job='train',config=config.manifest(),arguments=contract_args,
        model=dict(features=len(sessions[0].plan['feature_names']),width=args.width,heads=args.heads),
        feature_names=sessions[0].plan['feature_names'],code=code_identity(),
        train=[dict(root=str(x.root),plan_hash=x.plan['plan_hash'],complete_hash=file_hash(x.root/'complete.json'),date=x.plan['date']) for x in sessions],
        validation=[dict(root=str(x.root),plan_hash=x.plan['plan_hash'],complete_hash=file_hash(x.root/'complete.json'),date=x.plan['date']) for x in validation],
        output_root=str(root),wandb=None,teacher_supervision=False,
        torch_version=torch.__version__,numpy_version=np.__version__)
    manifest['contract_hash'] = digest(manifest)
    path = root/'run_manifest.json'
    if path.exists():
        if not args.resume:
            raise ValueError('Run already exists; use --resume with the same contract')
        if read(path) != json.loads(json.dumps(manifest)):
            raise ValueError('Resume contract, code, data, or environment changed')
    else:
        if args.resume:
            raise ValueError('Cannot resume a missing run')
        write(path,manifest)
    policy = PortfolioPolicy(**manifest['model']).to(args.device)
    optimizer = torch.optim.Adam(policy.parameters(),lr=args.learning_rate)
    rng = np.random.default_rng(args.seed)
    envs, session_indices = [], []

    def new_env():
        index = int(rng.integers(len(sessions)))
        cash = config.initial_cash*float(rng.choice(args.capital_multipliers))
        return index,TradingEnv(sessions[index],config,initial_cash=cash)

    for _ in range(args.environments):
        index,env = new_env()
        session_indices.append(index)
        envs.append(env)
    start, best, completed_episodes = 0, -float('inf'), 0
    latest = root/'checkpoint_latest.pt'

    def snapshot(iteration):
        return dict(contract_hash=manifest['contract_hash'],iteration=iteration,best=best,
            completed_episodes=completed_episodes,
            policy=policy.state_dict(),optimizer=optimizer.state_dict(),rng=rng.bit_generator.state,
            python_rng=random.getstate(),numpy_rng=np.random.get_state(),torch_rng=torch.get_rng_state(),
            cuda_rng=torch.cuda.get_rng_state_all() if args.device == 'cuda' else [],
            session_indices=session_indices,environments=[env.state_dict() for env in envs])

    if args.resume:
        saved = torch.load(latest,map_location=args.device,weights_only=False)
        if saved['contract_hash'] != manifest['contract_hash']:
            raise ValueError('Checkpoint contract mismatch')
        policy.load_state_dict(saved['policy'])
        optimizer.load_state_dict(saved['optimizer'])
        start, best = saved['iteration'], saved['best']
        completed_episodes = saved['completed_episodes']
        rng.bit_generator.state = saved['rng']
        random.setstate(saved['python_rng'])
        np.random.set_state(saved['numpy_rng'])
        torch.set_rng_state(saved['torch_rng'].cpu())
        if args.device == 'cuda':
            torch.cuda.set_rng_state_all([x.cpu() for x in saved['cuda_rng']])
        session_indices = saved['session_indices']
        for slot,index in enumerate(session_indices):
            envs[slot] = TradingEnv(sessions[index],config)
            envs[slot].load_state_dict(saved['environments'][slot])
    else:
        # Even interruption in the first rollout/validation has a restart point.
        _save(latest,snapshot(0))
    if start >= args.iterations:
        print(f'Checkpoint already reached iteration {start}',flush=True)
        return 0
    print(f'V2 PPO device={args.device} train_sessions={len(sessions)} validation_sessions={len(validation)} root={root}',flush=True)
    print('Costs/slippage are uncalibrated price-only assumptions: '+json.dumps(config.manifest()),flush=True)
    try:
        for iteration in range(start+1,args.iterations+1):
            if (root/'STOP').exists():
                write(root/'status.json',dict(status='stopped',iteration=iteration-1,active=0))
                return 2
            began = time.monotonic()
            trajectories = [[] for _ in envs]
            summaries = []
            last_report = began
            for step in range(args.rollout_steps):
                observations = [env.observe() for env in envs]
                with torch.no_grad():
                    modes,sizes,logprobs,_,values = policy.action(collate(observations,args.device))
                for slot,env in enumerate(envs):
                    count = len(observations[slot]['ids'])
                    mode = modes[slot,:count].cpu().numpy()
                    size = sizes[slot,:count].cpu().numpy()
                    _,reward,done,summary = env.step(mode,size)
                    trajectories[slot].append(dict(obs=observations[slot],modes=mode,sizes=size,
                        logprob=float(logprobs[slot]),value=float(values[slot]),reward=reward,done=done))
                    if done:
                        if not summary['valid_terminal']:
                            raise ValueError(f'Unresolved terminal holdings in training: {summary}')
                        summaries.append(dict(date=env.session.plan['date'],**summary))
                        completed_episodes += 1
                        session_indices[slot],envs[slot] = new_env()
                if time.monotonic()-last_report > 20:
                    print(f'Iteration {iteration} rollout={step+1}/{args.rollout_steps} active={len(envs)} episodes={completed_episodes}',flush=True)
                    last_report = time.monotonic()
            with torch.no_grad():
                _,_,_,_,bootstrap = policy.action(collate([env.observe() for env in envs],args.device),deterministic=True)
            rows = []
            for slot,trajectory in enumerate(trajectories):
                adv,returns = advantages([x['reward'] for x in trajectory],[x['value'] for x in trajectory],
                    [x['done'] for x in trajectory],float(bootstrap[slot]),gae_lambda=args.gae_lambda)
                for row,advantage,target in zip(trajectory,adv,returns):
                    row.update(advantage=float(advantage),target=float(target))
                    rows.append(row)
            mean = np.mean([x['advantage'] for x in rows])
            std = np.std([x['advantage'] for x in rows])
            measures = []
            early_stop = False
            for epoch in range(args.epochs):
                for offset in range(0,len(rows),args.batch_size):
                    if offset == 0:
                        permutation = rng.permutation(len(rows))
                    batch_rows = [rows[i] for i in permutation[offset:offset+args.batch_size]]
                    batch = collate([x['obs'] for x in batch_rows],args.device)
                    width = batch['valid'].shape[1]
                    mode = np.zeros((len(batch_rows),width),dtype=np.int64)
                    size = np.full((len(batch_rows),width),.5,dtype=np.float32)
                    for i,row in enumerate(batch_rows):
                        mode[i,:len(row['modes'])] = row['modes']
                        size[i,:len(row['sizes'])] = row['sizes']
                    _,_,logprob,entropy,value = policy.action(batch,
                        torch.as_tensor(mode,device=args.device),torch.as_tensor(size,device=args.device))
                    def tensor(key):
                        return torch.tensor([x[key] for x in batch_rows],dtype=torch.float32,device=args.device)
                    loss,measure = ppo_loss(logprob,tensor('logprob'),(tensor('advantage')-mean)/max(std,1e-8),
                        value,tensor('target'),entropy,clip=args.clip,entropy_weight=args.entropy_weight)
                    if measure['approx_kl'] > args.target_kl:
                        early_stop = True
                        break
                    optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(policy.parameters(),.5,error_if_nonfinite=True)
                    optimizer.step()
                    measures.append(measure)
                if early_stop:
                    break
            result = dict(iteration=iteration,completed_episodes=completed_episodes,episodes=summaries,
                active_accounts=[dict(date=e.session.plan['date'],**e.summary()) for e in envs],
                rollout_steps=len(rows),updates=len(measures),kl_early_stop=early_stop,
                losses={k:float(np.mean([m[k] for m in measures])) for k in measures[0]} if measures else {})
            improved = False
            if iteration == 1 or iteration % args.eval_every == 0 or iteration == args.iterations:
                result['validation'] = evaluate(policy,validation,config,args.device)
                score = float(np.mean([x['net_return'] for x in result['validation']]))
                improved = score > best
                best = max(best,score)
                result['validation_mean_return'] = score
            result['elapsed_seconds'] = time.monotonic()-began
            payload = snapshot(iteration)
            _save(latest,payload)
            if improved:
                _save(root/'checkpoint_best.pt',payload)
            write(root/'metrics'/f'{iteration:06d}.json',result)
            write(root/'status.json',dict(status='running',iteration=iteration,active=len(envs),
                queued_iterations=args.iterations-iteration,completed_episodes=completed_episodes,
                failed=0,retried=0,skipped=0))
            print(f"Iteration {iteration}/{args.iterations} steps={len(rows)} updates={len(measures)} episodes={completed_episodes} validation={result.get('validation_mean_return','not scheduled')} seconds={result['elapsed_seconds']:.1f}",flush=True)
    except KeyboardInterrupt:
        write(root/'status.json',dict(status='interrupted',active=0,resume='last committed iteration'))
        return 2
    except Exception:
        write(root/'status.json',dict(status='failed',active=0,failed=1,resume='last committed iteration'))
        raise
    write(root/'status.json',dict(status='complete',iteration=args.iterations,active=0,failed=0))
    return 0


def main(argv=None):
    return train(parser().parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
