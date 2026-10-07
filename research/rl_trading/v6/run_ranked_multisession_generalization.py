"""Fixed chronological pilot, admitted only by its own six-session underfit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
from dataclasses import asdict
from datetime import datetime
import gc
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
import numpy as np
import torch
from research.mlops.env import discover_env_files,load_env_files
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_multisession_gate import admit_multisession
from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
from research.rl_trading.v6.ranked_teacher_data import load_prepared
from research.rl_trading.v6.run_ranked_multisession_underfit import selected_targets
from research.rl_trading.v6.run_ranked_teacher_generalization import build_policy,evaluate_probabilities,exact_metrics
from research.rl_trading.v6.run_laptop_teacher import flatten
from research.rl_trading.v6.training import train_session


def bind_train_cache(root, source, binding, prior):
    proof=json.loads(source.read_text())
    if (file_hash(source)!=binding['source_sha256'] or proof.get('hash')!=digest({k:v for k,v in proof.items() if k!='hash'}) or
            proof['dataset_sha256']!=prior['dataset_sha256'] or proof['market_dataset_sha256']!=prior['market_dataset_sha256'] or
            file_hash(root/'prepared-train.pt')!=binding['cache_sha256']):
        raise ValueError('Six-session TRAIN source/content binding changed')
    s,t,_=load_prepared(root,proof)
    if (s.day.isoformat()!=binding['day'] or list(s.listings)!=binding['input_listings'] or
            s.source_certificate_sha256!=binding['bank_certificate_sha256'] or
            s.context_split_receipt_sha256!=binding['context_split_receipt_sha256']):
        raise ValueError('Six-session TRAIN population changed')
    return s,t


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--underfit',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-runtime',type=Path,default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    parser.add_argument('--development-day',default='2026-08-24')
    parser.add_argument('--epochs',type=int,default=10)
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or args.output.exists() or not all(p.resolve().is_relative_to(runtime) for p in (args.underfit,args.output)) or not 1<=args.epochs<=10 or not torch.cuda.is_available():
        raise ValueError('Fresh bounded laptop GPU pilot required')
    prior,complete,normalization=admit_multisession(args.underfit)
    resources=prior.get('laptop_resources')
    if resources is None:
        raise ValueError('Laptop resource settings require a newly verified underfit run')
    if args.development_day<=max(s['day'] for s in prior['sessions']):
        raise ValueError('Development date must follow every TRAIN session')
    selection_path=Path(prior['arguments']['selection'])
    if not selection_path.resolve().is_relative_to(runtime) or file_hash(selection_path)!=prior['selection_sha256']:
        raise ValueError('Admitted underfit selection changed')
    selection=json.loads(selection_path.read_text());sessions=[];tiny=[]
    for i,b in enumerate(prior['sessions']):
        root=Path(b['cache']);source=Path(prior['arguments']['initial_source']) if i==0 else root/'source.json'
        if not root.resolve().is_relative_to(runtime) or not source.resolve().is_relative_to(runtime):
            raise ValueError('Only verified laptop TRAIN caches admitted')
        s,t=bind_train_cache(root,source,b,prior);sessions.append((s,t))
        tiny.append((s,selected_targets(selection,b['day'],b['cache_sha256'],t)))
    torch.manual_seed(17);torch.set_num_threads(4);device=torch.device('cuda')
    ranking=MarketAttentionConfig(**prior['ranking'])
    def model():
        policy=build_policy(ranking,device,width=prior.get('width',128),normalization=normalization)
        policy.encoder.activation_checkpointing=prior.get('activation_checkpointing',False)
        policy.encoder.history_microbatch=resources['history_microbatch']
        from research.rl_trading.v6.laptop_resources import LaptopGpuPacer
        policy.resource_pacer=LaptopGpuPacer(device,duty_cycle=resources['duty_cycle'],reserve_bytes=resources['reserve_bytes'])
        return policy
    policy=model();policy.load_state_dict(torch.load(args.underfit/'last.pt',weights_only=True),strict=True)
    def evaluate(p,targets):
        reports=[];probabilities=[]
        for s,t in targets:
            evidence={}
            m,_,values=evaluate_probabilities(p,s,t,device,regression_evidence=evidence)
            m.update(evidence);reports.append(m)
            probabilities.append(dict(day=s.day.isoformat(),values=values))
        return pool_gate_metrics(reports),reports,probabilities
    replay,reports,_=evaluate(policy,tiny)
    if not exact_metrics(replay,complete['metrics']):raise ValueError('Six-session underfit replay changed')
    args.output.mkdir()
    def write(name,value):(args.output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
    write('underfit-replay.json',dict(exact=True,metrics=replay,sessions=reports))
    plan=dict(version='rl-v6-ranked-six-session-generalization-v1',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        underfit_manifest_sha256=file_hash(args.underfit/'manifest.json'),underfit_checkpoint_sha256=complete['checkpoint_sha256'],
        normalization_sha256=prior['normalization_sha256'],feature_contract=prior['feature_contract'],
        train_sessions=prior['sessions'],development_day=args.development_day,epochs=args.epochs,
        checkpoint_selection='fixed_final_epoch_before_development_targets',width=prior.get('width',128),
        activation_checkpointing=prior.get('activation_checkpointing',False),learning_rate=3e-4,weight_decay=1e-4,
        laptop_resources=resources,
        teacher_loss=prior['teacher_loss'],auxiliary_weights=prior['auxiliary_weights'],
        initialization='verified_six_session_underfit_weights_fresh_optimizer',sealed_labels_read=False,
        workstation_gpu_used=False,input_population_preserved=True,
        source_files_sha256={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')})
    plan['hash']=digest(plan);write('manifest.json',plan)
    load_env_files(discover_env_files(Path.cwd()),verbose=False)
    import wandb
    logger=wandb.init(project='rl-trading-v6',name=args.output.name,mode='online',dir=str(args.output),config=plan)
    if logger is None or logger.settings.mode!='online':raise ValueError('Online W&B required')
    write('wandb.json',dict(id=logger.id,url=logger.url))
    optimizer=torch.optim.AdamW(policy.parameters(),lr=3e-4,weight_decay=1e-4)
    try:
        for epoch in range(1,args.epochs+1):
            fits=[]
            for s,t in sessions:
                write('progress.json',dict(phase='training',epoch=epoch,day=s.day.isoformat()))
                fits.append(asdict(train_session(policy,optimizer,s,t,(),device=device,teacher_loss='branch-balanced-v3',regression_weights=(0.,0.))))
            metrics,reports,probabilities=evaluate(policy,sessions)
            record=dict(epoch=epoch,fit=fits,metrics=metrics,sessions=reports)
            write('train-result.json',record);logger.log(flatten(record),step=epoch)
            with (args.output/'metrics.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(record)+'\n')
            torch.save(dict(model=policy.state_dict(),optimizer=optimizer.state_dict(),epoch=epoch),args.output/f'epoch-{epoch:02d}.pt')
            print(json.dumps(dict(epoch=epoch,f1=metrics['action_class_f1'],ratio=metrics['allocation_ratio_mae'])),flush=True)
        torch.save(policy.state_dict(),args.output/'selected.pt')
        selected=dict(epoch=args.epochs,checkpoint_sha256=file_hash(args.output/'selected.pt'),frozen_before_development_targets=True)
        write('selection.json',selected)
        restored=model();restored.load_state_dict(torch.load(args.output/'selected.pt',weights_only=True),strict=True)
        repeated,repeated_reports,_=evaluate(restored,sessions)
        if not exact_metrics(repeated,metrics) or not exact_metrics(repeated_reports,reports):raise ValueError('Six-session natural TRAIN replay changed')
        for item in probabilities:np.savez_compressed(args.output/('train-'+item['day']+'-probabilities.npz'),**item['values'])
        del policy,optimizer,sessions,tiny;gc.collect();torch.cuda.empty_cache()
        from research.rl_trading.v6 import saved_label_audit as source,published_market_audit as market
        from research.rl_trading.v6.session_data import open_session
        from research.rl_trading.v6.opportunity_dataset import load_teacher
        from research.rl_trading.v6.probe_teacher_sequence import subset
        os.environ['RL_V6_LABEL_AUDIT_RUNTIME']=str(args.source_runtime.resolve())
        write('progress.json',dict(phase='verifying_development',day=args.development_day))
        active,entry,_,teacher,bankroot,_=source.session(args.development_day)
        if entry['role']!='development':raise ValueError('Only public development targets admitted')
        ma,_,me,mroot,_=market.session(args.development_day)
        if active['sha256']!=prior['dataset_sha256'] or ma['sha256']!=prior['market_dataset_sha256']:raise ValueError('Development dataset authority changed')
        names=source.saved_symbols(str(bankroot),entry['bank_certificate_sha256'])
        ids=sorted(i for i,name in names.items() if name in prior['tickers'])
        if len(ids)!=len(prior['tickers']) or {names[i] for i in ids}!=set(prior['tickers']):raise ValueError('Development target identity coverage changed')
        full=open_session(bankroot,runtime_root=source.runtime(),previous_root=source.mapped(entry['previous_root']))
        labels,_=load_teacher(teacher,full,runtime_root=source.runtime(),audit_development=True,audit_listing_ids=ids,market_root=mroot)
        begin=int(datetime.fromisoformat(args.development_day+'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1_000_000)
        development,targets=subset(full,labels,full.listings,begin,begin+600_000_000-1)
        if development.listings!=full.listings or not targets:raise ValueError('Full development market inputs required')
        write('development-scope.json',dict(day=args.development_day,role=entry['role'],input_listings=list(development.listings),target_ids=ids,
            bank_certificate_sha256=entry['bank_certificate_sha256'],market_certificate_sha256=me['sha256'],context_split_receipt_sha256=development.context_split_receipt_sha256))
        del full,labels;gc.collect()
        if file_hash(args.output/'selected.pt')!=selected['checkpoint_sha256']:raise ValueError('Frozen checkpoint changed')
        dev,prob,values=evaluate_probabilities(restored,development,targets,device)
        write('development.json',dict(metrics=dev,probabilities=prob));np.savez_compressed(args.output/'development-probabilities.npz',**values)
        logger.log({**flatten(dev,'development'),**flatten(prob,'development_probability')},step=args.epochs+1)
        write('complete.json',dict(status='completed',underfit_gate_verified=True,train_reload_exact=True,
            checkpoint_sha256=selected['checkpoint_sha256'],train_metrics=metrics,development_metrics=dev,
            development_probabilities=prob,generalization_evaluated=True,sealed_labels_read=False,
            workstation_gpu_used=False,production_teacher_certified=False,wandb_url=logger.url))
        logger.summary['completion_status']='completed'
        for name in ('manifest.json','complete.json','metrics.jsonl','selection.json','development.json','development-scope.json'):
            logger.save(str(args.output/name),base_path=str(args.output),policy='now')
    finally:logger.finish()
    return 0


if __name__=='__main__':raise SystemExit(main())
