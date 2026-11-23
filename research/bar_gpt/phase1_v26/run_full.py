"""Profile or run the authorized 20 complete Phase1 epochs, without weight uploads."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,gc,json,random,time
from pathlib import Path
import numpy as np
import torch
from .model import restore
from .train import optimizer
from .full_step import qualify
from .full_validate import Accumulator,Replay as ValidationReplay
from .full_train import train,epoch_learning_rate
from ..phase1_v12.model import positions
from ..phase1_v12.data import load_teacher,resume_contract
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..phase1_v11.data import verify
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.data_v3 import load_split,close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json
from .reporting import Reporter,ReportingFailure
from research.mlops.env import discover_env_files,load_env_files

ROOT=Path('D:/TradingML/runtimes/bar_gpt')
PARENT_SHA='4200b11d6330db1c3556ee9f3cfb594d3196819f1978cd00cc5785f5260a146b'


def profile(a,teacher,manifest,saved):
    results=[]
    for bs,workers in [tuple(map(int,p.split(':'))) for p in a.profile_configs.split(',')]:
        if bs not in (256,512,1024,2048,4096,8192,16384) or not 0<=workers<=32:raise ValueError('Profile bounds')
        a.batch_size=a.validation_batch_size=bs;a.workers=a.validation_workers=workers
        model=restore(saved).cuda();model.configure('independent');opt=optimizer(model,dict(price=a.price_lr,activity=a.activity_lr))
        source=device=None;graphs={};batch=actual=None;start=time.perf_counter();row=dict(batch=bs,workers=workers);status='complete'
        try:
            source=loader(manifest,a,'train');device=DeviceBatches(source,'cuda');row['startup_seconds']=time.perf_counter()-start
            torch.cuda.reset_peak_memory_stats();count=0
            for i in range(a.profile_batches+8):
                if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():raise InterruptedError('STOP')
                batch=device.pop();pp=positions(len(batch.grid),'cuda',64 if (i+1)%4==0 else 16);ap=positions(len(batch.grid),'cuda',56)
                if pp.shape[1] not in graphs:graphs[pp.shape[1]],actual=qualify(model,teacher,batch,pp,ap)
                else:actual=graphs[pp.shape[1]](batch,pp,ap)
                device.advance()
                if not bool(torch.isfinite(actual[0])&torch.isfinite(actual[2])):raise FloatingPointError('Profile gradients')
                opt.step()
                if i==7:torch.cuda.synchronize();tick=time.perf_counter();wait=device.wait_seconds
                if i>=8:count+=len(batch.grid)
            torch.cuda.synchronize();elapsed=time.perf_counter()-tick
            row.update(speed=count/elapsed,loader_wait_seconds=device.wait_seconds-wait,seconds=elapsed,peak_mib=torch.cuda.max_memory_allocated()/2**20,reserved_mib=torch.cuda.max_memory_reserved()/2**20,gradient_parity=True)
            # Same full-metric path that will run at each epoch; bounded probe.
            device.close();close_loader(source);device=source=None
            graphs.clear();model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache();model.eval();acc=Accumulator()
            start=time.perf_counter();source=loader(manifest,a,'validation');device=DeviceBatches(source,'cuda');row['validation_startup_seconds']=time.perf_counter()-start
            batch=device.pop();pos=torch.rand(len(batch.grid),64,device='cuda').argsort(1);vr=ValidationReplay(model,acc,batch,pos)
            torch.cuda.synchronize();tick=time.perf_counter();wait=device.wait_seconds;count=0
            for i in range(16):
                if i:batch=device.pop()
                vr(batch,pos);device.advance();count+=len(batch.grid)
            torch.cuda.synchronize();row['validation_speed']=count/(time.perf_counter()-tick);row['validation_loader_wait_seconds']=device.wait_seconds-wait
            del vr,acc
        except torch.OutOfMemoryError:
            status='out_of_memory';row['speed']=0.
        finally:
            if device:device.close()
            if source:close_loader(source)
            graphs.clear();del model,opt,batch,actual,device,source;gc.collect();torch.cuda.empty_cache()
        row['status']=status;results.append(row);write_json(a.output/'profiles.json',results);print(json.dumps(row),flush=True)
    eligible=[r for r in results if r['status']=='complete']
    if not eligible:raise RuntimeError('No qualified profile configuration')
    write_json(a.output/'selection.json',max(eligible,key=lambda r:r['speed']))


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=('profile','train'),required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,default=ROOT/'phase1-v26-preserve-20261122/e03-independent-s559/checkpoint_best.pt')
    p.add_argument('--split',type=Path,default=ROOT/'phase1-v6-cohort-balanced-v4/split.json')
    p.add_argument('--data',type=Path,default=ROOT/'phase1-v8-learned-mae-20261117/dataset')
    p.add_argument('--epochs',type=int,default=20);p.add_argument('--batch-size',type=int,default=2048);p.add_argument('--workers',type=int,default=8)
    p.add_argument('--validation-batch-size',type=int,default=2048);p.add_argument('--validation-workers',type=int,default=8)
    p.add_argument('--prefetch',type=int,default=4);p.add_argument('--price-lr',type=float,default=3e-7);p.add_argument('--activity-lr',type=float,default=1e-6)
    p.add_argument('--warmup-steps',type=int,default=500);p.add_argument('--validation-every',type=int,default=1000);p.add_argument('--checkpoint-every',type=int,default=250)
    p.add_argument('--max-updates',type=int,default=0);p.add_argument('--resume',type=Path)
    p.add_argument('--fork-from',type=Path)
    p.add_argument('--schedule',choices=('window_cosine','epoch_cosine'),default='window_cosine')
    p.add_argument('--lr-peak',type=float,default=1e-4);p.add_argument('--lr-floor',type=float,default=1e-6)
    p.add_argument('--profile-batches',type=int,default=32);p.add_argument('--profile-configs',default='512:8,1024:8,2048:8,1024:16,1024:32')
    p.add_argument('--matmul-precision',choices=('highest','high'),default='highest')
    a=p.parse_args();a.device='cuda';a.preparation='raw_gpu';a.seed=17;a.shard_prefetch=16;a.shard_prefetch_mib=64
    if not 1<=a.epochs<=20 or not 1<=a.batch_size<=16384 or not 0<=a.workers<=32:raise ValueError('Bounds')
    if not 1<=a.validation_batch_size<=16384 or not 0<=a.validation_workers<=32:raise ValueError('Validation bounds')
    if not 1e-9<=a.price_lr<=1e-4 or not 1e-9<=a.activity_lr<=1e-4:raise ValueError('LR bounds')
    if min(a.warmup_steps,a.validation_every,a.checkpoint_every,a.profile_batches)<1 or a.max_updates<0:raise ValueError('Cadence')
    if not 0<a.lr_floor<=a.lr_peak<=1e-3:raise ValueError('LR endpoints')
    if a.schedule=='epoch_cosine' and a.epochs<2:raise ValueError('At least two cosine values required')
    if a.fork_from and a.schedule!='epoch_cosine':raise ValueError('Scientific fork requires explicit new schedule')
    if a.fork_from and not a.resume and (a.output/'wandb-identity.json').exists():raise ValueError('Fork needs a new run identity')
    preflight(a);torch.cuda.set_per_process_memory_fraction(.85);torch.set_num_threads(4);torch.set_float32_matmul_precision(a.matmul_precision);torch.manual_seed(a.seed);random.seed(a.seed);np.random.seed(a.seed)
    if file_hash(a.checkpoint)!=PARENT_SHA:raise ValueError('Selected V26 checkpoint changed')
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False);split=load_split(a.split);data=verify(a.data)
    if saved['contract']['split_hash']!=split['manifest_hash'] or data['source_split_hash']!=split['manifest_hash']:raise ValueError('Split differs')
    teacher,identity=load_teacher(ROOT/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',split['manifest_hash'],'cuda')
    if identity['checkpoint_sha256']!=saved['contract']['anchor_teacher']:raise ValueError('Teacher changed')
    split=attach(split,Path(split['dataset']));split['splits']={}
    with lease(a.output):
        if a.mode=='profile':
            write_json(a.output/'manifest.json',dict(source=a.source_revision,parent_sha=PARENT_SHA,split_hash=split['manifest_hash'],counts=split['counts'],precision=a.matmul_precision,configs=a.profile_configs,batches=a.profile_batches,memory_fraction=.85))
            return profile(a,teacher,split,saved)
        model=restore(saved).cuda();model.configure('independent')
        contract=dict(version='phase1-v26-full20',source=a.source_revision,parent_sha=PARENT_SHA,teacher=identity,
            split_hash=split['manifest_hash'],data_hash=data['hash'],counts=split['counts'],epochs=a.epochs,batch_size=a.batch_size,workers=a.workers,seed=a.seed,
            price_lr=a.price_lr,activity_lr=a.activity_lr,warmup_steps=a.warmup_steps,weight_decay=0.,matmul_precision=a.matmul_precision,
            schedule='1percent floor to peak warmup; cosine to1percent floor over20 full epochs, consumed-window clock',
            initialization='Selected V26 weights; fresh optimizer and explicit new full-data scientific identity',
            loss='Unchanged V26 family losses and price teacher anchor; independent clipping',
            visibility='Price16 random visible in3/4 updates,64 fourth; activity56 random visible; validation64/56/16',
            validation_batch_size=a.validation_batch_size,validation_workers=a.validation_workers,
            validation_every=a.validation_every,checkpoint_every=a.checkpoint_every,parameters=sum(p.numel() for p in model.parameters()),
            representation='64x64',decoder_parameters=792,authorization='User approved20 epochs on full Phase1 after V26 audit; optimize pipeline',checkpoint_upload=False)
        if a.schedule=='epoch_cosine':
            contract.update(version='phase1-v26-full20-epoch-cosine',schedule='20 epoch-held cosine values; identical LR in ALL parameter groups; no warmup',
                lr_peak=a.lr_peak,lr_floor=a.lr_floor,lr_values=[epoch_learning_rate(i,a.epochs,a.lr_peak,a.lr_floor) for i in range(a.epochs)],
                initialization='Continue weights, Adam state, RNG and exact data cursor from preserved checkpoint; new scientific W&B identity',
                fork_checkpoint_sha=file_hash(a.fork_from) if a.fork_from else None,
                price_lr=a.lr_peak,activity_lr=a.lr_peak,warmup_steps=0)
        if a.resume:contract=resume_contract(contract,a.resume)
        config=dict(model=contract,workers=a.workers,engineering_revision=a.source_revision,output=str(a.output),resume=str(a.resume) if a.resume else None)
        write_json(a.output/(f'resume-{int(time.time())}.json' if a.resume else 'manifest.json'),config)
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False);os.environ.setdefault('WANDB_ENTITY','mehdifaraji')
        reporter=Reporter(a.output,config,mode='online',project='bargpt-phase01-v26-full20')
        if reporter.run is None:raise RuntimeError('Online W&B required')
        write_json(a.output/'launch.json',dict(argv=__import__('sys').argv,source=a.source_revision))
        try:
            result=train(model,teacher,split,a,contract,reporter);write_json(a.output/'result.json',dict(status=result,wandb_url=reporter.run.url));reporter.finish(result)
        except ReportingFailure as exc:
            import traceback
            proof=json.loads((a.output/'checkpoint_last.proof.json').read_text())
            failure=dict(error=repr(exc),traceback=traceback.format_exc(),pid=os.getpid(),checkpoint_sha=proof['sha256'],step=proof['step'])
            write_json(a.output/f'failure-{time.time_ns()}.json',failure)
            write_json(a.output/'reporting-retry.json',failure)
            reporter.close_local('recovering_reporting',repr(exc))
            print(failure['traceback'],flush=True)
            # Checkpoint, journal, local status and workers have all been closed.
            # A broken W&B atexit handler must not mask the receipt or block exit.
            os._exit(75)
        except BaseException as exc:
            import traceback
            failure=dict(error=repr(exc),traceback=traceback.format_exc())
            write_json(a.output/f'failure-{time.time_ns()}.json',failure)
            try:reporter.finish('failed',repr(exc))
            except Exception as cleanup:
                failure['cleanup_error']=repr(cleanup);write_json(a.output/f'cleanup-failure-{time.time_ns()}.json',failure)
                reporter.close_local('failed',repr(exc))
            raise


if __name__=='__main__':main()
