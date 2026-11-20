import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,gc,json,math,random,time
from pathlib import Path
import numpy as np
import torch
from .model import Autoencoder,Config,forward_batch,objective
from .engine import backward,Replay,train
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v8.runtime import preflight
from ..phase1_v11.data import verify
from ..phase2_v2.train import lease
from ..v5.data_v3 import load_split,close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json
from ..v5.reporting_v1 import Reporter
from research.mlops.env import discover_env_files,load_env_files


def qualify(a,parameters,manifest):
    rows=np.load(a.data/'tiny.npy',mmap_mode='r');b=prepare_raw(rows[:1],True).to('cuda')
    # Explicit easy synthetic test: repeat one observed bar, retain legal time metadata.
    b.grid.copy_(b.grid[:,:1].expand_as(b.grid).clone());b.valid.copy_(b.valid[:,:1].expand_as(b.valid).clone())
    records=[]
    for kind in (() if a.profile_only else ('linear','residual_mlp')):
        torch.manual_seed(17);model=Autoencoder(parameters,Config(backbone=kind)).cuda();opt=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=0.,fused=True)
        initial=float(objective(model,*forward_batch(model,b)[:3])[0].detach())
        best=float('inf');began=time.perf_counter();replay=Replay(model,b);curve=[]
        for step in range(4000):
            lr=1e-6+.5*(3e-4-1e-6)*(1+math.cos(math.pi*step/3999))
            for group in opt.param_groups:group['lr']=lr
            loss,parts,norm=replay(b)
            if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Tiny gradient failure')
            opt.step();best=min(best,float(loss))
            if step%200==0:curve.append(dict(step=step,loss=float(loss),parts=parts.cpu().tolist(),lr=lr))
        final=float(objective(model,*forward_batch(model,b)[:3])[0].detach())
        records.append(dict(kind=kind,initial=initial,final=final,best=best,steps=step+1,curve=curve,seconds=time.perf_counter()-began))
        write_json(a.output/'tiny.json',records);del replay
    if records:
        write_json(a.output/'tiny.json',records)
        if records[-1]['final']>.1 or records[-1]['best']>records[-1]['initial']*.0001:raise ValueError('Easy-case fit failed; diagnose before full run')
        del model,opt
    del b;gc.collect();torch.cuda.empty_cache()
    profiles=[]
    for batch,workers in ([(int(pair.split(':')[0]),int(pair.split(':')[1])) for pair in a.profile_configs.split(',')] if a.profile_configs else ((256,8),(512,16),(1024,16),(2048,16),(1024,8),(1024,32))):
        a.batch_size=batch;a.workers=workers;model=Autoencoder(parameters).cuda();opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=0.,fused=True)
        source=loader(manifest,a,'train');device=None;replay=None;b=None;start=time.perf_counter();n=0;steps=0
        try:
            device=DeviceBatches(source,'cuda');startup=time.perf_counter()-start
            for i in range(a.profile_batches):
                b=device.pop()
                if replay is None:
                    # Captured and eager gradients must agree on the identical input and parameters.
                    opt.zero_grad(set_to_none=True);eager=backward(model,b);expected=[p.grad.clone() for p in model.parameters()]
                    replay=Replay(model,b);captured=replay(b)
                    torch.testing.assert_close(captured[0],eager[0],rtol=1e-5,atol=1e-6)
                    for p,g in zip(model.parameters(),expected):torch.testing.assert_close(p.grad,g,rtol=1e-4,atol=1e-5)
                    del expected
                    torch.cuda.synchronize();measure=time.perf_counter()
                else:captured=replay(b)
                device.advance()
                if not bool(torch.isfinite(captured[0])&torch.isfinite(captured[2])):raise FloatingPointError('Profile gradient failure')
                opt.step();n+=len(b.target);steps+=1
            torch.cuda.synchronize();elapsed=time.perf_counter()-measure
            profiles.append(dict(batch_windows=batch,batch_bars=batch*64,workers=workers,windows=n,steps=steps,
                startup_seconds=startup,seconds=elapsed,windows_per_second=n/elapsed,bars_per_second=n*64/elapsed,
                loader_wait_seconds=device.wait_seconds,gpu_reserved_mib=torch.cuda.memory_reserved()/2**20))
            write_json(a.output/'profiles.json',profiles);print(profiles[-1],flush=True)
        finally:
            if device:device.close()
            close_loader(source)
            del replay,model,opt,b,source,device;gc.collect();torch.cuda.empty_cache()
    write_json(a.output/'selection.json',max(profiles,key=lambda x:x['bars_per_second']))


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['qualify','train'],required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--calibration',type=Path,required=True)
    p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'))
    p.add_argument('--split',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--epochs',type=int,default=20);p.add_argument('--batch-size',type=int,default=1024);p.add_argument('--workers',type=int,default=16)
    p.add_argument('--learning-rate',type=float,default=1e-4);p.add_argument('--min-learning-rate',type=float,default=1e-6)
    p.add_argument('--validation-every',type=int,default=2000);p.add_argument('--checkpoint-every',type=int,default=1000)
    p.add_argument('--profile-batches',type=int,default=256);p.add_argument('--max-updates',type=int,default=0);p.add_argument('--resume',type=Path)
    p.add_argument('--profile-only',action='store_true');p.add_argument('--profile-configs',default='')
    p.add_argument('--no-graphs',dest='graphs',action='store_false');a=p.parse_args()
    a.device='cuda';a.preparation='raw_gpu';a.seed=17;a.prefetch=4;a.shard_prefetch=16;a.shard_prefetch_mib=64
    a.validation_batch_size=a.batch_size;a.validation_workers=a.workers
    if not 0<=a.workers<=32 or min(a.epochs,a.batch_size,a.validation_every,a.checkpoint_every)<=0:raise ValueError('Invalid bounds')
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.manual_seed(a.seed);random.seed(a.seed);np.random.seed(a.seed)
    cal=json.loads(a.calibration.read_text());frozen=verify(a.data);manifest=load_split(a.split)
    if cal['data_hash']!=frozen['hash'] or cal['split_hash']!=manifest['manifest_hash']:raise ValueError('Data/calibration mismatch')
    manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    with lease(a.output):
        if a.mode=='qualify':return qualify(a,cal['parameters'],manifest)
        model=Autoencoder(cal['parameters']).cuda()
        contract=dict(version='phase0-v1',config=vars(model.config),source=a.source_revision,calibration_sha256=file_hash(a.calibration),
            split_hash=manifest['manifest_hash'],counts=manifest['counts'],epochs=a.epochs,batch_size=a.batch_size,workers=a.workers,
            learning_rate=a.learning_rate,min_learning_rate=a.min_learning_rate,graphs=a.graphs,seed=a.seed,
            objective='FP32; mean price/activity SmoothL1, price1000 log units, plus0.1 standardized-coordinate loss; no mask',
            population='Every bar in every certified Phase1 window. Repeated/overlapping bar exposures retained, not claimed unique.',
            dependency='Decoder receives only16 learned dimensions. Fixed training-only calibration, no local references.',parameters=sum(p.numel() for p in model.parameters()))
        config=dict(model=contract,output=str(a.output),resume=str(a.resume) if a.resume else None)
        if not a.resume:write_json(a.output/'manifest.json',config)
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False);os.environ.setdefault('WANDB_ENTITY','mehdifaraji')
        reporter=Reporter(a.output,config,mode='online',project='bargpt-phase0-singlebar-v1')
        if reporter.run is None:raise RuntimeError('Online logging required')
        try:
            state=train(model,manifest,a,contract,reporter);reporter.finish(state)
        except BaseException as exc:reporter.finish('failed',repr(exc));raise


if __name__=='__main__':main()
