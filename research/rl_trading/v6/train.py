"""Audited V6 teacher initialization followed by genuine quote-aware PPO.

All generated output stays beneath one configured runtime run directory.
Resume restarts the current session from its boundary and retains completed
session checkpoints. No unapproved partial dataset or sealed-test tuning.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import random
import subprocess
import time
import numpy as np
import torch
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.common import digest, file_hash, exclusive, bounds
from research.rl_trading.v6.training_gate import require_dataset
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.identity_map import certify_identity_map, open_identity_map
from research.rl_trading.v6.teacher_data import load_teacher
from research.rl_trading.v6.training import train_session
from research.rl_trading.v6.learning_rate import cosine_warmup
from research.rl_trading.v6.environment_source import ArteExecutionSource
from research.rl_trading.v6.environment import BracketEnvironment
from research.rl_trading.v6.rollout import collect_session, update_session, audit_reconstruction
from research.rl_trading.v6.replay_artifacts import save_replay
from research.rl_trading.v6.prepare_training import _write_json
from research.rl_trading.v6.telemetry import PeriodicProgress


def _commit():
    root=Path(__file__).resolve().parents[3]
    pin=root/'SOURCE_COMMIT.txt'
    return pin.read_text().strip() if pin.is_file() else subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=root,text=True).strip()


def _flatten(prefix, values):
    result={}
    for key,value in values.items():
        name=f'{prefix}/{key}'
        if isinstance(value,dict): result.update(_flatten(name,value))
        elif isinstance(value,(int,float,bool)) or value is None: result[name]=value
    return result


def _checkpoint(path,policy,optimizer,manifest,progress):
    payload={'version':'rl-trading-v6-attention-ppo-checkpoint-1','model':policy.state_dict(),
        'optimizer':optimizer.state_dict(),'manifest_hash':manifest['hash'],'progress':progress,
        'torch_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        'numpy_rng':np.random.get_state(),'python_rng':random.getstate()}
    temporary=path.with_suffix('.pt.tmp')
    torch.save(payload,temporary)
    temporary.replace(path)


def _model_causality_audit(policy,device):
    with torch.no_grad():
        scalar=torch.randn(125,37,device=device)
        levels=torch.randn(125,2,5,11,device=device)
        prefix=policy.encoder.encode_listing(scalar,levels)[:120].clone()
        scalar[120:]=999
        levels[120:]=999
        if not torch.equal(prefix,policy.encoder.encode_listing(scalar,levels)[:120]):
            raise ValueError('Temporal encoder failed future-prefix audit')
    return {'future_candle_prefix_invariance':'passed','future_label_observation_fields':0,
            'attention_keys':'only_completed_120_candle_ring',
            'ranking_evidence':'trailing_15_completed_clock_seconds_share_volume'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--early-manifest',type=Path,required=True)
    parser.add_argument('--late-manifest',type=Path,required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--teacher-epochs',type=int,default=10)
    parser.add_argument('--teacher-only',action='store_true',
        help='Teacher initialization and per-epoch development label evaluation; no trading replay or PPO')
    parser.add_argument('--ppo-epochs',type=int,default=40)
    parser.add_argument('--ppo-updates',type=int,default=4)
    parser.add_argument('--learning-rate',type=float,default=3e-4)
    parser.add_argument('--teacher-lr-schedule',choices=('fixed','cosine'),default='fixed')
    parser.add_argument('--warmup-epochs',type=float,default=1.)
    parser.add_argument('--minimum-lr-ratio',type=float,default=.1)
    parser.add_argument('--resume-from',type=Path,
        help='Explicit teacher-only continuation from a verified parent run last.pt')
    parser.add_argument('--clocks-per-chunk',type=int,default=32)
    parser.add_argument('--max-orders-per-second',type=int,default=64)
    parser.add_argument('--broker-engine',choices=('reference','tensor-100ms'),default='reference',
        help='Versioned approximate GPU broker uses one proposal per second')
    parser.add_argument('--broker-participation',type=float,default=.1)
    parser.add_argument('--compile-broker',action='store_true',
        help='Compile fixed-shape tensor fill kernels; startup compilation is separate')
    parser.add_argument('--decoder-batch-size',type=int,default=1)
    parser.add_argument('--replay-every',type=int,default=1)
    parser.add_argument('--log-every-seconds',type=float,default=60.)
    parser.add_argument('--luld-root',type=Path,required=True,
        help='Certified modeled LULD sidecar required before training')
    parser.add_argument('--halt-onset-penalty',type=float,default=.10)
    parser.add_argument('--halt-per-minute-penalty',type=float,default=.01)
    parser.add_argument('--terminal-exposure-penalty',type=float,default=.25)
    parser.add_argument('--train-replay-days',type=date.fromisoformat,nargs='+',
        default=[date(2026,7,31),date(2026,8,10),date(2026,8,21)])
    parser.add_argument('--seed',type=int,default=17)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--wandb-project',default='rl-trading-v6')
    args=parser.parse_args(argv)
    from src.runtime_paths import runtime_root
    runtime=runtime_root().resolve()
    run=args.run_root.resolve()
    if not runtime.is_dir() or not run.is_relative_to(runtime) or any(
            not p.resolve().is_relative_to(runtime) for p in (args.early_manifest,args.late_manifest,args.ledger)):
        raise ValueError('Training input/output roots must be under configured runtime')
    if min(args.teacher_epochs,args.ppo_epochs,args.ppo_updates,args.clocks_per_chunk,
           args.max_orders_per_second,args.replay_every)<1 or args.learning_rate<=0 or args.log_every_seconds<=0:
        raise ValueError('Positive training/rollout limits required')
    dataset=require_dataset(args.dataset,runtime_root=runtime)
    if args.teacher_epochs < 10:
        raise ValueError('Teacher initialization must run at least 10 epochs')
    if args.teacher_only and args.teacher_epochs > 20:
        raise ValueError('Teacher-only training is capped at 20 epochs')
    if not 0<args.broker_participation<=1 or args.decoder_batch_size<1:
        raise ValueError('Invalid GPU broker or decoder bounds')
    if args.compile_broker:
        if args.broker_engine!='tensor-100ms':
            raise ValueError('Compiled broker requires the tensor-100ms environment')
        compiler=runtime/'rl-v6-compiler-cache'/_commit()
        os.environ['TORCHINDUCTOR_CACHE_DIR']=str(compiler/'inductor')
        os.environ['TRITON_CACHE_DIR']=str(compiler/'triton')
    if args.teacher_lr_schedule=='cosine':
        cosine_warmup(0,args.teacher_epochs,args.learning_rate,args.warmup_epochs,args.minimum_lr_ratio)
    if args.resume_from and (not args.teacher_only or not args.resume_from.resolve().is_relative_to(runtime)):
        raise ValueError('Parent continuation requires a separate teacher-only runtime')
    from research.rl_trading.v6.luld import RiskPenalty
    from research.rl_trading.v6.build_luld import open_sidecar
    risk=RiskPenalty(args.halt_onset_penalty,args.halt_per_minute_penalty,args.terminal_exposure_penalty)
    if not args.luld_root.resolve().is_relative_to(runtime):
        raise ValueError('LULD sidecar must be under configured runtime')
    # Require complete sidecars for every train/development day before any
    # optimizer step, including days not selected for replay diagnostics.
    luld_certificates={str(entry['day']):file_hash(args.luld_root/str(entry['day'])/'complete.json')
                       for entry in dataset['days']}
    for entry in dataset['days']:
        day=date.fromisoformat(str(entry['day']))
        plan=json.loads((Path(entry['bank_root'])/'plan.json').read_text())
        # Validate every day's contents and execution audit before creating
        # an optimizer. Only one sparse sidecar is resident during this audit.
        book,_=open_sidecar(args.luld_root,day,{'build_id':plan['source_build_id'],
            'definition_hash':plan['source_definition_hash']})
        del book
    ranking=MarketAttentionConfig(**dataset['ranking'])
    device=torch.device(args.device)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    torch.backends.cudnn.deterministic=True
    torch.backends.cudnn.benchmark=False
    policy=RankedBracketActorCritic(config=ranking).to(device)
    optimizer=torch.optim.Adam(policy.parameters(),lr=args.learning_rate)
    manifest={'version':'rl-trading-v6-attention-ppo-run-1','dataset_sha256':file_hash(args.dataset),
        'ranking':asdict(ranking),'source_commit':_commit(),
        'config':{k:([str(item) for item in v] if isinstance(v,list) else str(v) if isinstance(v,Path) else v)
                  for k,v in vars(args).items() if k not in ('resume','audit_only')},
        'teacher_role':'candle_only_actor_initialization',
        'environment_version':'rl-v6-tensor-participation-100ms-v1' if args.broker_engine=='tensor-100ms' else 'reference-quote-oms',
        'decision_cadence':'one_proposal_per_second' if args.broker_engine=='tensor-100ms' else 'bounded_same_clock_proposals',
        'validation_contract':'development_teacher_labels_trading_validation_pending' if args.teacher_only else 'trading_replay',
        'reward':'quote_delta_equity_minus_separate_modeled_halt_and_terminal_exposure_shaping_v2',
        'risk_penalty':asdict(risk),'luld_certificates':luld_certificates,
        'holding_observation_version':'11_fields_modeled_halt_flag_and_age',
        'research_price_increment':.0001,'price_increment_authority':'canonical_precision_scenario_not_exchange_tick',
        'wandb_key_present':bool(os.environ.get('WANDB_API_KEY'))}
    manifest['hash']=digest(manifest)
    parent_payload=None
    if args.resume_from:
        parent_manifest=json.loads((args.resume_from.parent/'manifest.json').read_text())
        parent_payload=torch.load(args.resume_from,map_location=device,weights_only=False)
        if parent_payload['manifest_hash']!=parent_manifest['hash']:
            raise ValueError('Parent checkpoint manifest mismatch')
        ignored={'run_root','teacher_lr_schedule','warmup_epochs','minimum_lr_ratio','resume_from'}
        current=manifest['config']; previous={'broker_engine':'reference','compile_broker':False,
            'broker_participation':.1,'decoder_batch_size':1,**parent_manifest['config']}
        if ({k:v for k,v in current.items() if k not in ignored} !=
                {k:v for k,v in previous.items() if k not in ignored} or
                manifest['dataset_sha256']!=parent_manifest['dataset_sha256'] or
                manifest['luld_certificates']!=parent_manifest['luld_certificates'] or
                parent_payload['progress']['phase']!='teacher'):
            raise ValueError('Parent continuation changed data or non-schedule configuration')
        manifest['parent_checkpoint_sha256']=file_hash(args.resume_from)
        manifest['parent_manifest_hash']=parent_manifest['hash']
        manifest['hash']=digest({k:v for k,v in manifest.items() if k!='hash'})
        if args.resume:
            parent_payload=None  # Subsequent resumes use this run's own last.pt.
    run.mkdir(parents=True,exist_ok=True)
    progress={'phase':'teacher','epoch':0,'day_index':0,'wandb_step':0}
    logger=None
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    with exclusive(run/'run.lock'):
        if (run/'manifest.json').is_file():
            if json.loads((run/'manifest.json').read_text())!=manifest:
                raise ValueError('Run root belongs to another dataset/config/source')
            if not args.resume and not args.audit_only:
                raise ValueError('Existing run requires explicit --resume')
        else: _write_json(run/'manifest.json',manifest)
        last=run/'last.pt'
        if args.resume or parent_payload is not None:
            payload=parent_payload if parent_payload is not None else torch.load(last,map_location=device,weights_only=False)
            if parent_payload is None and payload['manifest_hash']!=manifest['hash']:
                raise ValueError('Resume checkpoint belongs to another audited run')
            policy.load_state_dict(payload['model'])
            optimizer.load_state_dict(payload['optimizer'])
            progress=payload['progress']
            if progress['phase'] in ('complete','stopped_validation_deterioration'):
                raise ValueError('Training already completed; use selected checkpoint replay, not a second run')
            torch.set_rng_state(payload['torch_rng'].cpu())
            if payload['cuda_rng']: torch.cuda.set_rng_state_all([r.cpu() for r in payload['cuda_rng']])
            np.random.set_state(payload['numpy_rng']); random.setstate(payload['python_rng'])
        def open_day(entry):
            return open_session(Path(entry['bank_root']),runtime_root=runtime,previous_root=Path(entry['previous_root']))
        def evidence(session):
            key=str(session.day)
            reader=arte_source.reader(threads=2)
            try:
                source=arte_source.load_build(args.early_manifest if session.day<=date(2026,8,17) else args.late_manifest,
                                             args.ledger,[session.day])
                plan=json.loads((session.root/'plan.json').read_text())
                if source['build_id']!=plan['source_build_id'] or source['definition_hash']!=plan['source_definition_hash']:
                    raise ValueError('Replay source differs from certified feature bank')
                identity=run/'identity'/key
                if not (identity/'complete.json').is_file():
                    population,proof=arte_source.population(reader,source,session.day)
                    certify_identity_map(session,population,proof['snapshot_hash'],identity,runtime_root=runtime)
                tickers=open_identity_map(identity,session,runtime_root=runtime)
                provider=ArteExecutionSource(reader,source,args.ledger,session.day,end_us=bounds(session.day)[1])
                provider.luld,provider.luld_certificate = open_sidecar(args.luld_root,session.day,source)
                return provider,tickers
            except Exception:
                reader.close(); raise
        def log(prefix,metrics):
            metrics={**metrics,'learning_rate':optimizer.param_groups[0]['lr']}
            progress['wandb_step']+=1
            if logger: logger.log(_flatten(prefix,metrics),step=progress['wandb_step'])
            print(json.dumps({'phase':prefix,'metrics':metrics},default=str),flush=True)
            with (run/'metrics.jsonl').open('a',encoding='utf-8') as out:
                out.write(json.dumps({'step':progress['wandb_step'],'phase':prefix,'metrics':metrics},default=str)+'\n')
        def tensor_collect(session,provider,tickers,*,max_clocks=None,deterministic=False):
            from research.rl_trading.v6.tensor_broker import TensorBroker,BrokerConfig
            from research.rl_trading.v6.tensor_rollout import collect_tensor_session
            from research.rl_trading.v6.broker_shards import BrokerShards,VERSION
            start=next(session.candle_events()).close_us
            finish=bounds(session.day)[1] if max_clocks is None else min(bounds(session.day)[1],start+max_clocks*1_000_000)
            namespace=digest((VERSION,provider.source['build_id'],tickers,
                [(t,provider.attempts[t]) for t in tickers]))[:20]
            shards=BrokerShards(provider,tickers,runtime/'rl-v6-broker-shards'/str(session.day)/namespace,
                                runtime_root=runtime)
            broker=TensorBroker(len(tickers),device=device,config=BrokerConfig(
                participation=args.broker_participation,risk=risk))
            if args.compile_broker:
                broker.compile_step()
            pulse_tensor=PeriodicProgress(lambda values:log('progress/tensor_rollout',
                {**values,'day':str(session.day)}),seconds=args.log_every_seconds)
            with closing(shards.buckets(start,finish,device=device,luld=provider.luld)) as tape:
                collection=collect_tensor_session(policy,session,broker,tape,
                    device=device,max_clocks=max_clocks,deterministic=deterministic,
                    progress_callback=lambda clock,count:pulse_tensor({'close_us':clock,'policy_steps':count}))
            return collection
        def pulse(prefix,day,epoch):
            return PeriodicProgress(lambda values:log(prefix,{**values,'day':str(day),'epoch':epoch+1}),
                                    seconds=args.log_every_seconds)
        try:
            audit=_model_causality_audit(policy,device)
            # Fresh non-learning smoke verifies the real quote collector and
            # recurrent reconstruction before any teacher/PPO optimizer step.
            if not args.teacher_only:
                session=open_day(dataset['days'][0])
                provider,tickers=evidence(session)
                try:
                    if args.broker_engine=='tensor-100ms':
                        env=tensor_collect(session,provider,tickers,max_clocks=5)
                        frames,steps,smoke=env.frames,env.steps,env.summary
                    else:
                        env=BracketEnvironment(tickers,provider,luld=provider.luld,risk_penalty=risk)
                        frames,steps,smoke=collect_session(policy,session,env,device=device,max_clocks=5,max_orders_per_second=4)
                    audit.update(real_reconstruction=audit_reconstruction(policy,session,frames,device=device),
                                 smoke_metrics=smoke,execution_evidence=provider.certificate())
                finally: provider.reader.close()
                del session,frames,steps,env,provider
            else:
                audit['trading_validation']='pending_execution_price_coverage'
            _write_json(run/'model-audit.json',{'status':'passed','source_commit':manifest['source_commit'],
                'dataset_sha256':manifest['dataset_sha256'],**audit})
            if args.audit_only:
                print(json.dumps({'status':'all_training_launch_audits_passed','run_root':str(run)}),flush=True)
                return 0
            import wandb
            logger=wandb.init(project=args.wandb_project,name=run.name,id=manifest['hash'][:16],
                              resume='allow',mode='online',dir=str(run),config=manifest)
            (run/'metrics.jsonl').touch(exist_ok=True)
            logger.save(str(run/'metrics.jsonl'),base_path=str(run),policy='live')
            for filename in ('manifest.json','model-audit.json'):
                logger.save(str(run/filename),base_path=str(run),policy='now')
            # Smoke sampling is diagnostic; production starts at exact seed
            # (or saved RNG) regardless of how often audit-only was invoked.
            if args.resume or parent_payload is not None:
                torch.set_rng_state(payload['torch_rng'].cpu())
                if payload['cuda_rng']: torch.cuda.set_rng_state_all([r.cpu() for r in payload['cuda_rng']])
            else:
                torch.manual_seed(args.seed)
            train_days=[e for e in dataset['days'] if e['role']=='train']
            dev_days=[e for e in dataset['days'] if e['role']=='development']
            diagnostics=[e for e in train_days if date.fromisoformat(str(e['day'])) in args.train_replay_days]
            if len(diagnostics)!=len(set(args.train_replay_days)):
                raise ValueError('Training replay diagnostics must select audited training days')
            phases=(('teacher',args.teacher_epochs),) if args.teacher_only else (('teacher',args.teacher_epochs),('ppo',args.ppo_epochs))
            for phase,epochs in phases:
                if phase=='teacher' and progress['phase']=='ppo': continue
                epoch_start=progress['epoch'] if progress['phase']==phase else 0
                for epoch in range(epoch_start,epochs):
                    day_start=progress['day_index'] if progress['phase']==phase and epoch==epoch_start else 0
                    for day_index,entry in enumerate(train_days[day_start:],start=day_start):
                        started=time.perf_counter()
                        log('progress/session_start',{'day':entry['day'],'phase':phase,'epoch':epoch+1})
                        session=open_day(entry)
                        if phase=='teacher':
                            start_clock,end_clock=bounds(session.day)
                            def teacher_rate(clock):
                                position=epoch+(day_index+(clock-start_clock)/(end_clock-start_clock))/len(train_days)
                                return cosine_warmup(position,args.teacher_epochs,args.learning_rate,
                                    args.warmup_epochs,args.minimum_lr_ratio)
                            decisions,outcomes=load_teacher(Path(entry['teacher_root']),session,runtime_root=runtime)
                            result=asdict(train_session(policy,optimizer,session,decisions,outcomes,
                                device=device,clocks_per_chunk=args.clocks_per_chunk,
                                learning_rate_for_clock=teacher_rate if args.teacher_lr_schedule=='cosine' else None,
                                progress_callback=pulse('progress/teacher',session.day,epoch)))
                            result['learning_rate']=optimizer.param_groups[0]['lr']
                            del decisions,outcomes
                        else:
                            provider,tickers=evidence(session)
                            try:
                                if args.broker_engine=='tensor-100ms':
                                    env=tensor_collect(session,provider,tickers)
                                    frames,steps,result=env.frames,env.steps,env.summary
                                else:
                                    env=BracketEnvironment(tickers,provider,luld=provider.luld,risk_penalty=risk)
                                    frames,steps,result=collect_session(policy,session,env,device=device,
                                        max_orders_per_second=args.max_orders_per_second,
                                        progress_callback=pulse('progress/rollout',session.day,epoch))
                                result['ppo']=update_session(policy,optimizer,session,frames,steps,device=device,
                                    epochs=args.ppo_updates,clocks_per_chunk=args.clocks_per_chunk,
                                    decoder_batch_size=args.decoder_batch_size,
                                    bootstrap=env.bootstrap if args.broker_engine=='tensor-100ms' else None,
                                    batch_candle_projection=args.broker_engine=='tensor-100ms',
                                    progress_callback=pulse('progress/ppo',session.day,epoch))
                                result['execution_evidence']=provider.certificate()
                                del frames,steps,env
                            finally: provider.reader.close()
                        result.update(day=str(session.day),epoch=epoch+1,elapsed_seconds=time.perf_counter()-started)
                        log(f'train/{phase}',result)
                        progress.update(phase=phase,epoch=epoch,day_index=day_index+1)
                        _checkpoint(last,policy,optimizer,manifest,progress)
                        del session
                    # Retain the completed-session boundary until replay and
                    # selection finish. A crash must not skip this validation.
                    progress.update(phase=phase,epoch=epoch,day_index=len(train_days))
                    checkpoint=run/f'{phase}-epoch-{epoch+1:03d}.pt'
                    _checkpoint(checkpoint,policy,optimizer,manifest,progress)
                    _checkpoint(last,policy,optimizer,manifest,progress)
                    if args.teacher_only:
                        for entry in dev_days:
                            session=open_day(entry)
                            decisions,outcomes=load_teacher(Path(entry['teacher_root']),session,runtime_root=runtime,audit_development=True)
                            result=asdict(train_session(policy,None,session,decisions,outcomes,
                                device=device,clocks_per_chunk=args.clocks_per_chunk,evaluation=True,
                                progress_callback=pulse('progress/teacher_validation',session.day,epoch)))
                            result.update(day=str(session.day),epoch=epoch+1)
                            log('validation/teacher',result)
                            del session,decisions,outcomes
                        logger.save(str(checkpoint),base_path=str(run),policy='now')
                    elif (epoch+1)%args.replay_every==0:
                        summaries=[]
                        for entry in dev_days+diagnostics:
                            session=open_day(entry)
                            provider,tickers=evidence(session)
                            try:
                                if args.broker_engine=='tensor-100ms':
                                    env=tensor_collect(session,provider,tickers,deterministic=True)
                                    frames,steps,summary=env.frames,env.steps,env.summary
                                else:
                                    env=BracketEnvironment(tickers,provider,luld=provider.luld,risk_penalty=risk)
                                    frames,steps,summary=collect_session(policy,session,env,device=device,
                                        max_orders_per_second=args.max_orders_per_second,deterministic=True,
                                        progress_callback=pulse('progress/replay',session.day,epoch))
                                quote_cert=run/'quote-evidence'/checkpoint.stem/f'{session.day}.json'
                                _write_json(quote_cert,provider.certificate())
                                if args.broker_engine=='tensor-100ms':
                                    from research.rl_trading.v6.tensor_artifacts import save_tensor_replay
                                    replay_root,_=save_tensor_replay(env,session,checkpoint,runtime_root=runtime,
                                        source_commit=manifest['source_commit'],quote_evidence_certificate=quote_cert)
                                else:
                                    replay_root,_=save_replay(env.journal,session,checkpoint,runtime_root=runtime,
                                        source_commit=manifest['source_commit'],quote_evidence_certificate=quote_cert)
                                summary.update(day=str(session.day),replay_root=str(replay_root))
                                log('replay/development' if entry['role']=='development' else 'replay/train_in_sample',summary)
                                if logger:
                                    artifact=wandb.Artifact(f'{checkpoint.stem}-{session.day}',type='model-replay-ledger')
                                    for name in ('orders.parquet','positions.parquet','equity.parquet','metrics.json','complete.json'):
                                        artifact.add_file(str(replay_root/name))
                                    logger.log_artifact(artifact)
                                if entry['role']=='development': summaries.append(summary)
                                del frames,steps,env
                            finally: provider.reader.close()
                            del session
                        net=sum(s['modeled_net_profit'] for s in summaries)
                        validation_log=run/'validation-history.json'
                        history=json.loads(validation_log.read_text()) if validation_log.is_file() else []
                        history=[r for r in history if not (r['phase']==phase and r['epoch']==epoch+1)]
                        history.append({'phase':phase,'epoch':epoch+1,'net':net,
                            'fees':sum(s['modeled_fees'] for s in summaries),
                            'turnover':sum(s['turnover_dollars'] for s in summaries)})
                        _write_json(validation_log,history)
                        eligible=net>0 and sum(s['buy_fill_orders'] for s in summaries)>0 and all(s['terminally_flat'] for s in summaries)
                        selection=run/'selection.json'
                        old=json.loads(selection.read_text()) if selection.is_file() else None
                        if eligible and (old is None or net>old['development_net_profit']):
                            _write_json(selection,{'status':'selected_on_development','checkpoint':str(checkpoint),
                                'checkpoint_sha256':file_hash(checkpoint),'development_net_profit':net,
                                'development_summaries':summaries,'sealed_test_accessed':False})
                        recent=[r for r in history if r['phase']=='ppo'][-4:]
                        if phase=='ppo' and len(recent)==4 and all(
                                after['net']<before['net'] for before,after in zip(recent,recent[1:])):
                            progress.update(phase='stopped_validation_deterioration')
                            _checkpoint(last,policy,optimizer,manifest,progress)
                            _write_json(run/'stop.json',{'reason':'three_consecutive_development_net_declines',
                                'recent_validation':recent,'selected_checkpoint_preserved':bool(old),
                                'increasing_fees_and_turnover':all(b['fees']>a['fees'] and b['turnover']>a['turnover']
                                    for a,b in zip(recent,recent[1:]))})
                            return 0
                    progress.update(phase=phase,epoch=epoch+1,day_index=0)
                    _checkpoint(last,policy,optimizer,manifest,progress)
                if phase=='teacher' and not args.teacher_only:
                    progress.update(phase='ppo',epoch=0,day_index=0)
                    _checkpoint(last,policy,optimizer,manifest,progress)
            progress.update(phase='complete')
            _checkpoint(last,policy,optimizer,manifest,progress)
            _write_json(run/'complete.json',{'status':'teacher_trained_label_evaluated_trading_validation_pending' if args.teacher_only else 'trained_and_development_evaluated',
                'manifest_hash':manifest['hash'],'selected_checkpoint':str(run/'selection.json') if (run/'selection.json').is_file() else None,
                'heldout_replay_pending':True})
        except Exception as error:
            _write_json(run/'failure.json',{'type':type(error).__name__,'message':str(error),
                'progress':progress,'resume_boundary':'last completed session checkpoint'})
            raise
        finally:
            if logger: logger.finish()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
