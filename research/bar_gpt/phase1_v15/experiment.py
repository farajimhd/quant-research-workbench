"""Bounded training of visibility-balanced, numerically stable latent MAE."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,gc,json,time
from pathlib import Path
import numpy as np
import torch
import wandb
from .model import Autoencoder
from ..phase1_v12.model import Autoencoder as Original,Config,positions,objective
from ..phase1_v12.data import load_teacher,prepare
from ..phase1_v10.step import stable_clip
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v11.data import verify
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.phase1_contract import write_json,file_hash
from research.mlops.env import discover_env_files,load_env_files
from ..phase1_v12.metrics import Metrics,scalars
from types import SimpleNamespace


from ..phase1_v13.experiment import cache,evaluate


@torch.no_grad()
def audit(m,teacher,data):
    z,meta,valid,x,v,frames,raw=data;metric=Metrics();g=torch.Generator(device='cuda').manual_seed(917)
    m.eval()
    for i in range(0,len(z),512):
        sl=slice(i,i+512);pos=positions(len(z[sl]),'cuda',16,g)
        b=SimpleNamespace(grid=raw[sl],frames=frames[sl])
        metric.add(m,teacher,b,(z[sl],meta[sl],valid[sl],x[sl],v[sl],m(z[sl],meta[sl],pos),m(z[sl],meta[sl],positions(len(z[sl]),'cuda',64))),pos)
    result=metric.result()
    if result['windows']!=len(z) or result['nonfinite']:raise ValueError('Audit population/finite gate failed')
    m.train();return result,scalars(result)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--steps',type=int,default=50000);p.add_argument('--batch',type=int,default=1024);p.add_argument('--variants',default='stable_balanced');p.add_argument('--seed',type=int,default=17);a=p.parse_args()
    variants=a.variants.split(',')
    if not variants or len(set(variants))!=len(variants) or any(x not in ('original','balanced','stable','stable_balanced') for x in variants):raise ValueError('Invalid variants')
    if a.output.exists():raise ValueError('Use a new immutable experiment directory')
    if not 1<=a.steps<=150000 or not 1<=a.batch<=8192:raise ValueError('Invalid bounded experiment geometry')
    a.output.mkdir(parents=True);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt/phase1-v12-frozen16-mae-20261121')
    run=root/'full-frozen16-mae256-20ep'
    if json.loads((run/'progress.json').read_text())['state']!='stopped':raise ValueError('Full run must remain stopped')
    import psutil
    owner=json.loads((run/'process.json').read_text())
    if psutil.pid_exists(owner['pid']) and abs(psutil.Process(owner['pid']).create_time()-owner['created'])<.1:raise ValueError('Old owner live')
    cal=json.loads((root/'teacher/calibration.json').read_text());teacher,_=load_teacher(root/'teacher/teacher.pt',cal['split_hash'],'cuda')
    path=run/'checkpoint_best.pt';peek=torch.load(path,map_location='cpu',weights_only=False);state=load_checkpoint(path,peek['contract'])
    dataset=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset');verified=verify(dataset)
    if verified['hash']!=cal['data_hash']:raise ValueError('Frozen subset changed')
    began=time.perf_counter();train=cache(dataset/'train.npy',teacher);dev=cache(dataset/'development.npy',teacher)
    contract=dict(source=a.revision,teacher_sha=cal['teacher']['checkpoint_sha256'],parent_sha=file_hash(path),data_hash=verified['hash'],split_hash=cal['split_hash'],train_windows=len(train[0]),dev_windows=len(dev[0]),steps=a.steps,batch=a.batch,lr=3e-5,seed=a.seed,cache_seconds=time.perf_counter()-began,scope='Frozen subset ablation, not full population. GPU cached FP32 teacher targets; throughput excludes raw loader and teacher.')
    contract.update(config=state['contract']['config'],target_normalization='unchanged training-only zscore',initialization='fresh matched weights',valid_training_bars=int(train[2].sum()))
    write_json(a.output/'manifest.json',contract)
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    all_results=[]
    for variant in variants:
        name=variant;out=a.output/name;out.mkdir()
        torch.manual_seed(a.seed)
        m=Autoencoder(Config(**state['contract']['config']),cal['mean'],cal['scale'],variant).cuda()
        opt=torch.optim.AdamW(m.parameters(),lr=3e-5,weight_decay=.01,fused=True)
        w=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v15-visibility-fix',name=a.output.name+'-'+name,dir=str(out),config=dict(contract,variant=variant),reinit=True)
        start=time.perf_counter();curve=[];training_seconds=0.;status='failed';step=0
        try:
            measured=evaluate(m,teacher,dev);w.log({'development/'+k:v for k,v in measured.items()},step=0)
            curve.append(dict(step=0,metrics=measured));print(name,0,measured,flush=True)
            g=torch.Generator(device='cuda').manual_seed(555)
            for step in range(1,a.steps+1):
                if time.perf_counter()-start>2700:raise TimeoutError('45 minute per-arm deadline')
                if (a.output/'STOP').exists():raise InterruptedError('Human STOP')
                tick=time.perf_counter();ids=torch.randint(len(train[0]),(a.batch,),device='cuda',generator=g)
                z,meta,valid=[x[ids] for x in train[:3]];pos=positions(len(z),'cuda',16,g)
                opt.zero_grad(set_to_none=True);pred=m(z,meta,pos);loss,_=objective(m,pred,z,valid,pos)
                loss.backward();norm=stable_clip(m.parameters())
                if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite gradient')
                opt.step();torch.cuda.synchronize();training_seconds+=time.perf_counter()-tick
                if step%100==0:w.log({'train/loss':float(loss.detach()),'train/gradient_norm':float(norm),'train/cached_windows_per_second':step*a.batch/training_seconds},step=step)
                if step%10000==0 or step==a.steps:
                    measured=evaluate(m,teacher,dev);w.log({'development/'+k:v for k,v in measured.items()},step=step)
                    curve.append(dict(step=step,metrics=measured));print(name,step,measured,flush=True)
                    write_json(out/'progress.json',dict(step=step,curve=curve))
            detailed,detailed_scalars=audit(m,teacher,dev)
            write_json(out/'detailed-development.json',detailed);write_json(out/'detailed-scalars.json',detailed_scalars)
            w.log({'detailed_development/'+k:v for k,v in detailed_scalars.items()},step=a.steps)
            training_metrics=evaluate(m,teacher,train);w.log({'training_evaluation/'+k:v for k,v in training_metrics.items()},step=a.steps)
            torch.save(dict(model=m.state_dict(),optimizer=opt.state_dict(),contract=dict(contract,variant=variant),step=a.steps,rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),sampling_rng=g.get_state()),out/'checkpoint.pt')
            result=dict(name=name,curve=curve,seconds=time.perf_counter()-start,cached_windows_per_second=a.steps*a.batch/training_seconds,checkpoint_sha=file_hash(out/'checkpoint.pt'),wandb_url=w.url,parameters=sum(p.numel() for p in m.parameters()))
            result['training_evaluation']=training_metrics
            write_json(out/'result.json',result);all_results.append(result);write_json(a.output/'comparison.json',all_results);status='complete'
        except BaseException as exc:
            torch.save(dict(model=m.state_dict(),optimizer=opt.state_dict(),contract=dict(contract,variant=variant),step=step,rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),error=repr(exc)),out/'checkpoint_partial.pt')
            raise
        finally:
            w.summary['status']=status;w.finish(exit_code=0 if status=='complete' else 1)
            del m,opt;gc.collect();torch.cuda.empty_cache()


if __name__=='__main__':main()



