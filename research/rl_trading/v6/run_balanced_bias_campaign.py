"""Laptop-only fixed four-class minibatch control, selected on calibration.

Class quotas replace inverse-frequency loss weights; applying both would double
balance the objective. Within each class, TRAIN sample weights retain the
original episode weighting. No development or sealed target affects sampling.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse,gc,json,subprocess,time
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch
from torch.nn import functional as F
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.bias_panel import verify_panel,normalization
from research.rl_trading.v6.bias_models import LocalWindowTeacher
from research.rl_trading.v6.run_bias_campaign import Trial,tensors,reserve_calibration,forward,evaluate,write,flatten
from research.rl_trading.v6.bias_metrics import calibration_thresholds

VERSION='rl-v6-bias-balanced-batches-v1'
TRIALS=(Trial('lag_balanced_batches',balance='natural'),
        Trial('tcn_structured_balanced_batches',architecture='tcn',structured=True,balance='natural'))

def batches(actions,weights,*,batch_size=256,seed=17):
    actions=np.asarray(actions);weights=np.asarray(weights,dtype=np.float64)
    if (actions.ndim!=1 or weights.shape!=actions.shape or not np.isin(actions,[0,1,2,3]).all()
        or not np.isfinite(weights).all() or (weights<=0).any() or batch_size<4 or batch_size%4):
        raise ValueError('Known actions, positive TRAIN weights and four equal quotas required')
    groups=[np.flatnonzero(actions==c) for c in range(4)]
    if any(not len(group) for group in groups):raise ValueError('All four TRAIN classes required')
    rng=np.random.default_rng(seed)
    masses=np.array([weights[group].sum() for group in groups])
    if not np.isfinite(masses).all():raise ValueError('Finite weighted class masses required')
    probability=[weights[group]/mass for group,mass in zip(groups,masses)]
    for _ in range((len(actions)+batch_size-1)//batch_size):
        chosen=np.concatenate([rng.choice(group,batch_size//4,replace=True,p=p) for group,p in zip(groups,probability)])
        rng.shuffle(chosen)
        yield chosen

def run_epoch(model,data,optimizer,*,epoch,batch_size=256):
    model.train();losses=[];counts=np.zeros(4,np.int64)
    actions=data['actions_numpy'];weights=data['weight'].cpu().numpy()
    for chosen in batches(actions,weights,batch_size=batch_size,seed=17+epoch):
        index=torch.as_tensor(chosen,device=data['action'].device)
        prediction=forward(model,data,index)['logit']
        targets=((data['action'][index]==0)|(data['action'][index]==3)).float()
        loss=F.binary_cross_entropy_with_logits(prediction,targets)
        if not torch.isfinite(loss):raise ValueError('Nonfinite balanced objective')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not torch.isfinite(norm):raise ValueError('Nonfinite balanced gradient')
        optimizer.step();losses.append(float(loss.detach()))
        counts+=np.bincount(actions[chosen],minlength=4)
    return dict(loss=float(np.mean(losses)),updates=len(losses),sampled_action_counts=counts.tolist(),
                objective='equal_class_risk_with_original_within_class_episode_weights',class_loss_weights=[1.,1.,1.,1.])

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--panel',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--epochs',type=int,default=20)
    args=p.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    root=args.panel.resolve();output=args.output.resolve()
    if not runtime.is_dir() or not all(x.is_relative_to(runtime) for x in (root,output)) or output.exists() or not torch.cuda.is_available() or not 1<=args.epochs<=40:
        raise ValueError('Authenticated laptop panel, fresh CUDA output and bounded epochs required')
    from research.rl_trading.v6.audit_bias_campaign import checked_panel
    proof,source=checked_panel(root)
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,source)
    panel=reserve_calibration(panel,source);norm=normalization(panel['train'])
    torch.set_num_threads(4);device=torch.device('cuda');started=time.time()
    manifest=dict(version=VERSION,panel_sha256=proof['panel_sha256'],panel_manifest_sha256=proof['manifest_sha256'],
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_files_sha256={path.name:file_hash(path) for path in Path(__file__).parent.glob('*.py')},
        trials=[asdict(t) for t in TRIALS],epochs=args.epochs,batch_size=256,normalization=norm,normalization_sha256=digest(norm),
        sampling='64_per_class_with_replacement_original_TRAIN_weights_inside_class',loss_weights='all_one_no_double_balancing',
        auxiliary_supervised=False,selection_fold='calibration_only_training_role_2026-08-04_and_2026-08-21',
        training_dates=['2026-07-31','2026-08-03'],development='not_evaluated_here_use_frozen_audit',
        sealed_labels_read=False,workstation_gpu_used=False,scope=source['scope'],device=torch.cuda.get_device_name(),seed=17)
    manifest['hash']=digest(manifest);output.mkdir();write(output/'manifest.json',manifest)
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    import wandb
    logger=wandb.init(project='rl-trading-v6',name=output.name,mode='online',dir=str(output),config=manifest)
    if logger is None or logger.settings.mode!='online':raise ValueError('Online W&B required')
    write(output/'wandb.json',dict(url=logger.url,id=logger.id,entity=logger.entity,project=logger.project))
    try:
        train=tensors(panel['train'],norm,device);calibration=tensors(panel['calibration'],norm,device);summaries=[]
        for trial in TRIALS:
            torch.manual_seed(17);torch.cuda.manual_seed_all(17)
            model=LocalWindowTeacher(trial.architecture,structured=trial.structured,normalization=norm).to(device)
            optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4)
            folder=output/trial.name;folder.mkdir();best=-1.
            for epoch in range(1,args.epochs+1):
                fit=run_epoch(model,train,optimizer,epoch=epoch)
                trained,_=evaluate(model,train);calibrated,cp=evaluate(model,calibration)
                score=float(np.mean([calibrated[label]['average_precision'] or 0. for label in ('ENTRY','EXIT')]))
                record=dict(epoch=epoch,fit=fit,training=trained,calibration=calibrated,selection_score=score)
                with (folder/'metrics.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(record,allow_nan=False)+'\n')
                logger.log(flatten(record,trial.name));write(output/'progress.json',dict(phase='training',trial=trial.name,epoch=epoch,completed=[s['trial']['name'] for s in summaries]))
                if score>best:
                    best=score;thresholds=calibration_thresholds(panel['calibration']['action'],cp)
                    torch.save(dict(model=model.state_dict(),trial=asdict(trial),epoch=epoch,normalization=norm,manifest_hash=manifest['hash'],thresholds=thresholds),folder/'selected.pt')
                    write(folder/'selection.json',dict(epoch=epoch,score=score,thresholds=thresholds,calibration=calibrated))
                print(trial.name,epoch,'TRAIN F1',*[round(trained[c]['f1'],3) for c in ('ENTRY','EXIT')],'cal AP',*[round(calibrated[c]['average_precision'] or 0.,3) for c in ('ENTRY','EXIT')],flush=True)
            summaries.append(dict(trial=asdict(trial),selection=json.loads((folder/'selection.json').read_text()),checkpoint_sha256=file_hash(folder/'selected.pt')))
            write(output/'trials.json',summaries);del model,optimizer;gc.collect();torch.cuda.empty_cache()
        selected=max(summaries,key=lambda s:s['selection']['score'])
        write(output/'selected-trial.json',dict(**selected,selected_before_development=True))
        write(output/'complete.json',dict(status='completed',selected=selected,development=[],wandb_url=logger.url,
            elapsed_seconds=time.time()-started,sealed_labels_read=False,workstation_gpu_used=False,scope=manifest['scope']))
        logger.summary['completion_status']='completed'
        for name in ('manifest.json','trials.json','selected-trial.json','complete.json'):logger.save(str(output/name),base_path=str(output),policy='now')
    finally:logger.finish()
    print('Completed fixed balanced minibatch controls; development not evaluated',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
