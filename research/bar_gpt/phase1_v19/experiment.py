"""Matched, bounded activity-loss experiments with a fixed Phase0 teacher."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
import argparse
import copy
import gc
import json
import time
from pathlib import Path
from types import SimpleNamespace
import torch
import wandb
from .objectives import objective, ARMS, calibrate
from ..phase1_v17.metrics import ActivityMetrics, scalars as activity_scalars
from ..phase1_v17.sampling import FrameSampler
from ..phase1_v16.model import Autoencoder
from ..phase1_v12.model import Config, positions
from ..phase1_v12.metrics import Metrics, scalars
from ..phase1_v12.data import load_teacher
from ..phase1_v15.experiment import cache
from ..phase1_v11.data import verify
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import lease,load_checkpoint
from ..v5.shards.phase1_contract import file_hash, write_json
from research.mlops.env import discover_env_files, load_env_files
from .stress import load_cases,evaluate as stress_evaluate


@torch.no_grad()
def evaluate(model, teacher, data):
    model.eval(); started=time.perf_counter()
    native=Metrics(); activity={k:ActivityMetrics() for k in ('masked','all_visible','teacher')}
    generator=torch.Generator(device='cuda').manual_seed(917)
    for i in range(0,len(data[0]),512):
        z,meta,valid,x,v,frames,raw=[a[i:i+512] for a in data]
        pos=positions(len(z),'cuda',16,generator)
        masked=model(z,meta,pos); visible=model(z,meta,positions(len(z),'cuda',64))
        native.add(model,teacher,SimpleNamespace(grid=raw,frames=frames),(z,meta,valid,x,v,masked,visible),pos)
        hidden=~torch.zeros_like(valid).scatter(1,pos,True)
        for name,pred,mask in (('masked',model.denormalize(masked),v&hidden[...,None]),('all_visible',model.denormalize(visible),v),('teacher',z,v)):
            activity[name].add(teacher.decode(pred)['logs'],raw,mask,frames)
    detailed=native.result(); result=scalars(detailed)
    for name,metric in activity.items():
        r=metric.result();detailed['activity_'+name]=r
        result.update({'activity/'+name+'/'+k:v for k,v in activity_scalars(r).items()})
    torch.cuda.synchronize();result['evaluation_seconds']=time.perf_counter()-started
    if result['nonfinite']:raise FloatingPointError('Nonfinite raw reconstruction')
    model.train();return detailed,result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--revision',required=True);p.add_argument('--steps',type=int,default=6000)
    p.add_argument('--arms',default=','.join(ARMS));p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--stress-cases',type=Path)
    p.add_argument('--batch',type=int,default=1024)
    p.add_argument('--balanced-frames',action='store_true')
    a=p.parse_args();arms=a.arms.split(',')
    if a.output.exists() or len(arms)>4 or len(set(arms))!=len(arms) or any(x not in ARMS for x in arms):raise ValueError('New identity and valid arms required')
    if not 1<=a.steps<=15000 or not 1<=a.batch<=4096 or not 1e-6<=a.lr<=1e-4:raise ValueError('Invalid bounds')
    receipt=json.loads(a.checkpoint.with_suffix('.proof.json').read_text()) if a.checkpoint.with_suffix('.proof.json').exists() else json.loads((a.checkpoint.parent/'result.json').read_text())
    receipt['checkpoint_sha']=receipt.get('checkpoint_sha',receipt.get('sha256'))
    if file_hash(a.checkpoint)!=receipt['checkpoint_sha']:raise ValueError('Parent changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    if a.checkpoint.with_suffix('.proof.json').exists():saved=load_checkpoint(a.checkpoint,saved['contract'])
    if saved['contract']['arm']!='scaled_slots_price':raise ValueError('Expected V16 positional price model')
    root=Path('D:/TradingML/runtimes/bar_gpt');calpath=root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json'
    cal=json.loads(calpath.read_text());dataset=root/'phase1-v8-learned-mae-20261117/dataset'
    verified=verify(dataset)
    if verified['hash']!=cal['data_hash'] or cal['split_hash']!=saved['contract']['split_hash']:raise ValueError('Data contract changed')
    a.output.mkdir(parents=True)
    import psutil,sys
    write_json(a.output/'process.json',dict(pid=os.getpid(),created=psutil.Process().create_time(),command=sys.argv))
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        start=time.perf_counter();teacher,_=load_teacher(Path(cal['teacher_path']),cal['split_hash'],'cuda')
        train=cache(dataset/'train.npy',teacher);dev=cache(dataset/'development.npy',teacher)
        activity_scale,activity_calibration=calibrate(train[6],train[4],train[5])
        stress=load_cases(a.stress_cases) if a.stress_cases else None
        sampler=FrameSampler(train[5]) if a.balanced_frames else None
        contract=dict(source=a.revision,parent=str(a.checkpoint),parent_sha=receipt['checkpoint_sha'],data_hash=verified['hash'],
                      split_hash=cal['split_hash'],calibration_sha=file_hash(calpath),teacher_sha=file_hash(Path(cal['teacher_path'])),
                      config=saved['contract']['config'],arm='scaled_slots_price',arms=arms,steps=a.steps,batch=a.batch,lr=a.lr,
                      seed=559,cache_seconds=time.perf_counter()-start,train_windows=len(train[0]),dev_windows=len(dev[0]),
                      precision='BF16 stem/FF; FP32 stable attention, readout, loss',embedding='256 learned values only',
                      masking='16 random visible bars in 3/4 batches; all64 in 1/4',
                      weighting='uniform training windows; original coordinate losses weight visible1 hidden0.1; new relative penalty weights all valid bars equally; metrics unweighted',
                      deadline_seconds=3300,scope='Bounded subset; no full training',fresh_optimizer=True)
        if sampler:
            contract['weighting']='Uniform six-frame draw then uniform window within frame: probability1/(6*frame_count). Calibration unchanged; metrics unweighted.'
            contract['frame_counts']=sampler.counts.cpu().tolist()
        contract['activity_calibration']=activity_calibration
        contract['hypotheses']={'control':'Matched continuation of V18; no inverse-aware term',
            'relative01':'Add0.1 native relative Huber to control exponential overshoot tails',
            'relative1':'Tenfold stronger inverse-aware penalty to test loss competition',
            'unscaled_relative01':'Matched relative01 loss, remove64/N positional-slot amplification'}
        contract['validation_boundary']='Development/audit/forensic examples never enter training or calibration'
        contract['diagnostic_stress_sha']=file_hash(a.stress_cases) if a.stress_cases else None
        write_json(a.output/'manifest.json',contract)
        results=[]
        for arm in arms:
            began=time.perf_counter();out=a.output/arm;out.mkdir();torch.manual_seed(17)
            model=Autoencoder(Config(**contract['config']),cal['mean'],cal['scale'],'scaled_slots_price').cuda()
            model.load_state_dict(saved['model']);opt=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01,fused=True)
            model.encoder.count_scaled=arm!='unscaled_relative01'
            generator=torch.Generator(device='cuda').manual_seed(559)
            arm_contract=dict(contract,loss_arm=arm,encoder_count_scaled=model.encoder.count_scaled)
            w=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v19-tail-repair',name=a.output.name+'-'+arm,dir=str(out),config=arm_contract,reinit=True)
            status='failed';step=0;curve=[];training_seconds=0.
            def save(path):
                torch.save(dict(model=model.state_dict(),optimizer=opt.state_dict(),contract=arm_contract,step=step,
                                rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),sampling_rng=generator.get_state()),path)
            try:
                # Training-only easy case, cloned weights. Never modifies experiment parent.
                tiny=copy.deepcopy(model);tiny_opt=torch.optim.AdamW(tiny.parameters(),lr=1e-4)
                tiny_curve=[];z,meta,valid,_,v,frames,raw=[x[:1] for x in train];pos=positions(1,'cuda',64)
                for t in range(201):
                    loss,_=objective(tiny,teacher,tiny(z,meta,pos),z,valid,pos,raw,v,arm,frames,activity_scale)
                    if t in (0,200):tiny_curve.append(float(loss.detach()))
                    if t<200:tiny_opt.zero_grad(set_to_none=True);loss.backward();stable_clip(tiny.parameters());tiny_opt.step()
                write_json(out/'tiny.json',dict(loss=tiny_curve))
                if not tiny_curve[1]<tiny_curve[0] or not torch.isfinite(loss):raise ValueError('Tiny fit gate failed')
                del tiny,tiny_opt
                for step in range(a.steps+1):
                    if time.perf_counter()-began>3000 or (a.output/'STOP').exists():raise InterruptedError('Deadline/human STOP')
                    if step:
                        tick=time.perf_counter();ids=sampler.sample(a.batch,generator) if sampler else torch.randint(len(train[0]),(a.batch,),device='cuda',generator=generator)
                        z,meta,valid,_,v,frames,raw=[x[ids] for x in train];pos=positions(len(z),'cuda',64 if step%4==0 else 16,generator)
                        opt.zero_grad(set_to_none=True)
                        loss,parts=objective(model,teacher,model(z,meta,pos),z,valid,pos,raw,v,arm,frames,activity_scale)
                        loss.backward();norm=stable_clip(model.parameters())
                        if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Loss/gradient')
                        opt.step();torch.cuda.synchronize();training_seconds+=time.perf_counter()-tick
                        if step%100==0:
                            speed=step*a.batch/training_seconds
                            w.log(dict({'train/loss':float(loss.detach()),'train/cached_windows_per_second':speed,'train/gradient_norm':float(norm)},**{'train/'+k:float(v.detach()) for k,v in parts.items()}),step=step)
                            write_json(out/'progress.json',dict(step=step,steps=a.steps,seconds=time.perf_counter()-began,speed=speed,loss=float(loss.detach())))
                    if step%1000==0 or step==a.steps:
                        detailed,measured=evaluate(model,teacher,dev)
                        if stress is not None:measured.update(stress_evaluate(model,teacher,stress))
                        write_json(out/f'evaluation-{step}.json',detailed);write_json(out/'latest-metrics.json',measured)
                        curve.append(dict(step=step,metrics=measured));w.log({'development/'+k:v for k,v in measured.items()},step=step)
                        print(arm,step,{k:measured[k] for k in ('all_visible/candle/1s/trade/moving_direction_pct','all_visible/raw_mae/1s/trade_close','activity/all_visible/1s/trade_volume/moving_direction_pct','activity/all_visible/1s/trade_volume/raw_mae')},flush=True)
                save(out/'checkpoint.pt')
                result=dict(name=arm,seconds=time.perf_counter()-began,checkpoint_sha=file_hash(out/'checkpoint.pt'),wandb_url=w.url,
                            cached_windows_per_second=a.steps*a.batch/training_seconds,peak_gpu_bytes=torch.cuda.max_memory_allocated(),curve=curve)
                write_json(out/'result.json',result);results.append(result);write_json(a.output/'comparison.json',results);status='complete'
            except BaseException:
                save(out/'checkpoint_partial.pt');raise
            finally:
                w.summary['status']=status;w.finish(exit_code=0 if status=='complete' else 1)
                del model,opt;gc.collect();torch.cuda.empty_cache()


if __name__=='__main__':main()
