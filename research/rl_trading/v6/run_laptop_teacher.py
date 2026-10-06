"""Frozen bounded train/development V6 experiment with mandatory online W&B.

This is not the full-market training campaign. Source banks and label receipts
are verified before declaring the diagnostic ticker/time subset. Never PPO or
sealed inspection. Existing full-run admission requirements remain unchanged.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import argparse
from dataclasses import asdict
from datetime import datetime
import gc
import json
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

import torch
from research.mlops.env import discover_env_files, load_env_files
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.probe_teacher_sequence import subset
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.context_splits import VERSION as CONTEXT_SPLIT_VERSION
from research.rl_trading.v6.opportunity_dataset import load_teacher
from research.rl_trading.v6.teacher_forecast import configure, CONTRACT
from research.rl_trading.v6.training import train_session


def flatten(value, prefix=''):
    output = {}
    if isinstance(value, dict):
        for key, item in value.items(): output.update(flatten(item, f'{prefix}/{key}' if prefix else str(key)))
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value): output.update(flatten(item, f'{prefix}/h{i}'))
    elif value is not None: output[prefix] = value
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-runtime', type=Path, default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    parser.add_argument('--train-day', default='2026-07-31')
    parser.add_argument('--development-day', default='2026-08-24')
    parser.add_argument('--tickers', nargs='+', default=['AAPL','NVDA','MU','CYCU','SNDK'])
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--wandb-project', default='rl-trading-v6')
    parser.add_argument('--heads', choices=('hierarchical-v2','hierarchical-v3','soft-v1'),default='hierarchical-v2')
    parser.add_argument('--teacher-loss',choices=('balanced-v2','branch-balanced-v3'),default='balanced-v2')
    parser.add_argument('--encoder',choices=('lag','mlp','tcn','gru','transformer'),default='lag')
    parser.add_argument('--structured-candle-projection',action='store_true')
    parser.add_argument('--normalize-candles',action='store_true')
    parser.add_argument('--classification-only',action='store_true',help='Diagnostic zero auxiliary weights; forecast/sizing are not trained')
    parser.add_argument('--compare-with',type=Path,help='Re-evaluate the immutable prior pilot checkpoint on identical scope')
    args = parser.parse_args(argv)
    hierarchical=args.heads in ('hierarchical-v2','hierarchical-v3')
    if hierarchical:
        from research.rl_trading.v6.hierarchical_heads import CONTRACT as experiment_contract
        if args.heads=='hierarchical-v3':
            from research.rl_trading.v6.hierarchical_heads import SEPARATE_CONTRACT as experiment_contract
    else:experiment_contract=CONTRACT
    root=Path('D:/TradingML/runtimes').resolve(); output=args.output.resolve()
    if not root.is_dir() or not output.is_relative_to(root): raise ValueError('Laptop runtime root required')
    if not 1<=args.seconds<=1800 or not 1<=args.epochs<=20 or not 1<=len(args.tickers)<=10:
        raise ValueError('Diagnostic limits: 1800 seconds, 20 epochs, 10 tickers')
    if output.exists(): raise ValueError('Use a fresh immutable experiment directory')
    if not torch.cuda.is_available(): raise ValueError('Laptop CUDA GPU required')
    from research.rl_trading.v6 import saved_label_audit as source, published_market_audit as market
    os.environ['RL_V6_LABEL_AUDIT_RUNTIME']=str(args.source_runtime.resolve())
    # Check split authority before opening any target shard.
    entries=[source.session(day)[1] for day in (args.train_day,args.development_day)]
    if [e['role'] for e in entries]!=['train','development']: raise ValueError('Requires distinct public train/development roles')
    output.mkdir(parents=True)
    torch.manual_seed(17);torch.set_num_threads(4)
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    import wandb
    started=time.perf_counter()
    manifest=dict(version='rl-v6-laptop-development-v2',contract=experiment_contract,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
        device=torch.cuda.get_device_name(),seed=17,scope='bounded_ticker_subset_not_full_market',
        forecast_metrics_target='explicit_saved_1b_action',
        sizing_denominator='original_full_market_1b_targets_preserved',sealed_labels_read=False,
        context_split_version=CONTEXT_SPLIT_VERSION,
        ppo=False,source_files_sha256={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')})
    from research.rl_trading.v6.context_splits import receipt as context_receipt
    manifest['context_split_receipts']={e['day']:context_receipt(source.runtime(),
        source.read_json(source.mapped(e['bank_root'])/'plan.json'))[1] for e in entries}
    manifest['hash']=digest(manifest)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    logger=wandb.init(project=args.wandb_project,name=output.name,mode='online',dir=str(output),config=manifest)
    try:
        if logger is None or logger.settings.mode!='online': raise ValueError('Online W&B required')
        def write(name, value):
            (output/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
        write('wandb.json',dict(url=logger.url,id=logger.id,project=logger.project,entity=logger.entity))
        def prepare(day, role):
            print(f'Verifying {role} full bank and label receipts: {day}',flush=True)
            active,entry,_,teacher,bankroot,_=source.session(day)
            symbols=source.saved_symbols(str(bankroot),entry['bank_certificate_sha256'])
            ids=sorted(i for i,t in symbols.items() if t in args.tickers)
            if len(ids)!=len(args.tickers): raise ValueError('Requested tickers must resolve uniquely on both days')
            full=open_session(bankroot,runtime_root=source.runtime(),previous_root=source.mapped(entry['previous_root']))
            if full.context_split_receipt_sha256!=manifest['context_split_receipts'][day]:
                raise ValueError('Pilot split metadata changed after admission')
            ma,_,me,mroot,_=market.session(day)
            labels,_=load_teacher(teacher,full,runtime_root=source.runtime(),audit_development=True,audit_listing_ids=ids,market_root=mroot)
            begin=int(datetime.fromisoformat(day+'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1_000_000)
            packed,selected=subset(full,labels,ids,begin,begin+args.seconds*1_000_000-1)
            if not selected: raise ValueError('Empty diagnostic session')
            del full,labels;gc.collect()
            write(f'{role}-scope.json',dict(day=day,role=role,identities=ids,begin_us=begin,
                end_us=begin+args.seconds*1_000_000,decisions=len(selected),
                allocation_targets=sum(d.allocation_ratio_target is not None for d in selected),
                dataset_sha256=active['sha256'],market_dataset_sha256=ma['sha256'],
                bank_certificate_sha256=entry['bank_certificate_sha256'],market_certificate_sha256=me['sha256'],
                context_split_receipt_sha256=packed.context_split_receipt_sha256))
            return packed,selected
        training,targets=prepare(args.train_day,'train')
        development,dev_targets=prepare(args.development_day,'development')
        device=torch.device('cuda')
        ranking=MarketAttentionConfig(**source.published()[1]['ranking'])
        normalization=None
        if args.normalize_candles:
            from research.rl_trading.v6.bias_panel import indexed_panel,normalization as fit_normalization
            normalization=fit_normalization(indexed_panel(training,targets))
            write('normalization.json',normalization)
        manifest['normalization_sha256']=file_hash(output/'normalization.json') if normalization else None
        manifest['hash']=digest({k:v for k,v in manifest.items() if k!='hash'});write('manifest.json',manifest)
        logger.config.update(manifest,allow_val_change=True)
        def new_policy():
            policy=RankedBracketActorCritic(config=ranking,wait_hold=True).to(device)
            if args.encoder!='lag' or args.structured_candle_projection:
                from research.rl_trading.v6.temporal_encoders import replace_encoder
                replace_encoder(policy,args.encoder,structured=args.structured_candle_projection)
            configure(policy,hierarchical=hierarchical,shared_heads=args.heads!='hierarchical-v3')
            if normalization:policy.configure_candle_features(normalization)
            policy.independent_episode_supervision=True
            return policy
        previous_evaluation=None
        if args.compare_with:
            if normalization or args.encoder!='lag' or args.structured_candle_projection:raise ValueError('Legacy comparison requires original input/encoder contract')
            prior=args.compare_with.resolve()
            if not prior.is_relative_to(root):raise ValueError('Comparison must be an existing laptop runtime')
            old_complete=json.loads((prior/'complete.json').read_text())
            if old_complete['status']!='completed' or file_hash(prior/'last.pt')!=old_complete['checkpoint_sha256']:
                raise ValueError('Comparison checkpoint binding changed')
            for name in ('train-scope.json','development-scope.json'):
                if json.loads((prior/name).read_text())!=json.loads((output/name).read_text()):
                    raise ValueError('Comparison requires identical data/window/ticker receipts')
            previous=configure(RankedBracketActorCritic(config=ranking,wait_hold=True).to(device))
            previous.independent_episode_supervision=True
            saved=torch.load(prior/'last.pt',map_location=device,weights_only=True)
            if saved['contract']!=CONTRACT:raise ValueError('Expected the prior soft-v1 pilot')
            previous.load_state_dict(saved['model'],strict=True)
            previous_evaluation=asdict(train_session(previous,None,development,dev_targets,(),device=device,evaluation=True))
            write('previous-checkpoint-evaluation.json',dict(metrics=previous_evaluation,checkpoint_sha256=file_hash(prior/'last.pt'),
                target_contract='saved_hard_action_metrics_soft_v1_loss',source=str(prior)))
            del previous,saved;gc.collect();torch.cuda.empty_cache()
        torch.manual_seed(17)
        policy=new_policy()
        auxiliary_weights=dict(ratio=0.,forecast=0.,quality=0.,future_quality=0.) if args.classification_only else None
        optimizer=torch.optim.Adam(policy.parameters(),lr=3e-4)
        def evaluate(): return asdict(train_session(policy,None,development,dev_targets,(),device=device,evaluation=True))
        baseline=evaluate();records=[]
        logger.log({**flatten(baseline,'development'),
                    **(flatten(previous_evaluation,'previous_checkpoint_development') if previous_evaluation is not None else {})},step=0)
        write('baseline.json',baseline)
        for epoch in range(1,args.epochs+1):
            def progress(value):
                write('progress.json',dict(phase='training',epoch=epoch,**value))
            trained=asdict(train_session(policy,optimizer,training,targets,(),device=device,
                clocks_per_chunk=32,teacher_loss=args.teacher_loss,progress_callback=progress,auxiliary_weights=auxiliary_weights,
                regression_weights=(0.,0.) if args.classification_only else (1.,1.)))
            evaluated=evaluate()
            training_evaluation=asdict(train_session(policy,None,training,targets,(),device=device,evaluation=True,evaluate_train=True))
            record=dict(epoch=epoch,training=trained,training_evaluation=training_evaluation,development=evaluated,auxiliary_supervised=not args.classification_only)
            records.append(record)
            with (output/'metrics.jsonl').open('a',encoding='utf-8') as stream: stream.write(json.dumps(record)+'\n')
            logger.log({**flatten(trained,'training'),**flatten(training_evaluation,'training_evaluation'),**flatten(evaluated,'development'), 'epoch':epoch},step=epoch)
            torch.save(dict(model=policy.state_dict(),optimizer=optimizer.state_dict(),contract=experiment_contract,epoch=epoch),output/'last.pt')
            print(json.dumps(dict(epoch=epoch,development_f1=evaluated['action_class_f1'],allocation_mae=evaluated['allocation_ratio_mae'])),flush=True)
        restored=new_policy()
        restored.load_state_dict(torch.load(output/'last.pt',map_location=device,weights_only=True)['model'],strict=True)
        replay=asdict(train_session(restored,None,development,dev_targets,(),device=device,evaluation=True))
        if replay!=records[-1]['development']:raise ValueError('Checkpoint reload changed development evaluation')
        logger.summary['completion_status']='completed'
        write('complete.json',dict(status='completed',baseline=baseline,final=records[-1],epochs=args.epochs,
            previous_checkpoint_development=previous_evaluation,contract=experiment_contract,reload_exact=True,
            wandb_url=logger.url,elapsed_seconds=time.perf_counter()-started,checkpoint_sha256=file_hash(output/'last.pt'),
            sealed_labels_read=False,workstation_gpu_used=False,scope=manifest['scope']))
        for name in ('manifest.json','metrics.jsonl','baseline.json','complete.json','train-scope.json','development-scope.json'):
            logger.save(str(output/name),base_path=str(output),policy='now')
        if previous_evaluation is not None:logger.save(str(output/'previous-checkpoint-evaluation.json'),base_path=str(output),policy='now')
    finally:
        logger.finish()
    return 0


if __name__=='__main__': raise SystemExit(main())
