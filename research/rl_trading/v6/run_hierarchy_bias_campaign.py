"""Fixed laptop experiment: original 1a timing and 1b selection supervision."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
import gc
import json
from pathlib import Path
import subprocess
import time
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from research.rl_trading.v1.common import file_hash,digest
from research.rl_trading.v6.bias_models import LocalWindowTeacher,balance_weights,current_objective
from research.rl_trading.v6.bias_panel import verify_panel,PRICE_DIVERSITY_VERSION
from research.rl_trading.v6.bias_metrics import action_report,binary_report,calibration_thresholds,fit_probability_calibration,calibrated_probability
from research.rl_trading.v6.run_bias_campaign import tensors,forward,evaluate,run_epoch,Trial,write,flatten


def product_logit(timing,eligibility):
    """Stable log-odds of two sigmoid probabilities, without target routing."""
    logp=(F.logsigmoid(timing)+F.logsigmoid(eligibility)).clamp_max(-1e-7)
    return logp-torch.where(logp<-.69314718056,torch.log1p(-logp.exp()),torch.log(-torch.expm1(logp)))


class HierarchyTeacher(LocalWindowTeacher):
    def __init__(self,architecture='tcn',*,width=128):
        super().__init__(architecture,width=width,structured=True)
        self.timing=nn.Linear(width,1);self.eligibility=nn.Linear(width,1)

    def forward(self,windows,present,market,held,**kwargs):
        result=super().forward(windows,present,market,held,**kwargs)
        result['timing_logit']=self.timing(result['context']).flatten()
        result['eligibility_logit']=self.eligibility(result['context']).flatten()
        result['logit']=torch.where(held[:,0]>0,result['logit'],product_logit(result['timing_logit'],result['eligibility_logit']))
        return result


def factor_epoch(model,data,optimizer,*,epoch,batch_size=256,price_scale=None):
    model.train();actions=data['actions_numpy'];w=data['weight'].cpu().numpy();flat=actions<2
    classes=torch.as_tensor(balance_weights(actions,w),device=data['action'].device)
    masks={'episode_selected':flat,'entry_1a':flat & data['episode_selected'].cpu().numpy()}
    extra={name:torch.as_tensor(balance_weights(np.where(data[name].cpu().numpy()[mask],0,1),w[mask]),device=data['action'].device)
        for name,mask in masks.items()}
    denominator=float(w.mean());order=np.random.default_rng(17+epoch).permutation(len(actions));losses=[]
    for start in range(0,len(order),batch_size):
        index=torch.as_tensor(order[start:start+batch_size],device=data['action'].device);result=forward(model,data,index)
        loss=current_objective(result['logit'],data['action'][index],data['weight'][index],classes).mean()/denominator
        mask=data['action'][index]<2
        if mask.any():
            for name,key in (('entry_1a','timing_logit'),('episode_selected','eligibility_logit')):
                selected=mask & data['episode_selected'][index] if name=='entry_1a' else mask
                if not selected.any():continue
                target=data[name][index][selected];weights=data['weight'][index][selected]
                ce=F.binary_cross_entropy_with_logits(result[key][selected],target.float(),reduction='none')
                loss+=.5*(ce*weights*extra[name][(~target).long()]).sum()/weights.sum()
        if price_scale is not None:
            target=data['price_return_bps'][index]/price_scale;valid=data['price_mask'][index]
            forced=model.decode_price(result['context'],forcing=target,forcing_mask=valid)
            regression=(F.smooth_l1_loss(result['price_free'],target,reduction='none')+F.smooth_l1_loss(forced,target,reduction='none'))*.5
            weight=data['weight'][index,None]*valid
            loss+=.1*(regression*weight).sum()/weight.sum().clamp_min(1e-9)
        if not torch.isfinite(loss):raise ValueError('Nonfinite hierarchy objective')
        optimizer.zero_grad(set_to_none=True);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5.);optimizer.step()
        losses.append(float(loss.detach()))
    return dict(loss=float(np.mean(losses)),updates=len(losses),objective='balanced-current + .5 balanced-original-timing + .5 balanced-eligibility',auxiliary_size_quality_future_supervised=False,autoregressive_prices_supervised=price_scale is not None)


@torch.no_grad()
def component_report(model,data,*,batch_size=256):
    model.eval();values={'entry_1a':[],'episode_selected':[]};flat=data['actions_numpy']<2
    for start in range(0,len(data['action']),batch_size):
        index=torch.arange(start,min(start+batch_size,len(data['action'])),device=data['action'].device);result=forward(model,data,index)
        for name,key in (('entry_1a','timing_logit'),('episode_selected','eligibility_logit')):values[name].append(result[key].sigmoid().cpu().numpy())
    return {name:binary_report(data[name].cpu().numpy()[mask],np.concatenate(values[name])[mask])
        for name,mask in {'entry_1a':flat & data['episode_selected'].cpu().numpy(),'episode_selected':flat}.items()}


@torch.no_grad()
def price_report(model,data,scale,*,batch_size=256):
    model.eval();prediction=[]
    for start in range(0,len(data['action']),batch_size):
        index=torch.arange(start,min(start+batch_size,len(data['action'])),device=data['action'].device)
        prediction.append(forward(model,data,index)['price_free'].cpu().numpy()*scale)
    value=np.concatenate(prediction);target=data['price_return_bps'].cpu().numpy();mask=data['price_mask'].cpu().numpy()
    return {str(h):dict(count=int(mask[:,h].sum()),free_running_mae_bps=float(np.abs(value[mask[:,h],h]-target[mask[:,h],h]).mean()),
        persistence_mae_bps=float(np.abs(target[mask[:,h],h]).mean())) for h in range(5)}


def underfit_passes(actions, components=None):
    """Every action and supervised hierarchy branch must memorize TRAIN."""
    reports=[actions[c] for c in ('ENTRY','WAIT','HOLD','EXIT')]
    if components is not None:reports.extend(components.values())
    return all(float(report['f1'])>=.95 for report in reports)


def underfit_gate(make,trial,train,folder,logger,price_scale=None):
    rng=np.random.default_rng(17)
    rows=np.concatenate([rng.choice(np.flatnonzero(train['actions_numpy']==c),32,replace=False) for c in range(4)])
    tiny={**train,'actions_numpy':train['actions_numpy'][rows],'episodes':[train['episodes'][i] for i in rows]}
    for key in ('windows','market','held','action','weight','future','quality','ratio','entry_1a','episode_selected','price_return_bps','price_mask'):
        if key in train:tiny[key]=train[key][rows]
    torch.manual_seed(17);model=make(trial).cuda()
    optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4)
    passed=False
    for epoch in range(1,201):
        fit=factor_epoch(model,tiny,optimizer,epoch=epoch,batch_size=128,price_scale=price_scale) if trial['factor'] else run_epoch(model,tiny,optimizer,Trial('flat',architecture='tcn',structured=True),epoch=epoch,batch_size=128)
        if epoch%10:continue
        actions,probability=evaluate(model,tiny,batch_size=128)
        flat=tiny['actions_numpy']<2
        actions['WAIT']=binary_report(tiny['actions_numpy'][flat]==1,1-probability[flat])
        actions['HOLD']=binary_report(tiny['actions_numpy'][~flat]==2,1-probability[~flat])
        components=component_report(model,tiny,batch_size=128) if trial['factor'] else None
        passed=underfit_passes(actions,components)
        logger.log(flatten(dict(actions=actions,components=components or {}), 'underfit/'+trial['name']))
        write(folder/'underfit.json',dict(passed=passed,epoch=epoch,rows=rows.tolist(),actions=actions,components=components,fit=fit,criterion='all four action F1 and supervised hierarchy F1 >= 0.95 on TRAIN-only 128-row subset'))
        print('underfit',trial['name'],epoch,passed,flush=True)
        if passed:break
    del model,optimizer;gc.collect();torch.cuda.empty_cache()
    return passed


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel',type=Path,required=True);parser.add_argument('--targets',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--epochs',type=int,default=10)
    parser.add_argument('--price-targets',type=Path,help='Optional authenticated dense autoregressive price supervision; changes only the diagnostic')
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();root=args.panel.resolve();targetroot=args.targets.resolve();output=args.output.resolve()
    if not runtime.is_dir() or any(not p.is_relative_to(runtime) for p in (root,targetroot,output)) or output.exists() or not 1<=args.epochs<=20 or not torch.cuda.is_available():raise ValueError('Fresh bounded laptop CUDA runtime required')
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text());targetproof=json.loads((targetroot/'complete.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['manifest_sha256']!=file_hash(root/'manifest.json') or proof['panel_sha256']!=file_hash(root/'panel.pt') or manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Panel authentication failed')
    if targetproof['status']!='prepared' or targetproof['version']!='rl-v6-original-hierarchy-targets-v1' or targetproof['panel_sha256']!=proof['panel_sha256'] or targetproof['targets_sha256']!=file_hash(targetroot/'targets.pt') or targetproof['sealed_labels_read'] is not False or targetproof['observation_contract_changed'] is not False:raise ValueError('Target sidecar authentication failed')
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);targets=torch.load(targetroot/'targets.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest)
    if panel['train']['clock'].max()>=panel['calibration']['clock'].min():raise ValueError('Calibration chronology changed')
    for fold,data in panel.items():
        if any(len(v)!=len(data['action']) for v in targets[fold].values()):raise ValueError('Hierarchy target row count changed')
        flat=data['action']<2
        if not np.array_equal((targets[fold]['entry_1a']&targets[fold]['episode_selected'])[flat],data['action'][flat]==0):raise ValueError('Final 1b ENTRY parity changed')
        if not targets[fold]['episode_selected'][~flat].all() or not np.array_equal(targets[fold]['exit_1a'][~flat],data['action'][~flat]==3):raise ValueError('Held target parity changed')
    price_targets=None;priceproof=None
    if args.price_targets is not None:
        price_root=args.price_targets.resolve()
        if not price_root.is_relative_to(runtime):raise ValueError('Price target root escaped runtime')
        priceproof=json.loads((price_root/'complete.json').read_text())
        if priceproof['status']!='prepared' or priceproof['version']!='rl-v6-exact-clock-price-forecast-targets-v1' or priceproof['panel_sha256']!=proof['panel_sha256'] or priceproof['targets_sha256']!=file_hash(price_root/'targets.pt') or priceproof['sealed_labels_read'] is not False or priceproof['observation_contract_changed'] is not False:raise ValueError('Price target authentication failed')
        price_targets=torch.load(price_root/'targets.pt',map_location='cpu',weights_only=False)
        for fold,data in panel.items():
            price=price_targets[fold];valid=price['mask']
            if price['return_bps'].shape!=(len(data['action']),5) or valid.shape!=price['return_bps'].shape or not np.isfinite(price['return_bps']).all() or (price['target_close_us'][valid]<np.broadcast_to(data['clock'][:,None],valid.shape)[valid]).any():raise ValueError('Invalid exact-clock price targets')
        train_price=price_targets['train'];scale=max(1.,float(np.quantile(np.abs(train_price['return_bps'][train_price['mask']]),.9)))
        if scale!=priceproof['train_only_scale_bps']:raise ValueError('TRAIN-only price scaling changed')
    torch.set_num_threads(4);torch.use_deterministic_algorithms(True);output.mkdir();started=time.time()
    trials=[dict(name='tcn_flat_control',architecture='tcn',factor=False),dict(name='tcn_hierarchy',architecture='tcn',factor=True),dict(name='gru_hierarchy',architecture='gru',factor=True)]
    if price_targets is not None:
        trials=[dict(name='tcn_hierarchy_control',architecture='tcn',factor=True,price=False),dict(name='tcn_price_ar',architecture='tcn',factor=True,price=True),dict(name='gru_price_ar',architecture='gru',factor=True,price=True)]
    plan=dict(version='rl-v6-hierarchy-bias-diagnostic-v1',trials=trials,epochs=args.epochs,seed=17,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_sha256=file_hash(Path(__file__)),panel_sha256=proof['panel_sha256'],targets_sha256=targetproof['targets_sha256'],folds=manifest['folds'],
        model_scope='19-ticker-local-diagnostic-not-production-market-policy',development_status='previously_inspected_exploratory',sealed_labels_read=False,workstation_gpu_used=False)
    if priceproof is not None:plan.update(price_targets_sha256=priceproof['targets_sha256'],price_scale_bps=scale,price_loss_weight=.1,price_training='equal free-running and teacher-forced loss; current action never receives forcing')
    write(output/'manifest.json',plan)
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    import wandb
    logger=wandb.init(project='rl-trading-v6',name=output.name,mode='online',dir=str(output),config=plan);write(output/'wandb.json',dict(url=logger.url,id=logger.id))
    def prepared(fold):
        result=tensors(panel[fold],manifest['normalization'],'cuda');result.update({k:torch.as_tensor(v,device='cuda') for k,v in targets[fold].items()})
        if price_targets is not None:result.update(price_return_bps=torch.as_tensor(price_targets[fold]['return_bps'],device='cuda'),price_mask=torch.as_tensor(price_targets[fold]['mask'],device='cuda'))
        return result
    train=prepared('train');calibration=prepared('calibration');summaries=[]
    def make(trial):
        if trial.get('price'):
            from research.rl_trading.v6.price_forecast_model import PriceForecastTeacher
            return PriceForecastTeacher(trial['architecture'])
        return HierarchyTeacher(trial['architecture']) if trial['factor'] else LocalWindowTeacher('tcn',structured=True)
    try:
        for trial in trials:
            folder=output/trial['name'];folder.mkdir()
            if not underfit_gate(make,trial,train,folder,logger,scale if trial.get('price') else None):
                write(output/'progress.json',dict(phase='underfit_failed',trial=trial['name'],generalization_run=False,bias_fixed=False))
                raise RuntimeError('Candidate failed TRAIN-only underfit gate; no generalization permitted')
            torch.manual_seed(17);model=make(trial).cuda();optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4);best=-1
            for epoch in range(1,args.epochs+1):
                fit=factor_epoch(model,train,optimizer,epoch=epoch,price_scale=scale if trial.get('price') else None) if trial['factor'] else run_epoch(model,train,optimizer,Trial('flat',architecture='tcn',structured=True),epoch=epoch)
                metrics,probability=evaluate(model,calibration);score=float(np.mean([metrics[c]['average_precision'] or 0 for c in ('ENTRY','EXIT')]))
                record=dict(epoch=epoch,fit=fit,calibration=metrics)
                if trial['factor']:record['hierarchy_components']=component_report(model,calibration)
                if trial.get('price'):record['price_forecasts']=price_report(model,calibration,scale)
                logger.log(flatten(record,trial['name']));write(output/'progress.json',dict(phase='training',trial=trial['name'],epoch=epoch,completed=len(summaries)))
                if score>best:
                    best=score;torch.save(dict(model=model.state_dict(),trial=trial,epoch=epoch),folder/'selected.pt');write(folder/'selection.json',dict(score=score,calibration=metrics,epoch=epoch))
                print(trial['name'],epoch,'calAP',*[round(metrics[c]['average_precision'] or 0,3) for c in ('ENTRY','EXIT')],flush=True)
            summaries.append(dict(trial=trial,selection=json.loads((folder/'selection.json').read_text()),checkpoint_sha256=file_hash(folder/'selected.pt')))
            del model,optimizer;gc.collect();torch.cuda.empty_cache()
        write(output/'selected-trial.json',dict(**max(summaries,key=lambda x:x['selection']['score']),selected_before_development=True));development=prepared('development');results=[]
        for summary in summaries:
            trial=summary['trial'];path=output/trial['name']/'selected.pt'
            if file_hash(path)!=summary['checkpoint_sha256']:raise ValueError('Selected checkpoint changed')
            saved=torch.load(path,map_location='cuda',weights_only=False);model=make(trial).cuda();model.load_state_dict(saved['model'],strict=True)
            metrics,cp=evaluate(model,calibration)
            if metrics!=summary['selection']['calibration']:raise ValueError('Checkpoint calibration replay changed')
            raw,dp=evaluate(model,development);platt=fit_probability_calibration(panel['calibration']['action'],cp)
            thresholds=calibration_thresholds(panel['calibration']['action'],calibrated_probability(panel['calibration']['action'],cp,platt))
            result=dict(name=trial['name'],calibration=metrics,development_default=raw,development_probability_calibrated=action_report(panel['development']['action'],calibrated_probability(panel['development']['action'],dp,platt),thresholds),reload_exact=True)
            if trial['factor']:result['hierarchy_components']=component_report(model,development)
            if trial.get('price'):result['price_forecasts']=price_report(model,development,scale)
            results.append(result);write(output/'development.json',results);logger.log(flatten(result,'fixed-development/'+trial['name']))
            np.savez_compressed(output/(trial['name']+'-probabilities.npz'),calibration=cp,development=dp)
            del model;gc.collect();torch.cuda.empty_cache()
        write(output/'complete.json',dict(status='completed',results=results,wandb_url=logger.url,sealed_labels_read=False,workstation_gpu_used=False,elapsed_seconds=time.time()-started));logger.summary['completion_status']='completed'
        write(output/'progress.json',dict(phase='completed_requires_root_review',completed=len(trials),bias_fixed=False))
    finally:logger.finish()
    return 0


if __name__=='__main__':raise SystemExit(main())
