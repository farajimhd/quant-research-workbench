"""Engineering-only large-batch qualification; never modifies saved weights."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,gc,json,time
from pathlib import Path
import numpy as np
import torch
from .model import restore,optimizer,build
from ..phase1_v8.runtime import preflight
from ..phase1_v21.step import qualified_replay
from ..phase1_v12.model import positions
from ..phase0_v3.experiment import cache
from ..phase1_v12.data import load_teacher
from ..phase1_v11.data import verify
from ..phase1_v19.objectives import calibrate
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True);a=p.parse_args()
    a.device='cuda';a.resume=False;a.source_revision=a.revision;preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt');data=root/'phase1-v8-learned-mae-20261117/dataset';verified=verify(data)
    receipt=json.loads((a.checkpoint.parent/'result.json').read_text());sha=file_hash(a.checkpoint)
    if sha!=receipt['checkpoint_sha']:raise ValueError('Pinned last checkpoint changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False);started=time.time();results=[]
    with lease(a.output):
        teacher,_=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',verified['source_split_hash'],'cuda')
        parts=cache(np.load(data/'train.npy',mmap_mode='r'))
        with torch.no_grad():old=torch.cat([teacher.encode(parts[0][i:i+512],parts[1][i:i+512]) for i in range(0,len(parts[0]),512)])
        train=(*parts,old);scale,_=calibrate(train[2],train[1],train[3])
        # Extend the original short learning check to measure actual one-example
        # fitting, rather than treating a10percent loss decrease as a near-zero fit.
        torch.manual_seed(17);tiny=build(saved['parent_template'],**saved['architecture']).cuda();tiny_opt=optimizer(tiny)
        x,v,raw,f,z=[q[:1] for q in train];inp=(x,v,raw,f,positions(1,'cuda',64),z)
        replay,actual=qualified_replay(tiny,inp,scale);tiny_curve=[dict(step=0,loss=float(actual[0]))]
        for step in range(1,2001):
            tiny_opt.zero_grad(set_to_none=True);actual=replay(inp);tiny_opt.step()
            if step%200==0:tiny_curve.append(dict(step=step,loss=float(actual[0])))
        with torch.no_grad():
            from ..phase1_v11.transforms import inverse
            prediction=inverse(tiny(x,v,inp[4])[2]['logs']);mask=v[...,:18]
            err=(prediction-raw).abs();per_channel=(torch.where(mask,err,0).sum((0,1))/mask.sum((0,1)).clamp_min(1)).tolist()
        write_json(a.output/'extended-tiny.json',dict(curve=tiny_curve,all_visible_raw_mae_by_channel=per_channel,scope='Fresh readouts from same warm parent, same first training example; longer qualification only, candidate checkpoint untouched'))
        del tiny,tiny_opt,replay;gc.collect();torch.cuda.empty_cache()
        for batch in (4096,8192,16384):
            if (a.output/'STOP').exists() or time.time()-started>600:raise InterruptedError('Profile deadline/STOP')
            model=restore(saved).cuda();opt=optimizer(model);graphs={};torch.cuda.reset_peak_memory_stats()
            try:
                for step in range(24):
                    ids=torch.arange(batch,device='cuda')%len(train[0]);x,v,raw,f,z=[q[ids] for q in train];pos=positions(batch,'cuda',64 if step%4==3 else 16)
                    inp=(x,v,raw,f,pos,z);opt.zero_grad(set_to_none=True)
                    if pos.shape[1] not in graphs:graphs[pos.shape[1]],actual=qualified_replay(model,inp,scale)
                    else:actual=graphs[pos.shape[1]](inp)
                    if not bool(torch.isfinite(actual[0])&torch.isfinite(actual[2])):raise FloatingPointError('Profile gradient')
                    opt.step()
                    if step==7:torch.cuda.synchronize();tick=time.perf_counter()
                torch.cuda.synchronize();row=dict(batch=batch,windows_per_second=16*batch/(time.perf_counter()-tick),peak_mib=torch.cuda.max_memory_allocated()/2**20,gradient_parity=True)
            except torch.OutOfMemoryError:
                row=dict(batch=batch,rejected='CUDA OOM; saved checkpoint unchanged')
            finally:
                graphs.clear();del model,opt;gc.collect();torch.cuda.empty_cache()
            results.append(row);write_json(a.output/'profiles.json',dict(source=a.revision,checkpoint_sha=sha,results=results));print(json.dumps(row),flush=True)
        if file_hash(a.checkpoint)!=sha:raise ValueError('Checkpoint changed during profiling')
        write_json(a.output/'result.json',dict(source=a.revision,checkpoint_sha=sha,seconds=time.time()-started,results=results,best=max((r for r in results if 'windows_per_second' in r),key=lambda r:r['windows_per_second']),
            scope='Cached fixed-pool engineering profile, trained candidate cloned for each batch; no new scientific training run or saved-weight modification'))


if __name__=='__main__':main()
