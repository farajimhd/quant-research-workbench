"""Fresh-weight PPO and frozen-policy training-only replay, with exact resume."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import random
import subprocess
import sys
import time
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import numpy as np
import torch
from research.market_shards.v1.contract import digest, sha, write_json
from research.rl_trading.v7.config import Config
from research.rl_trading.v7.data import TrainingDataset
from research.rl_trading.v7.environment import Environment
from research.rl_trading.v7.model import Policy, tensor_batch
from research.rl_trading.v7.stage_data import DEFAULT_DEST


def update(policy, optimizer, rows, bootstrap, config, rng):
    rewards=np.array([r['reward'] for r in rows],np.float64)
    values=np.array([r['value'] for r in rows]+[bootstrap],np.float64)
    advantage=np.zeros(len(rows),np.float64); running=0.
    for i in range(len(rows)-1,-1,-1):
        keep=1-float(rows[i]['done'])
        delta=rewards[i]+config.gamma*values[i+1]*keep-values[i]
        running=delta+config.gamma*config.gae_lambda*keep*running
        advantage[i]=running
    returns=torch.tensor(advantage+values[:-1],dtype=torch.float32)
    advantages=torch.tensor((advantage-advantage.mean())/max(advantage.std(),1e-6),dtype=torch.float32)
    batch=tensor_batch([r['obs'] for r in rows])
    actions=torch.tensor([r['action'] for r in rows])
    old_log=torch.tensor([r['log_prob'] for r in rows])
    records=[]
    for epoch in range(config.ppo_epochs):
        stop=False
        for ix in np.array_split(rng.permutation(len(rows)),max(1,int(np.ceil(len(rows)/config.batch)))):
            dist,value=policy(**{k:v[ix] for k,v in batch.items()})
            log=dist.log_prob(actions[ix]); difference=log-old_log[ix]; ratio=difference.exp()
            kl=((ratio-1)-difference).mean()
            if kl.detach().item()>.02:
                stop=True;break
            actor=-torch.minimum(ratio*advantages[ix],ratio.clamp(.8,1.2)*advantages[ix]).mean()
            critic=(value-returns[ix]).square().mean()
            loss=actor+.5*critic-.001*dist.entropy().mean()
            if not torch.isfinite(loss):raise ValueError('Nonfinite PPO objective')
            optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(policy.parameters(),.5)
            optimizer.step()
            records.append(dict(loss=float(loss.detach()),kl=float(kl.detach()),critic=float(critic.detach())))
        if stop:break
    return dict(updates=len(records),last=records[-1] if records else {},reward_sum=float(rewards.sum()))


def save_checkpoint(path, state):
    temporary=path.with_suffix('.partial')
    torch.save(state,temporary);os.replace(temporary,path)


def source_identity():
    root=Path(__file__).parent
    files={p.name:sha(p) for p in sorted(root.glob('*.py'))}
    for name in ('data.py','contract.py','source.py'):
        path=root.parents[1]/'market_shards'/'v1'/name
        files['shared/'+name]=sha(path)
    files['fees']=sha(root.parent/'v2'/'fees.py')
    return files


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',default=DEFAULT_DEST)
    parser.add_argument('--output',required=True)
    parser.add_argument('--cache',help='Optional prepared-array cache; omitted to bound disk growth')
    parser.add_argument('--dates',nargs='+')
    parser.add_argument('--passes',type=int,default=1)
    parser.add_argument('--seed',type=int,default=1917)
    parser.add_argument('--learning-rate',type=float,default=.0001)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--evaluate',type=Path,help='Frozen checkpoint; only training dates accepted')
    args=parser.parse_args(argv)
    runtime=Path('D:/TradingML/runtimes').resolve();root=Path(args.output).resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Output requires laptop runtime root')
    if args.passes<1:raise ValueError('Positive passes required')
    dataset=TrainingDataset(args.dataset)
    dates=args.dates or dataset.days
    if len(set(dates))!=len(dates) or any(d not in dataset.days for d in dates):
        raise ValueError('Only unique training dates allowed; validation remains sealed')
    config=replace(Config(),seed=args.seed,learning_rate=args.learning_rate)
    contract=dict(config=config.manifest(),dataset=dataset.binding,dates=dates,passes=args.passes,
                  source=source_identity(),mode='frozen-training-replay' if args.evaluate else 'fresh-ppo',
                  evaluation_checkpoint_sha256=sha(args.evaluate) if args.evaluate else None)
    contract_hash=digest(contract)
    if root.exists() and (root/'manifest.json').exists():
        prior=json.loads((root/'manifest.json').read_text())
        if (not args.resume or prior['contract_hash']!=contract_hash
                or digest(prior['contract'])!=prior['contract_hash']):
            raise ValueError('Existing run requires exact-contract resume')
    elif args.resume:raise ValueError('No run to resume')
    root.mkdir(parents=True,exist_ok=True)
    if not (root/'manifest.json').exists():
        commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
        dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip())
        write_json(root/'manifest.json',dict(contract=contract,contract_hash=contract_hash,git_commit=commit,working_tree_dirty=dirty,
                                           arguments=vars(args)|{'evaluate':str(args.evaluate) if args.evaluate else None},
                                           checkpoint='checkpoint_latest.pt',wandb=None,secrets_required=False))
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
    random.seed(args.seed);np.random.seed(args.seed);torch.manual_seed(args.seed)
    rng=np.random.default_rng(args.seed)
    policy=Policy(width=config.width,heads=config.heads)
    optimizer=torch.optim.Adam(policy.parameters(),lr=config.learning_rate)
    if args.evaluate:
        frozen=torch.load(args.evaluate,map_location='cpu',weights_only=False)
        original=frozen['contract']
        if (frozen['contract_hash']!=digest(original) or sha(args.evaluate)!=contract['evaluation_checkpoint_sha256']
                or original['dataset']!=dataset.binding or original['source']!=contract['source']):
            raise ValueError('Evaluation checkpoint source/dataset differs')
        policy.load_state_dict(frozen['policy'])
    schedule=[d for _ in range(args.passes) for d in dates]
    index=iteration=0;restored=None;completed=[]
    if args.resume:
        restored=torch.load(root/'checkpoint_latest.pt',map_location='cpu',weights_only=False)
        if restored['contract_hash']!=contract_hash or digest(restored['contract'])!=contract_hash:
            raise ValueError('Checkpoint contract mismatch')
        policy.load_state_dict(restored['policy']);optimizer.load_state_dict(restored['optimizer'])
        index=restored['index'];iteration=restored['iteration'];completed=restored['completed']
        torch.set_rng_state(restored['torch_rng']);rng.bit_generator.state=restored['numpy_rng']
        write_json(root/'updates'/f'{iteration:06d}.json',restored['update_record'])
        write_json(root/'results.json',completed)
    elif not args.evaluate:
        save_checkpoint(root/'checkpoint_initial.pt',dict(contract=contract,contract_hash=contract_hash,
                                                          policy=policy.state_dict()))
    started=time.monotonic()
    for episode in range(index,len(schedule)):
        day=schedule[episode]
        write_json(root/'status.json',dict(status='preparing',active=day,completed=len(completed),queued=len(schedule)-episode-1))
        session=dataset.load(day,cache_root=Path(args.cache) if args.cache else None)
        env=Environment(session,config)
        if restored is not None and restored.get('environment') is not None:
            env.__dict__.update(restored['environment'])
        restored=None
        while not env.done:
            rows=[]
            for _ in range(config.rollout):
                obs=env.observe()
                with torch.no_grad():
                    dist,value=policy(**tensor_batch([obs]));action=dist.sample()
                    log=dist.log_prob(action)
                _,reward,done,_=env.step(int(action))
                rows.append(dict(obs=obs,action=int(action),reward=reward,done=done,
                                 value=float(value),log_prob=float(log),fills=list(env.last_fills)))
                if done:break
            if rows and not args.evaluate:
                with torch.no_grad():
                    bootstrap=0. if env.done else float(policy(**tensor_batch([env.observe()]))[1])
                metrics=update(policy,optimizer,rows,bootstrap,config,rng)
            else:metrics={}
            iteration+=1
            result=env.result()
            if env.done:
                if not result['valid_terminal']:raise ValueError('Unliquidated terminal account; no qualification')
                completed.append(result|{'episode':episode,'evidence':'frozen-policy' if args.evaluate else 'changing-policy-training'})
            state=dict(contract_hash=contract_hash,contract=contract,policy=policy.state_dict(),
                       optimizer=optimizer.state_dict(),index=episode+1 if env.done else episode,
                       environment=None if env.done else {k:v for k,v in env.__dict__.items() if k not in ('session','config','e')},
                       iteration=iteration,completed=completed,torch_rng=torch.get_rng_state(),numpy_rng=rng.bit_generator.state,
                       update_record=dict(metrics=metrics,account=result,fills=[f for r in rows for f in r['fills']]))
            save_checkpoint(root/'checkpoint_latest.pt',state)
            write_json(root/'updates'/f'{iteration:06d}.json',state['update_record'])
            stopped=(root/'STOP').exists()
            status=dict(status='stopped' if stopped else 'running',active=day,second=env.t,
                        completed=len(completed),queued=len(schedule)-episode-1,iteration=iteration,
                        elapsed_seconds=time.monotonic()-started,return_ratio=result['return_ratio'])
            write_json(root/'status.json',status);print(json.dumps(status),flush=True)
            if stopped:return 2
        write_json(root/'results.json',completed)
    write_json(root/'status.json',dict(status='complete',completed=len(completed),queued=0,
                                     evidence='frozen-policy' if args.evaluate else 'changing-policy-training'))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
