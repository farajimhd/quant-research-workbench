"""Matched bounded continuations of the pinned epoch19 Phase0 teacher."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,json,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
import wandb
from .model import Autoencoder,ACTIVITY
from ..phase0_v1.model import objective
from ..phase0_v1.metrics import Metrics,scalars
from ..phase1_v17.metrics import ActivityMetrics,scalars as activity_scalars
from ..phase1_v12.data import load_teacher
from ..phase1_v7.full_data import prepare_raw,gpu_inputs
from ..phase1_v11.data import verify
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,digest,write_json
from research.mlops.env import discover_env_files,load_env_files


def fit(rows,reference):
    means=[];scales=[];counts=[];zeros=[];error=0.
    for j,r in zip(ACTIVITY,reference):
        q=np.concatenate([a['values'][...,j][a['valid'][...,j].astype(bool)].astype(np.float64) for a in rows])
        if not np.isfinite(q).all() or (q<0).any():raise ValueError('Invalid activity')
        x=np.log1p(q/r);restored=np.expm1(x)*r
        error=max(error,float(np.max(np.abs(restored-q)/np.maximum(1,q))))
        means.append(float(x.mean()));scales.append(max(float(x.std()),1e-5));counts.append(len(q));zeros.append(int((q==0).sum()))
    if error>1e-12:raise ValueError('Activity roundtrip')
    return dict(reference=reference,mean=means,scale=scales,counts=counts,zeros=zeros,roundtrip_relative_max=error)


def cache(rows):
    parts=[]
    for i in range(0,len(rows),512):
        b=prepare_raw(rows[i:i+512],True).to('cuda');x,v,_=gpu_inputs(b);parts.append((x,v,b.grid,b.frames))
    return tuple(torch.cat([p[j] for p in parts]) for j in range(4))


@torch.no_grad()
def evaluate(model,data):
    started=time.perf_counter();model.eval();metric=Metrics();activity=ActivityMetrics()
    for i in range(0,len(data[0]),512):
        x,v,raw,frames=[p[i:i+512] for p in data];out=model(x,v)
        metric.add(model,out,x,v,SimpleNamespace(grid=raw,frames=frames));activity.add(out['logs'],raw,v,frames)
    detailed=metric.result();detailed['activity']=activity.result();measured=scalars(detailed)
    measured.update({'activity/'+k:v for k,v in activity_scalars(detailed['activity']).items()});measured['seconds']=time.perf_counter()-started
    if measured['nonfinite']:raise FloatingPointError('Nonfinite reconstruction')
    model.train();return detailed,measured


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--steps',type=int,default=4000);p.add_argument('--batch',type=int,default=1024);p.add_argument('--lr',type=float,default=1e-6)
    p.add_argument('--arms',default='control,median,sqrt_median');a=p.parse_args();arms=a.arms.split(',')
    if a.output.exists() or any(x not in ('control','median','sqrt_median') for x in arms) or len(set(arms))!=len(arms):raise ValueError('New identity and valid arms required')
    if not 0<=a.steps<=15000 or not 1<=a.batch<=2048 or not 1e-7<=a.lr<=1e-4:raise ValueError('Experiment bounds')
    a.output.mkdir();torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    import psutil,sys
    write_json(a.output/'process.json',dict(pid=os.getpid(),created=psutil.Process().create_time(),command=sys.argv))
    root=Path('D:/TradingML/runtimes/bar_gpt');dataset=root/'phase1-v8-learned-mae-20261117/dataset';verified=verify(dataset)
    supplement=root/'phase1-v19-training-pool-20261121';sm=json.loads((supplement/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or sm['split_hash']!=verified['source_split_hash']:raise ValueError('Supplement provenance')
    if file_hash(supplement/'train.npy')!=sm['files']['train.npy']:raise ValueError('Supplement hash')
    rows=[np.load(dataset/'train.npy',mmap_mode='r'),np.load(supplement/'train.npy',mmap_mode='r')];start=time.perf_counter()
    references=[]
    for j in ACTIVITY:
        q=np.concatenate([r['values'][...,j][r['valid'][...,j].astype(bool)&(r['values'][...,j]>0)].astype(np.float64) for r in rows]);references.append(float(np.median(q)))
    calibrations={'control':None,'median':fit(rows,references),'sqrt_median':fit(rows,np.sqrt(references).tolist())}
    write_json(a.output/'calibration.json',dict(parameters=calibrations,data_hash=verified['hash'],supplement_hash=sm['hash'],seconds=time.perf_counter()-start,weighting='Uniform valid training bar exposures; positive median selects reference; input mean/std include valid zeros; outputs unchanged'))
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        cache_started=time.perf_counter()
        teacher,identity=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',verified['source_split_hash'],'cuda')
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(rows[0]),cache(rows[1])));dev=cache(np.load(dataset/'development.npy',mmap_mode='r'))
        contract=dict(source=a.revision,parent=identity,data_hash=verified['hash'],supplement_hash=sm['hash'],split_hash=verified['source_split_hash'],config=vars(teacher.config),steps=a.steps,batch=a.batch,lr=a.lr,seed=559,train_windows=len(train[0]),dev_windows=len(dev[0]),scope='Input-only transform ablation;16D embedding and existing decoder/output units/objective unchanged; not compatible with old Phase1 without qualification')
        contract['cache_seconds']=time.perf_counter()-cache_started
        write_json(a.output/'manifest.json',dict(contract,arms=arms));results=[]
        for arm in arms:
            out=a.output/arm;out.mkdir();began=time.perf_counter();torch.manual_seed(17)
            model=Autoencoder(dict(mean=teacher.mean,scale=teacher.scale),teacher.config,calibrations[arm]).cuda();missing,unexpected=model.load_state_dict(teacher.state_dict(),strict=False)
            if unexpected or set(missing)!={'activity_indices','input_reference','activity_input_mean','activity_input_scale'}:raise ValueError('Unexpected transferred state')
            opt=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01,fused=True);g=torch.Generator(device='cuda').manual_seed(559)
            w=wandb.init(entity='mehdifaraji',project='bargpt-phase0-v3-activity-reference',name=a.output.name+'-'+arm,config=dict(contract,arm=arm,input_calibration=calibrations[arm]),dir=str(out),reinit=True)
            step=0;status='failed';curve=[];training_seconds=0.
            def save(path):torch.save(dict(model=model.state_dict(),contract=dict(contract,arm=arm,input_calibration=calibrations[arm]),optimizer=opt.state_dict(),step=step,rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),sampling_rng=g.get_state()),path)
            try:
                tiny=copy.deepcopy(model);tiny_opt=torch.optim.AdamW(tiny.parameters(),lr=1e-5);x,v,_,_=[p[:1,:1] if p.ndim>1 else p[:1] for p in train];tiny_curve=[]
                for t in range(201):
                    loss,_=objective(tiny,tiny(x,v),x,v)
                    if t in (0,200):tiny_curve.append(float(loss.detach()))
                    if t<200:tiny_opt.zero_grad();loss.backward();stable_clip(tiny.parameters());tiny_opt.step()
                write_json(out/'tiny.json',dict(loss=tiny_curve));del tiny,tiny_opt
                if not tiny_curve[1]<tiny_curve[0]:raise ValueError('Tiny-fit failure')
                for step in range(a.steps+1):
                    if time.perf_counter()-began>3000 or (a.output/'STOP').exists():raise InterruptedError('Deadline/STOP')
                    if step:
                        tick=time.perf_counter();ids=torch.randint(len(train[0]),(a.batch,),device='cuda',generator=g);x,v=[p[ids] for p in train[:2]]
                        opt.zero_grad(set_to_none=True);loss,_=objective(model,model(x,v),x,v);loss.backward();norm=stable_clip(model.parameters())
                        if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Loss/gradient')
                        opt.step();torch.cuda.synchronize();training_seconds+=time.perf_counter()-tick
                        if step%100==0:
                            speed=step*a.batch/training_seconds;w.log({'train/loss':float(loss.detach()),'train/windows_per_second':speed,'train/bars_per_second':64*speed},step=step)
                            write_json(out/'progress.json',dict(step=step,steps=a.steps,seconds=time.perf_counter()-began,speed=speed,loss=float(loss.detach())))
                    if step%1000==0 or step==a.steps:
                        detailed,metrics=evaluate(model,dev);write_json(out/f'evaluation-{step}.json',detailed);write_json(out/'latest-metrics.json',metrics);curve.append(dict(step=step,metrics=metrics));w.log({'development/'+k:v for k,v in metrics.items()},step=step)
                        print(arm,step,{k:metrics[k] for k in ('raw_mae/1s/trade_close','candle/1s/trade/moving_direction_pct','range/1s/trade/bin_accuracy_pct','activity/1s/trade_volume/moving_direction_pct')},flush=True)
                save(out/'checkpoint.pt');result=dict(arm=arm,checkpoint_sha=file_hash(out/'checkpoint.pt'),curve=curve,seconds=time.perf_counter()-began,wandb_url=w.url,cached_windows_per_second=step*a.batch/max(training_seconds,.001));write_json(out/'result.json',result);results.append(result);write_json(a.output/'comparison.json',results);status='complete'
            except BaseException:save(out/'checkpoint_partial.pt');raise
            finally:w.summary['status']=status;w.finish(exit_code=0 if status=='complete' else 1)


if __name__=='__main__':main()
