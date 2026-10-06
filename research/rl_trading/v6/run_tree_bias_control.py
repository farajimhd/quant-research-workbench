"""Laptop CUDA tree diagnostic on authenticated strict-prior V6 windows.

This is a signal diagnostic, not a replacement for the autoregressive policy.
No labels, current candles, future targets or listing identities enter features.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import gc
import json
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
import torch
from research.rl_trading.v1.common import file_hash,digest
from research.rl_trading.v6.bias_panel import verify_panel,PRICE_DIVERSITY_VERSION
from research.rl_trading.v6.bias_metrics import action_report,calibration_thresholds,fit_probability_calibration,calibrated_probability
from research.rl_trading.v6.run_bias_campaign import flatten


def summary_features(data,*,batch_size=256):
    """Only prior windows, masked summaries and supplied causal market/state."""
    indices=data['windows'];source=data['features']
    if indices.ndim!=2 or indices.shape[1]!=120 or source.shape[1]!=147:
        raise ValueError('Expected strict-prior 120 by 147 contract')
    if (indices<0).any() or indices.max()>=len(source):raise ValueError('Window escaped source')
    if not np.array_equal(data['held'][:,0]>0,data['action']>=2):raise ValueError('Known state/action branch mismatch')
    result=np.empty((len(indices),1336),np.float32)
    for start in range(0,len(indices),batch_size):
        rows=indices[start:start+batch_size];valid=rows!=0;x=source[rows].copy()
        # Price anchor is strictly prior; OHLC-relative bps already stationary.
        count=valid.sum(1);last=np.where(valid,np.arange(120),-1).max(1)
        anchor=x[np.arange(len(x)),last.clip(0),3].copy()
        x[:,:,3]=np.where(valid,(x[:,:,3]-anchor[:,None])*10,0)
        x*=valid[:,:,None]
        latest=x[np.arange(len(x)),last.clip(0)]
        first=np.where(valid,np.arange(120),120).min(1).clip(0,119)
        values=[latest,latest-x[np.arange(len(x)),first]]
        for length in (5,20,120):
            mask=valid[:,-length:];part=x[:,-length:];n=mask.sum(1).clip(1)[:,None]
            mean=part.sum(1)/n
            variance=(np.square(part-mean[:,None])*mask[:,:,None]).sum(1)/n
            values.extend((mean,np.sqrt(variance)))
        market=data['market'][start:start+len(x)].copy();market[:,3]=0
        held=data['held'][start:start+len(x)].copy()
        held[:,1]=np.where(held[:,0]>0,(np.log(held[:,1].clip(1e-6))-anchor)*10,0)
        values.extend((market,held,count[:,None],(count/120)[:,None]))
        result[start:start+len(x)]=np.concatenate(values,axis=1)
    if not np.isfinite(result).all():raise ValueError('Nonfinite prior summary')
    return result


def branch_weights(actions,weights,*,balanced):
    y=(actions==0)|(actions==3);w=np.asarray(weights,np.float64).copy()
    if not np.isfinite(w).all() or (w<=0).any() or not y.any() or y.all():raise ValueError('Two supported TRAIN classes required')
    if balanced:
        # Compute both masses from the original weights, never sequential masses.
        original=np.asarray(weights,np.float64)
        w=np.where(y,original*original.sum()/(2*original[y].sum()),original*original.sum()/(2*original[~y].sum()))
    return (w/w.mean()).astype(np.float32)


def write(path,value):path.write_text(json.dumps(value,indent=2))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dependencies',type=Path,required=True)
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    panelroot=args.panel.resolve();output=args.output.resolve();dependencies=args.dependencies.resolve()
    if not runtime.is_dir() or any(not p.is_relative_to(runtime) for p in (panelroot,output,dependencies)) or output.exists():raise ValueError('Fresh laptop runtime required')
    proof=json.loads((panelroot/'complete.json').read_text());manifest=json.loads((panelroot/'manifest.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['panel_sha256']!=file_hash(panelroot/'panel.pt') or proof['manifest_sha256']!=file_hash(panelroot/'manifest.json'):raise ValueError('Authenticated diversity panel required')
    if manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Panel manifest changed')
    panel=torch.load(panelroot/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest)
    if panel['train']['clock'].max()>=panel['calibration']['clock'].min():raise ValueError('Calibration chronology changed')
    sys.path.insert(0,str(dependencies));import xgboost as xgb
    if xgb.__version__!='3.1.3' or not xgb.build_info().get('USE_CUDA'):raise ValueError('Pinned CUDA XGBoost required')
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    import wandb
    output.mkdir();started=time.time()
    plan=dict(version='rl-v6-causal-tree-signal-diagnostic-v1',source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_sha256=file_hash(Path(__file__)),panel_sha256=proof['panel_sha256'],folds=manifest['folds'],
        feature_contract='strict-prior-120: last/delta/mean/std5,20,120; causal-market; known-held-state; no-identities',
        development_status='previously_inspected_exploratory',xgboost_version=xgb.__version__,
        trials=[dict(name='natural_depth4',depth=4,balanced=False),dict(name='balanced_depth4',depth=4,balanced=True),dict(name='balanced_depth6',depth=6,balanced=True)],
        max_rounds=400,early_stopping_rounds=30,selection='calibration-only XGBoost aucpr early stopping; exact AP reported separately',sealed_labels_read=False,workstation_gpu_used=False)
    write(output/'manifest.json',plan)
    logger=wandb.init(project='rl-trading-v6',name=output.name,mode='online',dir=str(output),config=plan)
    write(output/'wandb.json',dict(url=logger.url,id=logger.id))
    try:
        matrices={fold:summary_features(data) for fold,data in panel.items()};results=[]
        for trial in plan['trials']:
            probability={fold:np.empty(len(data['action']),np.float32) for fold,data in panel.items()};models=[]
            for branch,positive in ((False,0),(True,3)):
                masks={fold:data['held'][:,0]>0 if branch else data['held'][:,0]==0 for fold,data in panel.items()}
                train=xgb.DMatrix(matrices['train'][masks['train']],label=panel['train']['action'][masks['train']]==positive,
                    weight=branch_weights(panel['train']['action'][masks['train']],panel['train']['weight'][masks['train']],balanced=trial['balanced']))
                calibration=xgb.DMatrix(matrices['calibration'][masks['calibration']],label=panel['calibration']['action'][masks['calibration']]==positive)
                model=xgb.train(dict(objective='binary:logistic',eval_metric='aucpr',device='cuda',tree_method='hist',max_depth=trial['depth'],
                    eta=.05,min_child_weight=10,subsample=.8,colsample_bytree=.8,reg_lambda=10,seed=17,nthread=4),train,num_boost_round=400,
                    evals=[(calibration,'calibration')],early_stopping_rounds=30,verbose_eval=50)
                if not json.loads(model.save_config())['learner']['generic_param']['device'].startswith('cuda'):raise ValueError('CPU fallback forbidden')
                path=output/(trial['name']+('-exit.json' if branch else '-entry.json'));model.save_model(path)
                reload=xgb.Booster();reload.load_model(path);reload.set_param(dict(device='cuda',nthread=4));rounds=(0,model.best_iteration+1)
                for fold in panel:
                    dm=xgb.DMatrix(matrices[fold][masks[fold]]);p=model.predict(dm,iteration_range=rounds)
                    if not np.array_equal(p,reload.predict(dm,iteration_range=rounds)):raise ValueError('Checkpoint reload changed predictions')
                    probability[fold][masks[fold]]=p
                models.append(dict(branch='EXIT' if branch else 'ENTRY',best_iteration=model.best_iteration,sha256=file_hash(path)))
                del train,calibration,model,reload;gc.collect()
            actions=panel['calibration']['action'];cp=probability['calibration']
            thresholds=calibration_thresholds(actions,cp);platt=fit_probability_calibration(actions,cp)
            calibrated_cp=calibrated_probability(actions,cp,platt);calibrated_thresholds=calibration_thresholds(actions,calibrated_cp)
            result=dict(name=trial['name'],checkpoints=models,thresholds=thresholds,probability_thresholds=calibrated_thresholds,
                calibration=action_report(actions,cp),probability_calibration=platt,reload_exact=True,
                development=action_report(panel['development']['action'],probability['development']),
                development_probability_calibrated=action_report(panel['development']['action'],calibrated_probability(panel['development']['action'],probability['development'],platt),calibrated_thresholds))
            results.append(result);logger.log(flatten(result,'diagnostic/'+trial['name']));write(output/'results.json',results)
            np.savez_compressed(output/(trial['name']+'-probabilities.npz'),**probability)
            print('Completed diagnostic',trial['name'],flush=True)
        write(output/'complete.json',dict(status='completed',results=results,wandb_url=logger.url,sealed_labels_read=False,workstation_gpu_used=False,elapsed_seconds=time.time()-started))
        logger.summary['completion_status']='completed'
    finally:logger.finish()
    return 0


if __name__=='__main__':raise SystemExit(main())
