"""Sequential laptop-GPU bias controls with frozen public-only data and W&B.

Architectures are selected on the chronological training calibration fold.
Development is opened for evaluation only after the selected trial is frozen.
This is a bounded diagnostic campaign, not final full-market teacher training.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
from dataclasses import dataclass,asdict
import gc
import json
from pathlib import Path
import subprocess
import time
import numpy as np
import torch
from torch.nn import functional as F
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.bias_panel import verify_panel,normalization,FOLDS,combine,PRICE_DIVERSITY_VERSION,DIVERSITY_FOLDS
from research.rl_trading.v6.bias_models import LocalWindowTeacher,balance_weights,current_objective,VERSION
from research.rl_trading.v6.bias_metrics import action_report,calibration_thresholds,fit_probability_calibration,calibrated_probability


@dataclass(frozen=True)
class Trial:
    name:str
    architecture:str='lag'
    balance:str='branch'
    auxiliary:bool=False
    shared_forecast:bool=False
    structured:bool=False
    stationary:bool=False


TRIALS=(Trial('lag_natural_current',balance='natural'),Trial('lag_sqrt_current',balance='sqrt'),
    Trial('lag_branch_current'),Trial('lag_branch_tied_aux',auxiliary=True,shared_forecast=True),
    Trial('lag_branch_separate_aux',auxiliary=True),Trial('tcn_branch_current',architecture='tcn'),
    Trial('gru_branch_current',architecture='gru'),Trial('transformer_branch_current',architecture='transformer'),
    Trial('mlp_branch_current',architecture='mlp'),Trial('tcn_structured_current',architecture='tcn',structured=True),
    Trial('tcn_structured_focal',architecture='tcn',structured=True,balance='focal'),
    *(Trial(architecture+'_stationary_branch',architecture=architecture,stationary=True) for architecture in ('lag','tcn','gru','transformer','mlp')),
    Trial('tcn_stationary_structured',architecture='tcn',stationary=True,structured=True))


def write(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8');temporary.replace(path)


def flatten(value,prefix=''):
    result={}
    if isinstance(value,dict):
        for k,v in value.items():result.update(flatten(v,f'{prefix}/{k}' if prefix else k))
    elif isinstance(value,(list,tuple)):
        for index,item in enumerate(value):result.update(flatten(item,f'{prefix}/h{index}'))
    elif value is not None:result[prefix]=value
    return result


def tensors(panel,norm,device):
    mean=np.asarray(norm['mean'],np.float32);std=np.asarray(norm['std'],np.float32)
    features=(panel['features']-mean)/std;features[0]=0
    return {**{k:torch.as_tensor(panel[k],device=device) for k in ('windows','action','weight','future','quality','ratio','held')},
        'features':torch.as_tensor(features,device=device),
        'market':torch.as_tensor((panel['market']-mean)/std,device=device),
        'actions_numpy':panel['action'],'episodes':panel['episode']}


def reserve_calibration(panel,manifest):
    """Pre-experiment chronological reservation; never select using development."""
    if manifest.get('version')==PRICE_DIVERSITY_VERSION:
        if manifest['folds']!={k:list(v) for k,v in DIVERSITY_FOLDS.items()}:
            raise ValueError('Training diversity fold scope changed')
        if panel['train']['clock'].max()>=panel['calibration']['clock'].min():
            raise ValueError('Calibration must follow every training decision')
        return panel
    begin=manifest['sessions']['2026-08-04']['begin_us']
    source=panel['train'];later=source['clock']>=begin
    def records(mask):
        return {k:(v if k=='features' else [x for x,good in zip(v,mask) if good] if k=='episode' else v[mask]) for k,v in source.items()}
    return dict(train=records(~later),calibration=combine([records(later),panel['calibration']]),development=panel['development'])


def forward(model,data,index,*,future=False,forcing=None):
    ids=data['windows'][index].long();present=ids!=0
    return model(data['features'][ids],present,data['market'][index],data['held'][index],
        future_steps=5 if future else 0,forcing=forcing)


def record_subset(data,mask):
    device=data['action'].device;selection=torch.as_tensor(mask,device=device)
    out=dict(data)
    for key in ('windows','market','held','action','weight','future','quality','ratio'):out[key]=data[key][selection]
    out['actions_numpy']=data['actions_numpy'][mask]
    out['episodes']=[x for x,good in zip(data['episodes'],mask) if good]
    return out


@torch.no_grad()
def evaluate(model,data,*,batch_size=256,thresholds=(.5,.5),auxiliary=False):
    model.eval();probabilities=[];ratios=[];qualities=[];future=[];future_quality=[]
    for start in range(0,len(data['action']),batch_size):
        index=torch.arange(start,min(start+batch_size,len(data['action'])),device=data['action'].device)
        result=forward(model,data,index,future=auxiliary)
        probabilities.append(result['logit'].sigmoid().cpu().numpy())
        ratios.append(result['ratio'].cpu().numpy());qualities.append(result['quality'].cpu().numpy())
        if auxiliary:
            future.append(result['future'].exp().cpu().numpy())
            future_quality.append(result['future_quality'].cpu().numpy())
    probability=np.concatenate(probabilities);actions=data['actions_numpy'];quality=np.concatenate(qualities)
    report=action_report(actions,probability,thresholds)
    report['ratio_mae']=None;report['quality_mae']={};report['auxiliary_supervised']=auxiliary
    target=data['ratio'].cpu().numpy();good=np.isfinite(target)
    if auxiliary and good.any():report['ratio_mae']=float(np.abs(np.concatenate(ratios)[good]-target[good]).mean())
    report['ratio_targets']=int(good.sum());q=data['quality'].cpu().numpy()
    for label,c,branch in (('ENTRY',0,0),('EXIT',3,1)):
        selected=actions==c
        report['quality_mae'][label]=float(np.abs(quality[selected,branch]-q[selected]).mean()) if auxiliary and selected.any() else None
    if auxiliary:
        future_probability=np.concatenate(future);target=data['future'].cpu().numpy();forecasts=[]
        from sklearn.metrics import average_precision_score
        for h in range(5):
            valid=target[:,h]>=0;y=target[valid,h];p=future_probability[valid,h]
            prediction=p.argmax(1);metrics={}
            for c,label in enumerate(('ENTRY','WAIT','HOLD','EXIT')):
                tp=int(((y==c)&(prediction==c)).sum());pred=int((prediction==c).sum());actual=int((y==c).sum())
                metrics[label]=dict(count=actual,predicted=pred,f1=2*tp/(pred+actual) if pred+actual else 0.,
                    average_precision=float(average_precision_score(y==c,p[:,c])) if actual else None)
            forecasts.append(metrics)
        report['future']=forecasts
        if 'future_quality' in data:
            predicted=np.concatenate(future_quality);expected=data['future_quality'].cpu().numpy()
            report['future_quality_mae']=[{name:float(np.abs(predicted[target[:,h]==c,h,b]-expected[target[:,h]==c,h]).mean()) if (target[:,h]==c).any() else None for name,c,b in (('ENTRY',0,0),('EXIT',3,1))} for h in range(5)]
    return report,probability


def run_epoch(model,data,optimizer,trial,*,epoch,batch_size=256,rows=None):
    model.train();n=len(data['action']);rows=np.arange(n) if rows is None else np.asarray(rows)
    actions=data['actions_numpy'][rows];sample_weights=data['weight'][rows].cpu().numpy()
    current=torch.as_tensor(balance_weights(actions,sample_weights,mode=trial.balance),device=data['action'].device)
    order=np.random.default_rng(17+epoch).permutation(rows);denominator=float(sample_weights.sum()/len(rows))
    future_np=data['future'][rows].cpu().numpy();valid=future_np>=0
    repeated=np.repeat(sample_weights[:,None],5,1)
    future_weights=torch.as_tensor(balance_weights(future_np[valid],repeated[valid],mode='branch'),device=data['action'].device) if trial.auxiliary else None
    losses=[];gradients=[]
    for start in range(0,len(order),batch_size):
        index=torch.as_tensor(order[start:start+batch_size],device=data['action'].device)
        result=forward(model,data,index,future=trial.auxiliary)
        loss=current_objective(result['logit'],data['action'][index],data['weight'][index],current,focal=trial.balance=='focal').mean()/denominator
        if trial.auxiliary:
            action=data['action'][index];weight=data['weight'][index]
            selected=(action==0)|(action==3)
            if selected.any():
                branch=(action==3).long();prediction=result['quality'].gather(1,branch[:,None]).flatten()
                quality=F.smooth_l1_loss(prediction,data['quality'][index],beta=.1,reduction='none')
                loss+=.1*(quality[selected]*weight[selected]).sum()/weight[selected].sum()
            target=data['ratio'][index];good=torch.isfinite(target)
            if good.any():loss+=.1*F.smooth_l1_loss(result['ratio'][good],target[good],beta=.1)
            targets=data['future'][index];mask=targets>=0;safe=targets.clamp_min(0)
            ce=-result['future'].gather(2,safe[...,None]).squeeze(-1)
            hard=F.one_hot(safe,4).float();forced=forward(model,data,index,future=True,forcing=hard)['future']
            forced_ce=-forced.gather(2,safe[...,None]).squeeze(-1)
            future_loss=((ce+forced_ce)*.5*future_weights[safe]*mask*weight[:,None]).sum()/(mask*weight[:,None]).sum().clamp_min(1e-9)
            loss+=.25*future_loss
            if 'future_quality' in data:
                selected=mask & ((targets==0)|(targets==3));branch=(targets==3).long()
                quality=result['future_quality'].gather(2,branch[...,None]).squeeze(-1)
                forced_quality=model.forecast.quality_predictions.gather(2,branch[...,None]).squeeze(-1)
                expected=data['future_quality'][index]
                error=.5*(F.smooth_l1_loss(quality,expected,beta=.1,reduction='none')+F.smooth_l1_loss(forced_quality,expected,beta=.1,reduction='none'))
                loss+=.1*(error*selected*weight[:,None]).sum()/(selected*weight[:,None]).sum().clamp_min(1e-9)
        if not torch.isfinite(loss):raise ValueError('Nonfinite bias-campaign objective')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        norms={name:float(torch.linalg.vector_norm(torch.stack([p.grad.norm() for p in module.parameters() if p.grad is not None])))
            for name,module in (('encoder',model.encoder),('entry',model.heads.entry),('exit',model.heads.exit))}
        if not all(np.isfinite(x) for x in norms.values()):raise ValueError('Nonfinite classification gradient')
        torch.nn.utils.clip_grad_norm_(model.parameters(),5.);optimizer.step()
        losses.append(float(loss.detach()));gradients.append(norms)
    return dict(loss=float(np.mean(losses)),gradient_norms={k:float(np.mean([r[k] for r in gradients])) for k in gradients[0]},class_weights=current.cpu().tolist(),updates=len(losses))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--epochs',type=int,default=20);parser.add_argument('--batch-size',type=int,default=256)
    parser.add_argument('--only',nargs='+',choices=[t.name for t in TRIALS])
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();output=args.output.resolve();source=args.panel.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime) or not source.is_relative_to(runtime) or output.exists():raise ValueError('Fresh laptop runtime and authenticated panel required')
    if not 1<=args.epochs<=40 or not 16<=args.batch_size<=512 or not torch.cuda.is_available():raise ValueError('Bounded laptop CUDA campaign required')
    proof=json.loads((source/'complete.json').read_text());panel_manifest=json.loads((source/'manifest.json').read_text())
    if proof['status']!='prepared' or proof['manifest_sha256']!=file_hash(source/'manifest.json') or proof['panel_sha256']!=file_hash(source/'panel.pt'):raise ValueError('Panel authentication failed')
    if panel_manifest['hash']!=digest({k:v for k,v in panel_manifest.items() if k!='hash'}):raise ValueError('Panel manifest changed')
    panel=torch.load(source/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,panel_manifest)
    panel=reserve_calibration(panel,panel_manifest)
    norm=normalization(panel['train']);device=torch.device('cuda');torch.set_num_threads(4)
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    import wandb
    trials=[t for t in TRIALS if args.only is None or t.name in args.only]
    output.mkdir();manifest=dict(version=VERSION,panel_sha256=proof['panel_sha256'],panel_manifest_sha256=proof['manifest_sha256'],
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_files_sha256={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')},
        trials=[asdict(t) for t in trials],epochs=args.epochs,batch_size=args.batch_size,normalization=norm,normalization_sha256=digest(norm),
        device=torch.cuda.get_device_name(),seed=17,sealed_labels_read=False,workstation_gpu_used=False,
        selection_fold='calibration_only_training_role_Aug10_Aug11' if panel_manifest['version']==PRICE_DIVERSITY_VERSION else 'calibration_only_training_role_2026-08-04_and_2026-08-21',
        training_dates=list(DIVERSITY_FOLDS['train']) if panel_manifest['version']==PRICE_DIVERSITY_VERSION else ['2026-07-31','2026-08-03'],development='opened_after_trial_selection',
        prior_inspected_development_dates=list(panel_manifest['folds']['development']) if panel_manifest['version']==PRICE_DIVERSITY_VERSION else ['2026-08-24','2026-08-25'],
        new_public_development_dates=[] if panel_manifest['version']==PRICE_DIVERSITY_VERSION else [d for d in panel_manifest['folds']['development'] if d not in ('2026-08-24','2026-08-25')],
        scope=panel_manifest['scope'],auxiliary_loss_weights=dict(forecast=.25,quality=.1,ratio=.1),future_quality_supervised=False)
    manifest['hash']=digest(manifest);write(output/'manifest.json',manifest)
    logger=wandb.init(project='rl-trading-v6',name=output.name,mode='online',dir=str(output),config=manifest)
    if logger is None or logger.settings.mode!='online':raise ValueError('Online W&B required')
    write(output/'wandb.json',dict(url=logger.url,id=logger.id,entity=logger.entity,project=logger.project))
    train=tensors(panel['train'],norm,device);calibration=tensors(panel['calibration'],norm,device)
    started=time.time();summaries=[]
    try:
        # Equal row counts are deliberate only in this TRAIN memorization check.
        rng=np.random.default_rng(17);small=np.concatenate([rng.choice(np.flatnonzero(panel['train']['action']==c),32,replace=False) for c in range(4)])
        for architecture in ('lag','tcn','gru','transformer','mlp'):
            torch.manual_seed(17);model=LocalWindowTeacher(architecture,width=128).to(device)
            optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=0.)
            trial=Trial('memorize_'+architecture,architecture=architecture)
            tiny={**train,'actions_numpy':panel['train']['action'][small], 'episodes':[panel['train']['episode'][i] for i in small]}
            # Gather the record axes while retaining the common causal feature table.
            for key in ('windows','market','held','action','weight','future','quality','ratio'):tiny[key]=train[key][small]
            memorized=None
            for epoch in range(1,201):
                fit=run_epoch(model,tiny,optimizer,trial,epoch=epoch,batch_size=128)
                if epoch%10==0:
                    memorized,_=evaluate(model,tiny,batch_size=128)
                    logger.log(flatten(memorized,f'memorization/{architecture}'))
                    if min(memorized[c]['f1'] for c in ('ENTRY','EXIT'))>=.95:break
            write(output/f'memorization-{architecture}.json',dict(epochs=epoch,metrics=memorized,fit=fit,rows=small.tolist()))
            print('memorization',architecture,epoch,{c:memorized[c]['f1'] for c in ('ENTRY','EXIT')},flush=True)
            del model,optimizer;gc.collect();torch.cuda.empty_cache()
        step=0
        for trial in trials:
            folder=output/trial.name;folder.mkdir();torch.manual_seed(17)
            model=LocalWindowTeacher(trial.architecture,structured=trial.structured,shared_forecast=trial.shared_forecast,stationary=trial.stationary,normalization=norm).to(device)
            optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4);best=-1.;records=[]
            for epoch in range(1,args.epochs+1):
                fit=run_epoch(model,train,optimizer,trial,epoch=epoch,batch_size=args.batch_size)
                trained,_=evaluate(model,train,batch_size=args.batch_size)
                calibrated,probability=evaluate(model,calibration,batch_size=args.batch_size,auxiliary=trial.auxiliary)
                score=float(np.mean([calibrated[c]['average_precision'] or 0. for c in ('ENTRY','EXIT')]))
                record=dict(epoch=epoch,fit=fit,training=trained,calibration=calibrated,selection_score=score);records.append(record)
                with (folder/'metrics.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(record,allow_nan=False)+'\n')
                step+=1;logger.log(flatten(record,trial.name))
                write(output/'progress.json',dict(phase='training',trial=trial.name,epoch=epoch,completed=[r['trial']['name'] for r in summaries]))
                if score>best:
                    best=score;thresholds=calibration_thresholds(panel['calibration']['action'],probability)
                    torch.save(dict(model=model.state_dict(),trial=asdict(trial),epoch=epoch,normalization=norm,manifest_hash=manifest['hash'],thresholds=thresholds),folder/'selected.pt')
                    write(folder/'selection.json',dict(epoch=epoch,score=score,thresholds=thresholds,calibration=calibrated))
                print(trial.name,epoch,'trainF1',*[round(trained[c]['f1'],3) for c in ('ENTRY','EXIT')], 'calAP',*[round(calibrated[c]['average_precision'] or 0.,3) for c in ('ENTRY','EXIT')],flush=True)
            summary=dict(trial=asdict(trial),selection=json.loads((folder/'selection.json').read_text()),checkpoint_sha256=file_hash(folder/'selected.pt'))
            summaries.append(summary);write(output/'trials.json',summaries)
            del model,optimizer;gc.collect();torch.cuda.empty_cache()
        selected=max(summaries,key=lambda r:r['selection']['score'])
        write(output/'selected-trial.json',dict(**selected,selected_before_development=True))
        # Only now admit development feature/target tensors for fixed-checkpoint evaluation.
        development=tensors(panel['development'],norm,device);results=[]
        compare={selected['trial']['name'],trials[0].name}
        for summary in summaries:
            name=summary['trial']['name']
            if name not in compare:continue
            saved=torch.load(output/name/'selected.pt',map_location=device,weights_only=False)
            config=summary['trial'];model=LocalWindowTeacher(config['architecture'],structured=config['structured'],shared_forecast=config['shared_forecast'],stationary=config['stationary'],normalization=norm).to(device)
            model.load_state_dict(saved['model'],strict=True)
            calibrated,cp=evaluate(model,calibration,batch_size=args.batch_size,auxiliary=config['auxiliary'])
            # Deterministic selected-checkpoint replay must match saved calibration statistics.
            if calibrated!=summary['selection']['calibration']:raise ValueError('Checkpoint replay changed calibration metrics')
            natural,dp=evaluate(model,development,batch_size=args.batch_size,auxiliary=config['auxiliary'])
            adjusted,_=evaluate(model,development,batch_size=args.batch_size,thresholds=saved['thresholds'],auxiliary=config['auxiliary'])
            platt=fit_probability_calibration(panel['calibration']['action'],cp)
            calibrated_cp=calibrated_probability(panel['calibration']['action'],cp,platt)
            probability_thresholds=calibration_thresholds(panel['calibration']['action'],calibrated_cp)
            calibrated_dp=calibrated_probability(panel['development']['action'],dp,platt)
            result=dict(name=name,development_default=natural,development_calibrated=adjusted,thresholds=saved['thresholds'],reload_exact=True,
                probability_calibration=platt,development_probability_calibrated=action_report(panel['development']['action'],calibrated_dp,probability_thresholds),per_day={})
            for day in panel_manifest['folds']['development']:
                evidence=panel_manifest['sessions'][day];mask=(panel['development']['clock']>=evidence['begin_us'])&(panel['development']['clock']<evidence['end_us'])
                if not mask.any():raise ValueError('Admitted development day has no decisions')
                result['per_day'][day]=dict(status='previously_inspected_exploratory' if day in manifest['prior_inspected_development_dates'] else 'new_public_fixed_checkpoint_evaluation',
                    default=action_report(panel['development']['action'][mask],dp[mask]),calibrated=action_report(panel['development']['action'][mask],calibrated_dp[mask],probability_thresholds))
            independent=np.isin(panel['development']['clock']//86_400_000_000,[panel_manifest['sessions'][day]['begin_us']//86_400_000_000 for day in manifest['new_public_development_dates']])
            if independent.any():result['new_public_development']=action_report(panel['development']['action'][independent],calibrated_dp[independent],probability_thresholds)
            results.append(result);logger.log(flatten(result,f'fixed_development/{name}'));write(output/'development.json',results)
            np.savez_compressed(output/f'{name}-probabilities.npz',calibration=cp,development=dp)
            del model;gc.collect();torch.cuda.empty_cache()
        write(output/'complete.json',dict(status='completed',selected=selected,development=results,wandb_url=logger.url,
            elapsed_seconds=time.time()-started,sealed_labels_read=False,workstation_gpu_used=False,scope=manifest['scope']))
        logger.summary['completion_status']='completed'
        for name in ('manifest.json','trials.json','selected-trial.json','development.json','complete.json'):logger.save(str(output/name),base_path=str(output),policy='now')
    finally:logger.finish()
    print('Completed campaign',output,flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
