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
import gc
import json
import random
import time
from types import SimpleNamespace
import numpy as np
import torch

from research.rl_trading.v2.config import Config, VERSION
from research.rl_trading.v2.data import ARRAYS, DATA_VERSION, MarketSession, chronological
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.model import PortfolioPolicy, collate
from research.rl_trading.v2.objectives import advantages, ppo_loss
from research.rl_trading.v2.io import code_identity, digest, exclusive, file_hash, output_root, read, write
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.wandb_utils import init_wandb


# The frozen one-pass pilot predates explicit versioned continuation. Other
# source bytes must still match exactly; this is its certified controller hash.
PILOT_TRAIN_HASH = 'c814a0a450b7688f9561f922fa443eab7e5a1fbbe3cbe308fc0debd3046067ed'
# V8 controller bytes are the only accepted parent for a changed-epoch
# continuation. All model, data, and execution source files remain identical.
V8_TRAIN_HASH = 'c5dcf14ad1456313a69c99a254e2c86acf34a1cd6a5573d7a9a5773f8bbeeb94'
PRE_EARLY_EXIT_HASHES = {
    str(Path('research/rl_trading/v2/config.py')): '42141072c818fbea0fc164d6cdf08f5d3079fec7fbb42aaeeb0fab8cbe8b97b5',
    str(Path('research/rl_trading/v2/train.py')): 'f65b8861c8533a8f666471d560cff9ae24bb0c5b75c1d50ca73a3a996e54b50c',
}
BALANCED_ACTION_PARENT_HASHES = {
    str(Path('research/rl_trading/v2/config.py')): '19c996d072e91872a61826580b99934dec0ebecd874fb30da4f72a5812f5f047',
    str(Path('research/rl_trading/v2/model.py')): '4adbe145cce55244396509bff2d8e89dcc7ea46e3e989d4ed6dafc597afc2362',
    str(Path('research/rl_trading/v2/train.py')): '719bf821da6fae635084d96ef27840ccb15c2e86a0cc5641b9a791b218c6aff6',
    str(Path('research/rl_trading/v2/environment.py')): '3eca8a7db4d37485e6f38cf87cab273329da31a7f4a58d1adabe6efac77f1b0d',
}


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
    p.add_argument('--rollout-steps',type=int,default=512)
    p.add_argument('--environments',type=int,default=4)
    p.add_argument('--epochs',type=int,default=4)
    p.add_argument('--batch-size',type=int,default=32)
    p.add_argument('--width',type=int,default=64)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--seed',type=int,default=17)
    p.add_argument('--threads',type=int,default=2)
    p.add_argument('--learning-rate',type=float,default=3e-4)
    p.add_argument('--gae-lambda',type=float,default=1.)
    p.add_argument('--clip',type=float,default=.2)
    p.add_argument('--entropy-weight',type=float,default=.001)
    p.add_argument('--target-kl',type=float,default=.03)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--validation-rollouts',type=int,default=3)
    p.add_argument('--selection-mode',choices=('legacy_mean','robust_q25'),default='legacy_mean')
    p.add_argument('--validation-seed',type=int,default=1917)
    p.add_argument('--capital-multipliers',type=float,nargs='+',default=[.5,1.,2.])
    p.add_argument('--session-order',choices=('random','cycle'),default='random')
    p.add_argument('--min-completed-episodes',type=int,default=0)
    p.add_argument('--selection-min-episodes',type=int,default=0)
    p.add_argument('--stream-sessions',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--continue-from-run',type=Path)
    p.add_argument('--continue-with-more-epochs-from-run',type=Path)
    p.add_argument('--initialize-from-best',type=Path)
    p.add_argument('--initialize-hierarchical-from-best',type=Path)
    p.add_argument('--allow-segment',action='store_true')
    p.add_argument('--wandb-mode',choices=('disabled','offline','online'),default='disabled')
    p.add_argument('--wandb-project',default='rl-trading-v2')
    p.add_argument('--wandb-entity',default='')
    config_arguments(p)
    return p


def evaluate(policy, sessions, config, device, *, rollouts=3, seed=1917,
             allow_segment=False):
    if rollouts < 1 or seed < 0:
        raise ValueError('Invalid fixed-seed validation contract')
    result = []
    was_training = policy.training
    policy.eval()
    devices = [torch.cuda.current_device()] if torch.device(device).type == 'cuda' else []
    try:
        with torch.no_grad(), torch.random.fork_rng(devices=devices):
            for source in sessions:
                session = (source if isinstance(source,MarketSession) else
                           MarketSession.load(source.root,allow_segment=allow_segment))
                for replicate in range(rollouts):
                    # Common random numbers make checkpoint comparisons repeatable
                    # without changing the training RNG or hiding stochastic trades.
                    sample_seed = seed + int(session.plan['date'].replace('-',''))*rollouts + replicate
                    torch.manual_seed(sample_seed)
                    env = TradingEnv(session,config)
                    obs = env.observe()
                    last_report = time.monotonic()
                    while not env.done:
                        modes,sizes,_,_,_ = policy.action(collate([obs],device))
                        obs,_,_,_ = env.step(modes[0].cpu().numpy(),sizes[0].cpu().numpy())
                        if time.monotonic()-last_report > 20:
                            print(f"Validation {session.plan['date']} replicate={replicate+1}/{rollouts} second={env.t}/{session.seconds-1}",flush=True)
                            last_report = time.monotonic()
                    summary = dict(date=session.plan['date'],replicate=replicate+1,**env.summary())
                    result.append(summary)
                if session is not source:
                    del env
                    del session
                    gc.collect()
    finally:
        policy.train(was_training)
    return result


def _save(path, payload):
    temporary = path.with_suffix('.tmp')
    torch.save(payload,temporary)
    os.replace(temporary,path)


def wandb_metrics(result):
    """Project durable per-iteration evidence to scalar W&B metrics."""
    summaries = result['episodes']
    report = dict(iteration=result['iteration'],episodes_completed=result['completed_episodes'],
        updates=result['updates'],rollout_steps=result['rollout_steps'],
        elapsed_seconds=result['elapsed_seconds'],kl_early_stop=int(result['kl_early_stop']))
    report.update({'loss/'+k:v for k,v in result['losses'].items()})
    if summaries:
        report['train/net_return_mean'] = float(np.mean([x['net_return'] for x in summaries]))
        report['train/max_drawdown_mean'] = float(np.mean([x['max_drawdown'] for x in summaries]))
        report['train/fees_mean'] = float(np.mean([x['fees'] for x in summaries]))
        report['train/filled_orders_mean'] = float(np.mean([x['filled_orders'] for x in summaries]))
        for name in ('policy_pass_decisions','policy_buy_decisions','policy_reduce_decisions',
                     'policy_close_decisions','discretionary_fills','discretionary_fees',
                     'realized_net_pnl','realized_forced_exit_pnl'):
            report['train/'+name+'_mean'] = float(np.mean([x[name] for x in summaries]))
    if 'validation_mean_return' in result:
        report['validation/net_return_mean'] = result['validation_mean_return']
        if 'validation_return_q25' in result:
            report['validation/net_return_q25'] = result['validation_return_q25']
            report['validation/selection_eligible'] = int(result['validation_selection_eligible'])
        report['validation/valid_terminal_fraction'] = float(np.mean(
            [x['valid_terminal'] for x in result['validation']]))
        report['validation/max_drawdown_mean'] = float(np.mean([x['max_drawdown'] for x in result['validation']]))
        report['validation/fees_mean'] = float(np.mean([x['fees'] for x in result['validation']]))
        report['validation/filled_orders_mean'] = float(np.mean([x['filled_orders'] for x in result['validation']]))
        report['validation/pass_only_fraction'] = float(np.mean([x['filled_orders'] == 0 for x in result['validation']]))
        for name in ('policy_pass_decisions','policy_buy_decisions','policy_reduce_decisions',
                     'policy_close_decisions','discretionary_fills','discretionary_fees',
                     'realized_net_pnl','realized_forced_exit_pnl'):
            report['validation/'+name+'_mean'] = float(np.mean([x[name] for x in result['validation']]))
    return report


def selection_evidence(validation):
    """Require broad positive, executed outcomes, not one lucky rollout."""
    returns = np.asarray([row['net_return'] for row in validation],dtype=np.float64)
    if len(returns) < 9 or not np.all(np.isfinite(returns)):
        raise ValueError('Balanced selection requires nine finite validation returns')
    q25 = float(np.quantile(returns,.25))
    eligible = (all(row['valid_terminal'] and row['filled_orders'] > 0 for row in validation)
                and q25 > 0.)
    return q25,eligible


def train(args):
    for name in ('iterations','rollout_steps','environments','epochs','batch_size','eval_every',
                 'validation_rollouts','threads'):
        if getattr(args,name) < 1:
            raise ValueError(name+' must be positive')
    if (not 0 < args.gae_lambda <= 1 or not 0 < args.clip < 1
            or not np.isfinite(args.learning_rate) or args.learning_rate <= 0
            or not np.isfinite(args.entropy_weight) or args.entropy_weight < 0
            or not np.isfinite(args.target_kl) or args.target_kl <= 0
            or args.validation_seed < 0
            or any(not np.isfinite(x) or x <= 0 for x in args.capital_multipliers)):
        raise ValueError('Invalid PPO parameters')
    if args.min_completed_episodes < 0 or args.selection_min_episodes < 0:
        raise ValueError('Episode requirements must be nonnegative')
    if args.selection_mode == 'robust_q25' and args.validation_rollouts < 9:
        raise ValueError('Balanced-action selection requires at least nine validation rollouts')
    if args.min_completed_episodes and (args.environments != 1 or
            args.session_order != 'cycle' or args.capital_multipliers != [1.] or
            args.selection_min_episodes < 1):
        raise ValueError('Full-session campaign requires one 1x account, cycled sessions, and post-episode selection')
    if args.stream_sessions and (args.environments != 1 or args.session_order != 'cycle'):
        raise ValueError('Streamed sessions require one account and chronological cycling')
    if not args.run_name or Path(args.run_name).name != args.run_name or args.run_name in ('.','..'):
        raise ValueError('Run name must be a single directory name')
    if sum(bool(x) for x in (args.initialize_from_best,args.initialize_hierarchical_from_best,
                              args.continue_from_run,args.continue_with_more_epochs_from_run)) > 1:
        raise ValueError('Choose one checkpoint initialization mode')
    config = Config(**{field.name:getattr(args,field.name) for field in fields(Config)})
    root = output_root()/'train'/args.run_name
    root.mkdir(parents=True,exist_ok=True)
    with exclusive(root/'train.lock'):
        return _train_locked(args,config,root)


def _session_reference(path, *, allow_segment):
    root = Path(path).resolve()
    plan, complete = read(root/'plan.json'), read(root/'complete.json')
    if (plan.get('version') != DATA_VERSION or plan.get('teacher_dependency') is not False
            or plan.get('plan_hash') != digest({k:v for k,v in plan.items() if k != 'plan_hash'})
            or complete.get('plan_hash') != plan['plan_hash']
            or complete.get('listing_count') != len(plan['listings'])
            or set(complete.get('files',{})) != {name+'.npy' for name in ARRAYS}
            or (plan.get('segment') and not allow_segment)):
        raise ValueError('Invalid streamed market certificate')
    return SimpleNamespace(root=root,plan=plan,seconds=plan['rows'])


def _continuation(parent_root, manifest, *, run_root, device, more_epochs=False):
    parent_root = Path(parent_root).resolve()
    manifest = json.loads(json.dumps(manifest))
    if parent_root == run_root.resolve():
        raise ValueError('Continuation must use a new run directory')
    parent = read(parent_root/'run_manifest.json')
    status = read(parent_root/'status.json')
    if parent.get('contract_hash') != digest({k:v for k,v in parent.items() if k != 'contract_hash'}):
        raise ValueError('Parent run manifest integrity failure')
    if status.get('status') not in ('complete','no_valid_checkpoint'):
        raise ValueError('Continuation requires a completed parent run')
    for key in ('version','job','config','model','feature_names','train','validation',
                'teacher_supervision','torch_version','numpy_version','wandb'):
        if parent.get(key) != manifest.get(key):
            raise ValueError('Continuation changes parent contract: ' + key)
    old_args, new_args = parent['arguments'], manifest['arguments']
    allowed = {'min_completed_episodes','epochs'} if more_epochs else {'min_completed_episodes'}
    if ({k:v for k,v in old_args.items() if k not in allowed} !=
            {k:v for k,v in new_args.items() if k not in allowed} or
            new_args['min_completed_episodes'] <= old_args['min_completed_episodes']):
        raise ValueError('Continuation changes settings beyond the approved session/epoch target')
    if more_epochs and new_args['epochs'] <= old_args['epochs']:
        raise ValueError('Epoch-changing continuation must increase PPO epochs')
    changed = str(Path('research/rl_trading/v2/train.py'))
    old_code, new_code = parent['code']['files'], manifest['code']['files']
    if (set(old_code) != set(new_code) or
            any(old_code[name] != new_code[name] for name in old_code if name != changed) or
            (old_code[changed] != new_code[changed] and
             old_code[changed] != (V8_TRAIN_HASH if more_epochs else PILOT_TRAIN_HASH))):
        raise ValueError('Continuation changed model, data, or execution source')
    checkpoint = parent_root/'checkpoint_latest.pt'
    saved = torch.load(checkpoint,map_location=device,weights_only=False)
    if (saved.get('contract_hash') != parent.get('contract_hash') or
            saved.get('completed_episodes') != status.get('completed_episodes') or
            saved.get('iteration') != status.get('iteration')):
        raise ValueError('Parent checkpoint and completed run disagree')
    lineage = dict(parent_run=str(parent_root),parent_contract_hash=parent['contract_hash'],
                   parent_checkpoint_hash=file_hash(checkpoint),
                   parent_iteration=saved['iteration'],
                   parent_completed_episodes=saved['completed_episodes'])
    if more_epochs:
        lineage['optimization_change'] = dict(ppo_epochs_from=old_args['epochs'],
                                               ppo_epochs_to=new_args['epochs'])
    inherited_best = None
    if saved['best'] > -float('inf'):
        best_path = parent_root/'checkpoint_best.pt'
        inherited_best = torch.load(best_path,map_location=device,weights_only=False)
        if inherited_best.get('contract_hash') != parent['contract_hash']:
            raise ValueError('Parent best checkpoint contract mismatch')
        lineage['parent_best_checkpoint_hash'] = file_hash(best_path)
    return lineage, saved, inherited_best


def _best_initialization(parent_root, manifest, *, run_root, device):
    """Transfer a verified best policy/optimizer, never an incompatible account."""
    parent_root = Path(parent_root).resolve()
    manifest = json.loads(json.dumps(manifest))
    if parent_root == run_root.resolve():
        raise ValueError('Best-policy initialization requires a new run directory')
    parent = read(parent_root/'run_manifest.json')
    if parent.get('contract_hash') != digest({k:v for k,v in parent.items() if k != 'contract_hash'}):
        raise ValueError('Parent run manifest integrity failure')
    for key in ('job','model','feature_names','train','validation','teacher_supervision',
                'torch_version','numpy_version','wandb'):
        if parent.get(key) != manifest.get(key):
            raise ValueError('Best-policy initialization changes parent contract: ' + key)
    if (parent.get('version') != 'rl-trading-v2-ppo-single-account-sessions-3' or
            manifest.get('version') != 'rl-trading-v2-ppo-single-account-sessions-4'):
        raise ValueError('Best-policy initialization requires the pinned early-exit migration')
    old_config, new_config = parent['config'], manifest['config']
    if (old_config.get('liquidation_buffer_seconds') != 120 or
            new_config.get('liquidation_buffer_seconds') != 900 or
            {k:v for k,v in old_config.items() if k not in ('version','liquidation_buffer_seconds')} !=
            {k:v for k,v in new_config.items() if k not in ('version','liquidation_buffer_seconds')}):
        raise ValueError('Best-policy initialization changes other execution assumptions')
    old_args, new_args = parent['arguments'], manifest['arguments']
    if (old_args.get('liquidation_buffer_seconds') != 120 or
            new_args.get('liquidation_buffer_seconds') != 900 or
            new_args.get('min_completed_episodes',0) < old_args.get('min_completed_episodes',0) or
            {k:v for k,v in old_args.items() if k not in ('liquidation_buffer_seconds','min_completed_episodes')} !=
            {k:v for k,v in new_args.items() if k not in ('liquidation_buffer_seconds','min_completed_episodes')}):
        raise ValueError('Best-policy initialization changes other training settings')
    old_code, new_code = parent['code']['files'], manifest['code']['files']
    if (set(old_code) != set(new_code) or
            any(old_code[name] != new_code[name] for name in old_code if name not in PRE_EARLY_EXIT_HASHES) or
            any(old_code.get(name) not in ((expected,PILOT_TRAIN_HASH) if name.endswith('train.py')
                                                else (expected,))
                for name,expected in PRE_EARLY_EXIT_HASHES.items())):
        raise ValueError('Best-policy initialization changed unapproved source')
    best_path = parent_root/'checkpoint_best.pt'
    best = torch.load(best_path,map_location=device,weights_only=False)
    if (best.get('contract_hash') != parent['contract_hash'] or
            not np.isfinite(best.get('best',-float('inf'))) or
            best.get('completed_episodes',0) < 1):
        raise ValueError('Parent best checkpoint is not eligible')
    metric = read(parent_root/'metrics'/f"{best['iteration']:06d}.json")
    if (not metric.get('validation_all_flat') or
            not np.isclose(metric.get('validation_mean_return',float('nan')),best['best'])):
        raise ValueError('Parent best checkpoint lacks valid validation evidence')
    lineage = dict(parent_run=str(parent_root),parent_contract_hash=parent['contract_hash'],
                   parent_best_checkpoint_hash=file_hash(best_path),
                   parent_best_iteration=best['iteration'],parent_best_score=best['best'],
                   transferred=['policy','optimizer'],account_state='fresh',
                   session_cursor='first_training_date',random_state='fresh_seed')
    return lineage,best


def _hierarchical_initialization(parent_root, manifest, *, run_root, device):
    """Transfer the certified V4 policy under the V8 executable-action mask."""
    parent_root = Path(parent_root).resolve()
    manifest = json.loads(json.dumps(manifest))
    if parent_root == run_root.resolve():
        raise ValueError('Hierarchical-action initialization requires a new run directory')
    parent = read(parent_root/'run_manifest.json')
    if parent.get('contract_hash') != digest({k:v for k,v in parent.items() if k != 'contract_hash'}):
        raise ValueError('Parent run manifest integrity failure')
    if (parent.get('version') != 'rl-trading-v2-ppo-single-account-sessions-4' or
            manifest.get('version') != 'rl-trading-v2-ppo-hierarchical-actions-8'):
        raise ValueError('Hierarchical-action initialization requires V4 to V8 migration')
    for key in ('job','model','feature_names','train','validation','teacher_supervision',
                'torch_version','numpy_version','wandb'):
        if parent.get(key) != manifest.get(key):
            raise ValueError('Balanced-action initialization changes parent contract: ' + key)
    if ({k:v for k,v in parent['config'].items() if k != 'version'} !=
            {k:v for k,v in manifest['config'].items() if k != 'version'}):
        raise ValueError('Balanced-action initialization changes execution assumptions')
    old_args, new_args = parent['arguments'], manifest['arguments']
    if (old_args.get('validation_rollouts') != 3 or new_args.get('validation_rollouts') != 9 or
            new_args.get('selection_mode') != 'robust_q25' or
            {k:v for k,v in old_args.items() if k != 'validation_rollouts'} !=
            {k:v for k,v in new_args.items() if k not in ('validation_rollouts','selection_mode')}):
        raise ValueError('Balanced-action initialization changes unapproved training settings')
    old_code, new_code = parent['code']['files'], manifest['code']['files']
    if (set(old_code) != set(new_code) or
            any(old_code[name] != new_code[name] for name in old_code if name not in BALANCED_ACTION_PARENT_HASHES) or
            any(old_code.get(name) != expected for name,expected in BALANCED_ACTION_PARENT_HASHES.items())):
        raise ValueError('Balanced-action initialization changes unapproved source')
    best_path = parent_root/'checkpoint_best.pt'
    best = torch.load(best_path,map_location=device,weights_only=False)
    if (best.get('contract_hash') != parent['contract_hash'] or
            not np.isfinite(best.get('best',-float('inf'))) or
            best.get('completed_episodes',0) < 1):
        raise ValueError('Parent best checkpoint is not eligible')
    metric = read(parent_root/'metrics'/f"{best['iteration']:06d}.json")
    if (not metric.get('validation_all_flat') or
            not np.isclose(metric.get('validation_mean_return',float('nan')),best['best'])):
        raise ValueError('Parent best checkpoint lacks valid validation evidence')
    lineage = dict(parent_run=str(parent_root),parent_contract_hash=parent['contract_hash'],
                   parent_best_checkpoint_hash=file_hash(best_path),
                   parent_best_iteration=best['iteration'],parent_best_score=best['best'],
                   transferred=['policy'],optimizer_state='fresh',account_state='fresh',
                   session_cursor='first_training_date',random_state='fresh_seed',
                   change='V4-equivalent type prior; executable reduce mask and whole-share sizing; nine-rollout selection')
    return lineage,best


def _train_locked(args, config, root):
    torch.set_num_threads(args.threads)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA requested but unavailable')
    torch.use_deterministic_algorithms(True)
    load = (lambda p: _session_reference(p,allow_segment=args.allow_segment)) if args.stream_sessions else (
        lambda p: MarketSession.load(p,allow_segment=args.allow_segment))
    sessions = [load(p) for p in args.train_sessions]
    validation = [load(p) for p in args.val_sessions]
    chronological(sessions,validation)
    if args.stream_sessions:
        schemas = {tuple(source.plan['feature_names']) for source in sessions+validation}
        if len(schemas) != 1:
            raise ValueError('Streamed market feature schemas differ')
    if args.min_completed_episodes:
        required = sum(sessions[i % len(sessions)].seconds-1
            for i in range(args.min_completed_episodes))
        if args.iterations*args.rollout_steps < required:
            raise ValueError(f'Iteration budget cannot complete {args.min_completed_episodes} sessions; require at least {required} steps')
    contract_args = {k:v for k,v in vars(args).items() if k not in ('resume','iterations','run_name','train_sessions','val_sessions','continue_from_run','continue_with_more_epochs_from_run','initialize_from_best','initialize_hierarchical_from_best')}
    manifest = dict(version=VERSION,job='train',config=config.manifest(),arguments=contract_args,
        model=dict(features=len(sessions[0].plan['feature_names']),width=args.width,heads=args.heads),
        feature_names=sessions[0].plan['feature_names'],code=code_identity(),
        train=[dict(root=str(x.root),plan_hash=x.plan['plan_hash'],complete_hash=file_hash(x.root/'complete.json'),date=x.plan['date']) for x in sessions],
        validation=[dict(root=str(x.root),plan_hash=x.plan['plan_hash'],complete_hash=file_hash(x.root/'complete.json'),date=x.plan['date']) for x in validation],
        output_root=str(root),wandb=dict(mode=args.wandb_mode,project=args.wandb_project,
                                         entity=args.wandb_entity),teacher_supervision=False,
        torch_version=torch.__version__,numpy_version=np.__version__)
    inherited = inherited_best = initialized_best = None
    if args.continue_from_run:
        lineage, inherited, inherited_best = _continuation(args.continue_from_run,manifest,
                                                            run_root=root,device=args.device)
        manifest['lineage'] = lineage
        if inherited['iteration'] >= args.iterations:
            raise ValueError('Continuation iteration budget must exceed parent checkpoint')
    if args.continue_with_more_epochs_from_run:
        lineage, inherited, inherited_best = _continuation(
            args.continue_with_more_epochs_from_run,manifest,run_root=root,
            device=args.device,more_epochs=True)
        manifest['lineage'] = lineage
        if inherited['iteration'] >= args.iterations:
            raise ValueError('Continuation iteration budget must exceed parent checkpoint')
    if args.initialize_from_best:
        lineage, initialized_best = _best_initialization(args.initialize_from_best,manifest,
                                                          run_root=root,device=args.device)
        manifest['lineage'] = lineage
    if args.initialize_hierarchical_from_best:
        lineage, initialized_best = _hierarchical_initialization(args.initialize_hierarchical_from_best,
                                                              manifest,run_root=root,device=args.device)
        manifest['lineage'] = lineage
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
    next_session_index = 0

    def new_env():
        nonlocal next_session_index
        if args.session_order == 'cycle':
            index = next_session_index % len(sessions)
            next_session_index += 1
        else:
            index = int(rng.integers(len(sessions)))
        cash = config.initial_cash*float(rng.choice(args.capital_multipliers))
        session = (MarketSession.load(sessions[index].root,allow_segment=args.allow_segment)
                   if args.stream_sessions else sessions[index])
        return index,TradingEnv(session,config,initial_cash=cash)

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
            session_indices=session_indices,next_session_index=next_session_index,
            environments=[env.state_dict() for env in envs])

    if args.resume or inherited is not None:
        saved = torch.load(latest,map_location=args.device,weights_only=False) if args.resume else inherited
        if args.resume and saved['contract_hash'] != manifest['contract_hash']:
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
        next_session_index = saved['next_session_index']
        for slot,index in enumerate(session_indices):
            session = (MarketSession.load(sessions[index].root,allow_segment=args.allow_segment)
                       if args.stream_sessions else sessions[index])
            envs[slot] = TradingEnv(session,config)
            envs[slot].load_state_dict(saved['environments'][slot])
        if inherited is not None and not args.resume:
            _save(latest,snapshot(start))
            if inherited_best is not None:
                inherited_best['contract_hash'] = manifest['contract_hash']
                _save(root/'checkpoint_best.pt',inherited_best)
    else:
        if initialized_best is not None:
            if args.initialize_hierarchical_from_best:
                missing,unexpected = policy.load_state_dict(initialized_best['policy'],strict=False)
                if set(missing) != {'action_type.weight','action_type.bias'} or unexpected:
                    raise ValueError('V4 policy transfer changed unexpected model parameters')
            else:
                policy.load_state_dict(initialized_best['policy'])
            if not args.initialize_hierarchical_from_best:
                optimizer.load_state_dict(initialized_best['optimizer'])
        # Even interruption in the first rollout/validation has a restart point.
        _save(latest,snapshot(0))
    if start >= args.iterations:
        print(f'Checkpoint already reached iteration {start}',flush=True)
        return 0
    wandb_run = None
    if args.wandb_mode != 'disabled':
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        wandb_id = manifest['contract_hash'][:12]
        (root/'wandb').mkdir(exist_ok=True)
        wandb_run = init_wandb(entity=args.wandb_entity,project=args.wandb_project,
            run_name=args.run_name,config=manifest,run_dir=root/'wandb',mode=args.wandb_mode,
            timeout_seconds=60,run_id=wandb_id,
            resume_mode='must' if args.resume else 'never',capture_console=False)
        write(root/'wandb_run.json',dict(id=wandb_id,project=args.wandb_project,
            entity=args.wandb_entity,url=getattr(wandb_run,'url',None),mode=args.wandb_mode))
        synced_path = root/'wandb_synced.json'
        synced = (read(synced_path)['iteration'] if synced_path.exists() else
                  manifest['lineage']['parent_iteration'] if inherited is not None else 0)
        if synced > start:
            raise ValueError('W&B sync cursor is ahead of the training checkpoint')
        for completed in range(synced+1,start+1):
            saved = read(root/'metrics'/f'{completed:06d}.json')
            wandb_run.log(wandb_metrics(saved),step=completed)
            write(synced_path,dict(iteration=completed,run_id=wandb_id))
    print(f'V2 PPO device={args.device} train_sessions={len(sessions)} validation_sessions={len(validation)} root={root}',flush=True)
    print('Execution contract (IBKR fee scenario; slippage remains uncalibrated): '+json.dumps(config.manifest()),flush=True)
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
                        if args.stream_sessions:
                            envs[slot] = None
                            del env
                            gc.collect()
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
                    size = np.full((len(batch_rows),width,3),.5,dtype=np.float32)
                    for i,row in enumerate(batch_rows):
                        mode[i,:len(row['modes'])] = row['modes']
                        size[i,:len(row['sizes'])] = row['sizes']
                    _,_,logprob,entropy,value = policy.action(batch,
                        torch.as_tensor(mode,device=args.device),torch.as_tensor(size,device=args.device))
                    def tensor(key):
                        return torch.tensor([x[key] for x in batch_rows],dtype=torch.float32,device=args.device)
                    loss,measure = ppo_loss(logprob,tensor('logprob'),
                        (tensor('advantage')-mean)/max(std,1e-8),value,tensor('target'),entropy,
                        clip=args.clip,entropy_weight=args.entropy_weight)
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
            if (completed_episodes >= args.selection_min_episodes and
                    (iteration == 1 or iteration % args.eval_every == 0 or iteration == args.iterations)):
                active_state = None
                if args.stream_sessions:
                    active_state = envs[0].state_dict()
                    envs[0] = None
                    env = None
                    rows = trajectories = observations = batch_rows = batch = None
                    gc.collect()
                try:
                    result['validation'] = evaluate(policy,validation,config,args.device,
                        rollouts=args.validation_rollouts,seed=args.validation_seed,
                        allow_segment=args.allow_segment)
                finally:
                    if active_state is not None:
                        session = MarketSession.load(sessions[session_indices[0]].root,
                            allow_segment=args.allow_segment)
                        restored = TradingEnv(session,config)
                        restored.load_state_dict(active_state)
                        envs[0] = restored
                        restored = session = None
                score = float(np.mean([x['net_return'] for x in result['validation']]))
                valid = all(x['valid_terminal'] for x in result['validation'])
                if args.selection_mode == 'robust_q25':
                    q25,eligible = selection_evidence(result['validation'])
                    result['validation_return_q25'] = q25
                    result['validation_selection_eligible'] = eligible
                    improved = eligible and score > best
                else:
                    improved = valid and score > best
                if improved:
                    best = score
                result['validation_mean_return'] = score
                result['validation_all_flat'] = valid
            result['elapsed_seconds'] = time.monotonic()-began
            payload = snapshot(iteration)
            _save(latest,payload)
            if improved:
                _save(root/'checkpoint_best.pt',payload)
            write(root/'metrics'/f'{iteration:06d}.json',result)
            if wandb_run is not None:
                wandb_run.log(wandb_metrics(result),step=iteration)
                write(root/'wandb_synced.json',dict(iteration=iteration,run_id=wandb_id))
            write(root/'status.json',dict(status='running',iteration=iteration,active=len(envs),
                queued_iterations=args.iterations-iteration,completed_episodes=completed_episodes,
                failed=0,retried=0,skipped=0))
            print(f"Iteration {iteration}/{args.iterations} steps={result['rollout_steps']} updates={len(measures)} episodes={completed_episodes} validation={result.get('validation_mean_return','not scheduled')} seconds={result['elapsed_seconds']:.1f}",flush=True)
            if args.min_completed_episodes and completed_episodes >= args.min_completed_episodes:
                status = 'complete' if best > -float('inf') else 'no_valid_checkpoint'
                write(root/'status.json',dict(status=status,iteration=iteration,active=0,
                    completed_episodes=completed_episodes,failed=0))
                return 0 if status == 'complete' else 2
    except KeyboardInterrupt:
        write(root/'status.json',dict(status='interrupted',active=0,resume='last committed iteration'))
        return 2
    except Exception:
        write(root/'status.json',dict(status='failed',active=0,failed=1,resume='last committed iteration'))
        raise
    finally:
        if wandb_run is not None:
            wandb_run.finish()
    if completed_episodes < args.min_completed_episodes:
        raise ValueError('Training ended before the required complete sessions')
    status = 'complete' if best > -float('inf') else 'no_valid_checkpoint'
    write(root/'status.json',dict(status=status,iteration=args.iterations,active=0,
        completed_episodes=completed_episodes,failed=0))
    return 0 if status == 'complete' else 2


def main(argv=None):
    return train(parser().parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
