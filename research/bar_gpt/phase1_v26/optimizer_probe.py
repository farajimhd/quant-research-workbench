"""Bounded retained-vs-fresh Adam diagnosis at the user-requested uniform1e-4."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,gc,json,time
from pathlib import Path
import torch
from .model import restore
from .train import optimizer
from .step import Replay
from .gates import check
from ..phase1_v25.data import load
from ..phase1_v25.metrics import evaluate
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease,load_checkpoint
from ..v5.shards.phase1_contract import write_json,file_hash
from research.mlops.env import discover_env_files,load_env_files


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True);p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--steps',type=int,choices=(1024,4096),default=1024)
    a=p.parse_args();a.device='cuda';a.resume=False;preflight(a)
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.cuda.set_per_process_memory_fraction(.85)
    root=a.output.parent
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    import wandb
    with lease(a.output):
        peek=torch.load(a.checkpoint,map_location='cpu',weights_only=False);saved=load_checkpoint(a.checkpoint,peek['contract']);del peek
        train,dev,_,provenance=load(root)
        teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',saved['contract']['split_hash'],'cuda')
        with torch.no_grad():anchor=teacher.encode(train[0],train[1])
        baseline_model=restore(saved).cuda();baseline_model.configure('independent');_,reference=evaluate(baseline_model,dev);del baseline_model
        write_json(a.output/'reference.json',reference)
        config=dict(source=a.source_revision,checkpoint_sha=file_hash(a.checkpoint),data=provenance,lr=1e-4,batch=1024,steps=a.steps,
            hypothesis='Does resetting Adam moments improve stability at the SAME uniform1e-4?',seed=73391,
            checkpoint_upload=False,deadline_seconds_per_arm=900,architecture='unchanged V26',loss='unchanged V26')
        write_json(a.output/'manifest.json',config);results=[]
        for mode in ('retained_adam','fresh_adam'):
            run=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v26-full20',name=a.output.name+'-'+mode,group='uniform-epoch-cosine-diagnosis',dir=str(a.output),config=dict(config,mode=mode))
            run.define_metric('optimizer_step');run.define_metric('*',step_metric='optimizer_step')
            model=restore(saved).cuda();model.configure('independent');opt=optimizer(model,dict(price=1e-4,activity=1e-4))
            if mode=='retained_adam':opt.load_state_dict(copy.deepcopy(saved['optimizer']))
            for g in opt.param_groups:g['lr']=g['peak']=1e-4
            gen=torch.Generator(device='cuda').manual_seed(73391);graphs={};started=time.perf_counter();evaluations=[];failed=True
            try:
                for i in range(1,a.steps+1):
                    if time.perf_counter()-started>900:raise TimeoutError('Bounded optimizer diagnosis')
                    if (a.output/'STOP').exists() or (root/'STOP').exists():raise InterruptedError('STOP')
                    ids=torch.randint(len(train[0]),(1024,),device='cuda',generator=gen)
                    pp=positions(1024,'cuda',64 if (saved['step']+i)%4==0 else 16,gen);ap=positions(1024,'cuda',56,gen)
                    batch=(*[t[ids] for t in train],anchor[ids],pp,ap)
                    if pp.shape[1] not in graphs:graphs[pp.shape[1]]=Replay(model,batch)
                    value,_,norm=graphs[pp.shape[1]](batch)
                    if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite diagnosis update')
                    opt.step()
                    if i%32==0:
                        row=dict(step=i,loss=float(value),speed=i*1024/(time.perf_counter()-started),mode=mode)
                        write_json(a.output/'progress.json',row);run.log(dict(optimizer_step=i,**{'train/'+k:v for k,v in row.items() if k!='mode'}))
                    if i in (128,512,1024,2048,4096):
                        _,metrics=evaluate(model,dev);gate=check(metrics,reference)
                        write_json(a.output/f'{mode}-{i}-metrics.json',metrics)
                        evaluations.append(dict(step=i,gate=gate));run.log(dict(optimizer_step=i,**{'development/'+k:v for k,v in metrics.items()}))
                        print(json.dumps(dict(mode=mode,step=i,accepted=gate['accepted'],summary=gate['summary'],price_ratio=gate['ratios']['price']['geomean'],activity_ratio=gate['ratios']['activity']['geomean'])),flush=True)
                result=dict(mode=mode,evaluations=evaluations,seconds=time.perf_counter()-started,wandb_url=run.url,status='complete')
                results.append(result);write_json(a.output/'results.json',results);run.summary['conclusion']=result
                failed=False
            finally:
                run.finish(exit_code=int(failed));graphs.clear();del model,opt,batch;gc.collect();torch.cuda.empty_cache()


if __name__=='__main__':main()
