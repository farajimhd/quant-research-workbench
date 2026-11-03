"""Predict market returns on internal training partitions; never reports trading profit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
import json
from pathlib import Path
import subprocess
import shutil
import sys
import time
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import numpy as np
import torch
from research.market_shards.v1.contract import digest,sha,write_json
from research.rl_trading.v7.data import TrainingDataset
from research.rl_trading.v7.stage_data import DEFAULT_DEST
from research.rl_trading.v7.train import save_checkpoint,source_identity
from research.rl_trading.v7.experiments.predictive_model import PredictivePolicy
from research.rl_trading.v7.experiments.predictive_data import PredictiveDataset


def materialize(dataset,split):
    keys=('features','candidate_valid','account','y','target_valid')
    pieces={k:[] for k in keys}
    count=0
    for day,arrays in dataset.iter_days(split):
        for k in keys:pieces[k].append(np.asarray(arrays[k]))
        count+=1
        if count%20==0:print(json.dumps(dict(status='loading',split=split,days=count)),flush=True)
    return {k:np.concatenate(v,axis=0) for k,v in pieces.items()}


def inputs(arrays,ix,device):
    x={k:torch.as_tensor(arrays[k][ix],device=device) for k in ('features','candidate_valid','account')}
    x['holding']=torch.zeros((len(ix),8),device=device)
    return x


def evaluate(model,arrays,device,batch):
    predictions=[]
    model.eval()
    with torch.no_grad():
        for begin in range(0,len(arrays['features']),batch):
            ix=np.arange(begin,min(begin+batch,len(arrays['features'])))
            predictions.append(model.predict(**inputs(arrays,ix,device)).cpu().numpy())
    p=np.concatenate(predictions);y=arrays['y'];mask=arrays['target_valid']
    result={};total_loss=0.;total_zero=0.;total_count=0
    costs=arrays['features'][...,-14:]
    hurdle=costs[...,2]+costs[...,3]+costs[...,4]
    for h,seconds in enumerate((60,300,900)):
        keep=mask[...,h];actual=y[...,h][keep].astype(np.float64);pred=p[...,h][keep].astype(np.float64)
        error=pred-actual;absolute=np.abs(error)
        loss=np.where(absolute<=1,.5*error**2,absolute-.5)
        zero=np.where(np.abs(actual)<=1,.5*actual**2,np.abs(actual)-.5)
        count=len(actual)
        if not count:raise ValueError('Missing development targets')
        projected=(costs[...,9]>0)&(costs[...,10]>0)&(costs[...,11]>0)&(costs[...,1]>0)
        eligible=projected[keep]
        top=eligible & (pred>=np.quantile(pred[eligible],.9)) if eligible.any() else np.zeros(count,bool)
        result[str(seconds)]=dict(count=count,mae=float(absolute.mean()),rmse=float(np.sqrt((error**2).mean())),
            zero_mae=float(np.abs(actual).mean()),zero_huber=float(zero.mean()),correlation=float(np.corrcoef(pred,actual)[0,1]) if pred.std()>1e-12 and actual.std()>1e-12 else 0.,
            cost_projectable_count=int(eligible.sum()),top_decile_count=int(top.sum()),
            top_decile_log_mark_return=float(actual[top].mean()) if top.any() else None,
            top_decile_simple_mark_return=float((100*np.expm1(actual[top]/100)).mean()) if top.any() else None,
            top_decile_current_cost=float(hurdle[keep][top].mean()) if top.any() else None)
        total_loss+=loss.sum();total_zero+=zero.sum();total_count+=count
    return dict(huber=float(total_loss/total_count),zero_huber=float(total_zero/total_count),horizons=result,
                evidence='forecast metrics on last30 of150 TRAINING sessions; not simulated account returns')


def publish_committed(root,state):
    best=root/'epochs'/f"{state['best_epoch']:04d}.pt"
    if not best.exists() or sha(best)!=state['best_checkpoint_sha256']:
        raise ValueError('Committed winning epoch snapshot missing or changed')
    temporary=root/'checkpoint_best.partial'
    shutil.copyfile(best,temporary)
    if sha(temporary)!=state['best_checkpoint_sha256']:raise ValueError('Best snapshot copy mismatch')
    os.replace(temporary,root/'checkpoint_best.pt')
    write_json(root/'metrics.json',state['history'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=DEFAULT_DEST)
    parser.add_argument('--data',default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    parser.add_argument('--output',required=True)
    parser.add_argument('--epochs',type=int,default=8)
    parser.add_argument('--batch',type=int,default=256)
    parser.add_argument('--seed',type=int,default=1917)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv)
    root=Path(args.output).resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Runtime output required')
    if args.epochs<1 or args.batch<1:raise ValueError('Positive bounded training settings required')
    dataset=PredictiveDataset(args.source,args.data)
    base=TrainingDataset(args.source)
    here=Path(__file__).parent
    contract=dict(data=dataset.binding,training_binding=base.binding,fit_days=dataset.fit_days,
                  development_days=dataset.development_days,epochs=args.epochs,batch=args.batch,seed=args.seed,
                  device=args.device,labels='future60/300/900second mark log returns; training-only targets',
                  source={name:sha(here/name) for name in ('pretrain.py','predictive_model.py','predictive_data.py','cost_aware.py')},
                  base_source=source_identity(),
                  selection='minimum internal-development Huber; no canonical validation access')
    contract_hash=digest(contract)
    if (root/'manifest.json').exists():
        old=json.loads((root/'manifest.json').read_text())
        if not args.resume or old['contract_hash']!=contract_hash or digest(old['contract'])!=contract_hash:
            raise ValueError('Exact-contract resume required')
    elif args.resume:raise ValueError('No run to resume')
    root.mkdir(parents=True,exist_ok=True)
    if not (root/'manifest.json').exists():
        write_json(root/'manifest.json',dict(contract=contract,contract_hash=contract_hash,
                   git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    write_json(root/'status.json',dict(status='loading-training-partitions',validation_opened=False))
    fit=materialize(dataset,'fit');development=materialize(dataset,'development')
    torch.set_num_threads(1);torch.manual_seed(args.seed);torch.cuda.manual_seed_all(args.seed)
    torch.use_deterministic_algorithms(True)
    rng=np.random.default_rng(args.seed)
    model=PredictivePolicy().to(args.device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-3)
    history=[];start_epoch=0;best=float('inf');best_epoch=0
    if args.resume:
        saved=torch.load(root/'checkpoint_latest.pt',map_location=args.device,weights_only=False)
        if saved['contract_hash']!=contract_hash or digest(saved['contract'])!=contract_hash:raise ValueError('Checkpoint mismatch')
        model.load_state_dict(saved['policy']);optimizer.load_state_dict(saved['optimizer'])
        history=saved['history'];start_epoch=saved['epoch'];best=saved['best'];best_epoch=saved['best_epoch']
        torch.set_rng_state(saved['torch_rng'].cpu());torch.cuda.set_rng_state_all([v.cpu() for v in saved['cuda_rng']])
        rng.bit_generator.state=saved['numpy_rng']
        publish_committed(root,saved)
    n=len(fit['features']);started=time.monotonic()
    for epoch in range(start_epoch,args.epochs):
        model.train();order=rng.permutation(n);total=0.;count=0;skipped=0
        for begin in range(0,n,args.batch):
            ix=order[begin:begin+args.batch]
            mask=torch.as_tensor(fit['target_valid'][ix],device=args.device)
            valid=int(mask.sum())
            if not valid:skipped+=1;continue
            target=torch.as_tensor(fit['y'][ix],device=args.device)
            prediction=model.predict(**inputs(fit,ix,args.device))
            loss=torch.nn.functional.smooth_l1_loss(prediction[mask],target[mask])
            if not torch.isfinite(loss):raise ValueError('Nonfinite forecast objective')
            optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
            total+=float(loss.detach())*valid;count+=valid
            if begin%(args.batch*40)==0:
                status=dict(status='pretraining',epoch=epoch+1,epochs=args.epochs,completed_snapshots=begin+len(ix),
                            total_snapshots=n,skipped_batches=skipped,elapsed_seconds=time.monotonic()-started)
                write_json(root/'status.json',status);print(json.dumps(status),flush=True)
        metrics=evaluate(model,development,args.device,args.batch)
        record=dict(epoch=epoch+1,fit_huber=total/count,development=metrics,skipped_batches=skipped)
        history.append(record);improved=metrics['huber']<best;best=min(best,metrics['huber'])
        if improved:best_epoch=epoch+1
        state=dict(contract=contract,contract_hash=contract_hash,policy=model.state_dict(),optimizer=optimizer.state_dict(),
                   epoch=epoch+1,best=best,best_epoch=best_epoch,history=history,torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),
                   numpy_rng=rng.bit_generator.state)
        epoch_path=root/'epochs'/f'{epoch+1:04d}.pt';epoch_path.parent.mkdir(exist_ok=True)
        if epoch_path.exists():
            archive=root/'uncommitted';archive.mkdir(exist_ok=True)
            epoch_path.replace(archive/f'{epoch+1:04d}-{time.time_ns()}.pt')
        save_checkpoint(epoch_path,state)
        state['best_checkpoint_sha256']=sha(root/'epochs'/f'{best_epoch:04d}.pt')
        save_checkpoint(root/'checkpoint_latest.pt',state)
        publish_committed(root,state);print(json.dumps(record),flush=True)
        if (root/'STOP').exists():
            write_json(root/'status.json',dict(status='stopped',completed_epochs=epoch+1));return 2
    write_json(root/'status.json',dict(status='complete',completed_epochs=args.epochs,best_development_huber=best,
                                     evidence='supervised forecast pretraining, not trading qualification'))
    return 0


if __name__=='__main__':raise SystemExit(main())
