"""Matched bounded single-head hierarchical MAE experiments."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,gc,json,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
import wandb
from .model import initialize,verify_protected
from ..phase1_v20.model import set_gradient_scope
from .objectives import loss
from .step import PARTS,backward,qualified_replay
from ..phase0_v3.experiment import cache
from ..phase0_v1.metrics import Metrics,scalars
from ..phase1_v17.metrics import ActivityMetrics,scalars as activity_scalars
from ..phase1_v19.objectives import calibrate
from ..phase1_v12.model import positions,gather
from ..phase1_v12.data import load_teacher
from ..phase1_v11.data import verify
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,digest,write_json
from research.mlops.env import discover_env_files,load_env_files


@torch.no_grad()
def evaluate(model,teacher,data):
    began=time.perf_counter();model.eval();raw_metrics={k:Metrics() for k in ('masked','all_visible','bar_probe')};activity={k:ActivityMetrics() for k in raw_metrics}
    g=torch.Generator(device='cuda').manual_seed(917);anchor=0.;bars=0
    for i in range(0,len(data[0]),512):
        x,v,raw,frames,old=[p[i:i+512] for p in data];z,meta=model.features(x,v);pos=positions(len(x),'cuda',16,g)
        hidden=~torch.zeros_like(v[...,0]).scatter(1,pos,True)
        outputs={}
        for name,p in (('masked',pos),('all_visible',positions(len(x),'cuda',64))):
            embedding=model.temporal(gather(z,p),gather(meta,p),p);outputs[name]=model.decode(embedding)
        # Fixed legacy probe is evaluation-only; not part of the model or loss.
        outputs['bar_probe']=teacher.decode(z)
        for name,out in outputs.items():
            vm=v&hidden[...,None] if name=='masked' else v
            raw_metrics[name].add(teacher if name=='bar_probe' else model,out,x,vm,SimpleNamespace(grid=raw,frames=frames))
            activity[name].add(out['logs'],raw,vm,frames)
        mask=v[...,:18].any(-1);anchor+=float(torch.where(mask,((z-old)/model.temporal.scale).square().mean(-1),0).sum());bars+=int(mask.sum())
    detail={};measured={}
    for name,metric in raw_metrics.items():
        detail[name]=metric.result();detail['activity_'+name]=activity[name].result()
        measured.update({name+'/'+k:v for k,v in scalars(detail[name]).items()})
        measured.update({'activity/'+name+'/'+k:v for k,v in activity_scalars(detail['activity_'+name]).items()})
    measured.update(evaluation_seconds=time.perf_counter()-began,bar_latent_anchor_mse=anchor/max(1,bars),nonfinite=sum(r['nonfinite'] for k,r in detail.items() if not k.startswith('activity_')))
    if measured['nonfinite']:raise FloatingPointError('Nonfinite validation')
    model.train();return detail,measured


def optimizer(model,lr,bar_lr):
    other=[p for n,p in model.named_parameters() if not n.startswith('bar.')]
    return torch.optim.AdamW([dict(params=model.bar.parameters(),lr=bar_lr),dict(params=other,lr=lr)],weight_decay=0.,fused=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--steps',type=int,default=3000);p.add_argument('--lr',type=float,default=1e-6);p.add_argument('--bar-lr',type=float,default=1e-7)
    p.add_argument('--decoder-visible-only',action='store_true')
    p.add_argument('--graphs',action='store_true')
    p.add_argument('--parent',type=Path)
    p.add_argument('--arms',default='range,both_log,both_symmetric,metric');a=p.parse_args();arms=a.arms.split(',')
    if a.output.exists() or len(set(arms))!=len(arms) or any(k not in ('range','both_log','both_symmetric','metric','metric_log','joint_control','joint_metric_log','joint_repaired','joint_separated','body_direction') for k in arms):raise ValueError('New identity and valid arms required')
    if not 0<=a.steps<=10000 or not 1e-8<=a.bar_lr<=a.lr<=1e-4:raise ValueError('Experiment bounds')
    a.output.mkdir();torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    import psutil,sys
    write_json(a.output/'process.json',dict(pid=os.getpid(),created=psutil.Process().create_time(),command=sys.argv))
    root=Path('D:/TradingML/runtimes/bar_gpt');data=root/'phase1-v8-learned-mae-20261117/dataset';verified=verify(data)
    supplement=root/'phase1-v19-training-pool-20261121';sm=json.loads((supplement/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or sm['split_hash']!=verified['source_split_hash'] or file_hash(supplement/'train.npy')!=sm['files']['train.npy']:raise ValueError('Training supplement provenance')
    cp=a.parent or root/'phase1-v20-joint-singlehead-20261121/joint/checkpoint.pt';receipt=json.loads((cp.parent/'result.json').read_text())
    if file_hash(cp)!=receipt['checkpoint_sha']:raise ValueError('Window parent hash')
    saved=torch.load(cp,map_location='cpu',weights_only=False);cal=json.loads((root/'phase1-v12-frozen16-mae-20261121/teacher/calibration.json').read_text())
    refpath=root/'phase0-v3-input-reference-20261121/calibration.json';refs=json.loads(refpath.read_text())
    if refs['data_hash']!=verified['hash'] or refs['supplement_hash']!=sm['hash']:raise ValueError('Input-reference calibration provenance')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    with lease(a.output):
        start=time.perf_counter();teacher,identity=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',verified['source_split_hash'],'cuda')
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(np.load(data/'train.npy',mmap_mode='r')),cache(np.load(supplement/'train.npy',mmap_mode='r'))))
        dev=cache(np.load(data/'development.npy',mmap_mode='r'))
        def anchors(items):
            with torch.no_grad():z=torch.cat([teacher.encode(items[0][i:i+512],items[1][i:i+512]) for i in range(0,len(items[0]),512)])
            return (*items,z)
        train=anchors(train);dev=anchors(dev);activity_scale,activity_cal=calibrate(train[2],train[1],train[3])
        contract=dict(source=a.revision,bar_parent=identity,window_parent_sha=file_hash(cp),data_hash=verified['hash'],supplement_hash=sm['hash'],split_hash=verified['source_split_hash'],input_calibration_sha=file_hash(refpath),steps=a.steps,lr=a.lr,bar_lr=a.bar_lr,seed=559,cache_seconds=time.perf_counter()-start,train_windows=len(train[0]),dev_windows=len(dev[0]),architecture='Feature attention4x1 to16D; temporal128x2 to512D; oneLinear512to1152 FP32 output head; no intermediate decoder',masking='16 random bars in3/4 batches; all64 in1/4; hidden bars removed before both attention stages',anchor='Head arms freeze encoders; joint arms retain original weight1 teacher-latent anchor',activity_calibration=activity_cal,scope='Bounded real training pool; unchanged development; no full training')
        contract['decoder_visible_only']=a.decoder_visible_only
        contract['graphs']=a.graphs
        contract['intervention']='Head arms freeze encoders; joint arms train both encoders. Head arms freeze and close/body rows; fit existing wick/activity output rows only. No new head. Diagnostic calibration.'
        profiles=[]
        for batch in (256,512,1024,2048,4096,8192):
            torch.manual_seed(17);model=initialize(teacher,saved,cal,arms[0]).cuda();opt=optimizer(model,a.lr,a.bar_lr);g=torch.Generator(device='cuda').manual_seed(559);torch.cuda.reset_peak_memory_stats();began=None;replays={}
            for i in range(12):
                ids=torch.randint(len(train[0]),(batch,),device='cuda',generator=g);x,v,raw,f,z=[q[ids] for q in train];pos=positions(batch,'cuda',64 if i%4==3 else 16,g)
                set_gradient_scope(model,pos.shape[1],a.decoder_visible_only)
                opt.zero_grad(set_to_none=True);inputs=(x,v,raw,f,pos,z)
                if a.graphs:
                    if pos.shape[1] not in replays:replays[pos.shape[1]],actual=qualified_replay(model,inputs,activity_scale,a.decoder_visible_only)
                    else:actual=replays[pos.shape[1]](inputs)
                else:actual=backward(model,inputs,activity_scale,a.decoder_visible_only)
                value,_,norm=actual
                if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Profile')
                opt.step();torch.cuda.synchronize()
                if i==3:began=time.perf_counter()
            elapsed=time.perf_counter()-began;profiles.append(dict(batch=batch,cached_windows_per_second=8*batch/elapsed,peak_mib=torch.cuda.max_memory_allocated()/2**20,gradient_parity='16/64 every gradient passed' if a.graphs else 'eager'));del replays,model,opt,actual,value,norm,inputs;gc.collect();torch.cuda.empty_cache()
        batch=max(profiles,key=lambda x:x['cached_windows_per_second'])['batch'];contract.update(batch=batch,profiles=profiles);write_json(a.output/'manifest.json',dict(contract,arms=arms));results=[]
        for arm in arms:
            out=a.output/arm;out.mkdir();began=time.perf_counter();torch.manual_seed(17)
            input_cal=None
            model=initialize(teacher,saved,cal,arm).cuda()
            if arm=='frozen':model.bar.requires_grad_(False)
            opt=optimizer(model,a.lr,a.bar_lr);g=torch.Generator(device='cuda').manual_seed(559)
            arm_contract=dict(contract,arm=arm,input_calibration=input_cal,repair_arm=arm,training_scope='joint' if arm.startswith('joint_') else 'protected_head',bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),parameters=sum(p.numel() for p in model.parameters()),decoder_parameters=sum(p.numel() for p in model.decoder.parameters()))
            w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v21-repair',name=a.output.name+'-'+arm,config=arm_contract,dir=str(out),reinit=True);step=0;status='failed';curve=[];seconds=0.;replays={}
            def save(path):torch.save(dict(model=model.state_dict(),optimizer=opt.state_dict(),contract=arm_contract,step=step,rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),sampling_rng=g.get_state()),path)
            try:
                tiny=copy.deepcopy(model);tiny_opt=optimizer(tiny,1e-5,1e-6);x,v,raw,f,z=[q[:1] for q in train];pos=positions(1,'cuda',64);tiny_curve=[]
                for t in range(201):
                    value,_=loss(tiny,x,v,raw,f,pos,z,activity_scale)
                    if t in (0,200):tiny_curve.append(float(value.detach()))
                    if t<200:tiny_opt.zero_grad();value.backward();stable_clip(tiny.parameters());tiny_opt.step()
                write_json(out/'tiny.json',dict(loss=tiny_curve));del tiny,tiny_opt
                if not tiny_curve[1]<tiny_curve[0]:raise ValueError('Tiny fit')
                for step in range(a.steps+1):
                    if time.perf_counter()-began>3000 or (a.output/'STOP').exists():raise InterruptedError('Deadline/STOP')
                    if step:
                        tick=time.perf_counter();ids=torch.randint(len(train[0]),(batch,),device='cuda',generator=g);x,v,raw,f,z=[q[ids] for q in train];pos=positions(batch,'cuda',64 if step%4==0 else 16,g)
                        set_gradient_scope(model,pos.shape[1],a.decoder_visible_only)
                        opt.zero_grad(set_to_none=True);inputs=(x,v,raw,f,pos,z)
                        if a.graphs:
                            if pos.shape[1] not in replays:replays[pos.shape[1]],actual=qualified_replay(model,inputs,activity_scale,a.decoder_visible_only)
                            else:actual=replays[pos.shape[1]](inputs)
                        else:actual=backward(model,inputs,activity_scale,a.decoder_visible_only)
                        value,part_values,norm=actual;parts=dict(zip(PARTS,part_values))
                        if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Loss/gradient')
                        opt.step();torch.cuda.synchronize();seconds+=time.perf_counter()-tick
                        if step%100==0:
                            speed=step*batch/seconds;w.log(dict({'train/loss':float(value.detach()),'train/cached_windows_per_second':speed},**{'train/'+k:float(v.detach()) for k,v in parts.items()}),step=step)
                            write_json(out/'progress.json',dict(step=step,steps=a.steps,seconds=time.perf_counter()-began,speed=speed,loss=float(value.detach())))
                    if step%500==0 or step==a.steps:
                        detailed,metrics=evaluate(model,teacher,dev);write_json(out/f'evaluation-{step}.json',detailed);write_json(out/'latest-metrics.json',metrics);curve.append(dict(step=step,metrics=metrics));w.log({'development/'+k:v for k,v in metrics.items()},step=step)
                        print(arm,step,{k:metrics[k] for k in ('all_visible/raw_mae/1s/trade_close','all_visible/candle/1s/trade/moving_direction_pct','activity/all_visible/1s/trade_volume/moving_direction_pct','bar_probe/raw_mae/1s/trade_close')},flush=True)
                verify_protected(model,saved);save(out/'checkpoint.pt');result=dict(arm=arm,checkpoint_sha=file_hash(out/'checkpoint.pt'),curve=curve,seconds=time.perf_counter()-began,wandb_url=w.url,cached_windows_per_second=step*batch/max(seconds,.001));write_json(out/'result.json',result);results.append(result);write_json(a.output/'comparison.json',results);status='complete'
            except BaseException:save(out/'checkpoint_partial.pt');raise
            finally:w.summary['status']=status;w.finish(exit_code=0 if status=='complete' else 1);replays.clear()


if __name__=='__main__':main()
