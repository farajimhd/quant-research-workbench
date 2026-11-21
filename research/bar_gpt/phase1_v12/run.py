import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,gc,json,math,random,shutil,time
from pathlib import Path
import numpy as np
import torch
from .model import Autoencoder,Config,positions,objective
from .data import load_teacher,prepare,resume_contract
from .step import backward,Replay
from .train import train,evaluate
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


@torch.no_grad()
def calibrate(a,teacher,identity,frozen,manifest):
    # Pin the exact user-selected checkpoint before deriving latent statistics.
    source=a.teacher
    progress=json.loads((source.parent/'progress.json').read_text())
    if progress['state'] not in ('complete','stopped'):
        raise ValueError('Phase0 must exit before teacher pinning')
    shutil.copy2(source,a.output/'teacher.pt');shutil.copy2(source.with_suffix('.proof.json'),a.output/'teacher.proof.json')
    if file_hash(a.output/'teacher.pt')!=identity['checkpoint_sha256']:raise ValueError('Pinned teacher changed')
    rows=np.load(a.data/'train.npy',mmap_mode='r');sums=torch.zeros(2,16,device='cuda',dtype=torch.float64);count=0;start=time.perf_counter()
    for offset in range(0,len(rows),512):
        b=prepare_raw(rows[offset:offset+512],True).to('cuda');z,_,valid,_,_=prepare(teacher,b)
        sums[0]+=torch.where(valid[...,None],z.double(),0).sum((0,1));sums[1]+=torch.where(valid[...,None],z.double().square(),0).sum((0,1));count+=int(valid.sum())
    mean=sums[0]/count;variance=(sums[1]/count-mean.square()).clamp_min(0)
    constant=variance==0;scale=torch.where(constant,1,variance.sqrt())
    if not torch.isfinite(mean).all() or not torch.isfinite(scale).all() or (scale<=0).any():raise ValueError('Invalid latent statistics')
    mean=mean.float();scale=scale.float();maximum=0.
    for offset in range(0,len(rows),1024):
        z=prepare(teacher,prepare_raw(rows[offset:offset+1024],True).to('cuda'))[0]
        restored=((z-mean)/scale)*scale+mean
        maximum=max(maximum,float(((restored-z).abs()/z.abs().clamp_min(1)).max()))
    if maximum>2e-6:raise ValueError('Latent transform roundtrip failed')
    record=dict(version='phase1-v12',teacher=identity,teacher_path=str(a.output/'teacher.pt'),split_hash=manifest['manifest_hash'],
                data_hash=frozen['hash'],training_windows=len(rows),valid_bar_exposures=count,mean=mean.tolist(),scale=scale.tolist(),
                constant_dimensions=constant.cpu().tolist(),roundtrip_max_error_over_max_one_target=maximum,seconds=time.perf_counter()-start,
                weighting='Uniform valid bar exposures in the frozen training-only subset; no validation statistics or sampling weights.')
    write_json(a.output/'calibration.json',record);print(json.dumps({k:record[k] for k in ('training_windows','valid_bar_exposures','seconds','roundtrip_max_error_over_max_one_target')}),flush=True)


def make_model(a,cal):
    return Autoencoder(Config(width=a.width,layers=a.layers,heads=a.heads,embedding=256,decoder_width=64,precision=a.precision),cal['mean'],cal['scale']).cuda()


def qualify(a,teacher,cal,manifest):
    torch.manual_seed(a.seed);model=make_model(a,cal);opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=0.,fused=True)
    b=prepare_raw(np.load(a.data/'tiny.npy',mmap_mode='r')[:1],True).to('cuda');pos=positions(1,'cuda')
    initial=float(backward(model,teacher,b,pos)[0]);replay=Replay(model,teacher,b,pos);curve=[];start=time.perf_counter()
    for step in range(a.tiny_steps):
        for group in opt.param_groups:group['lr']=1e-6+.5*(3e-4-1e-6)*(1+math.cos(math.pi*step/max(1,a.tiny_steps-1)))
        loss,parts,norm=replay(b,pos)
        if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Tiny gradient failure')
        opt.step()
        if step%200==0:curve.append(dict(step=step,loss=float(loss)))
    model.zero_grad(set_to_none=True);final=float(backward(model,teacher,b,pos)[0])
    record=dict(initial=initial,final=final,steps=a.tiny_steps,seconds=time.perf_counter()-start,curve=curve)
    write_json(a.output/'tiny.json',record)
    if final>.01 or final>initial*.01:raise ValueError('Tiny overfit gate failed')
    del model,opt,b,replay;gc.collect();torch.cuda.empty_cache()
    profiles=[]
    for pair in a.profile_configs.split(','):
        batch,workers=map(int,pair.split(':'));a.batch_size=batch;a.workers=workers
        torch.manual_seed(a.seed);model=make_model(a,cal);opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01,fused=True)
        source=loader(manifest,a,'train');device=replay=None;b=None;start=time.perf_counter();n=0
        try:
            device=DeviceBatches(source,'cuda');startup=time.perf_counter()-start
            for i in range(a.profile_batches+8):
                b=device.pop();pos=positions(len(b.grid),'cuda')
                if replay is None:
                    opt.zero_grad(set_to_none=True);expected=backward(model,teacher,b,pos);gradients=[p.grad.clone() for p in model.parameters()]
                    replay=Replay(model,teacher,b,pos);actual=replay(b,pos)
                    torch.testing.assert_close(actual[0],expected[0],rtol=1e-4,atol=2e-5)
                    for p,g in zip(model.parameters(),gradients):torch.testing.assert_close(p.grad,g,rtol=1e-3,atol=2e-5)
                    del gradients
                else:actual=replay(b,pos)
                device.advance()
                if not bool(torch.isfinite(actual[0])&torch.isfinite(actual[2])):raise FloatingPointError('Profile nonfinite gradient')
                opt.step()
                if i==7:torch.cuda.synchronize();began=time.perf_counter();wait=device.wait_seconds
                if i>=8:n+=len(b.grid)
            torch.cuda.synchronize();elapsed=time.perf_counter()-began
            profiles.append(dict(batch_windows=batch,workers=workers,steps=a.profile_batches,windows=n,seconds=elapsed,
                                 windows_per_second=n/elapsed,startup_seconds=startup,loader_wait_seconds=device.wait_seconds-wait,
                                 gpu_reserved_mib=torch.cuda.memory_reserved()/2**20,precision=a.precision))
            write_json(a.output/'profiles.json',profiles);print(profiles[-1],flush=True)
        finally:
            if device:device.close()
            close_loader(source);del replay,model,opt,b,source,device;gc.collect();torch.cuda.empty_cache()
    if any(p.grad is not None or p.requires_grad for p in teacher.parameters()):raise AssertionError('Teacher mutated')
    write_json(a.output/'selection.json',max(profiles,key=lambda x:x['windows_per_second']))


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['calibrate','qualify','profile-validation','train'],required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--teacher',type=Path);p.add_argument('--calibration',type=Path)
    p.add_argument('--teacher-epoch',type=int,default=19)
    p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'))
    p.add_argument('--split',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--epochs',type=int,default=20);p.add_argument('--batch-size',type=int,default=4096);p.add_argument('--workers',type=int,default=16)
    p.add_argument('--learning-rate',type=float,default=3e-4);p.add_argument('--min-learning-rate',type=float,default=3e-6);p.add_argument('--warmup-steps',type=int,default=1000)
    p.add_argument('--validation-every',type=int,default=2000);p.add_argument('--checkpoint-every',type=int,default=1000)
    p.add_argument('--max-updates',type=int,default=0);p.add_argument('--resume',type=Path);p.add_argument('--tiny-steps',type=int,default=4000)
    p.add_argument('--profile-batches',type=int,default=128);p.add_argument('--profile-configs',default='2048:8,4096:16,8192:16,8192:32,16384:16')
    p.add_argument('--width',type=int,default=128);p.add_argument('--layers',type=int,default=2);p.add_argument('--heads',type=int,default=4)
    p.add_argument('--precision',choices=['fp32','bf16'],default='bf16');p.add_argument('--no-graphs',dest='graphs',action='store_false')
    p.add_argument('--no-metric-graphs',dest='metric_graphs',action='store_false');a=p.parse_args()
    a.device='cuda';a.preparation='raw_gpu';a.seed=17;a.prefetch=4;a.shard_prefetch=16;a.shard_prefetch_mib=64
    a.validation_batch_size=a.batch_size;a.validation_workers=a.workers
    if min(a.batch_size,a.epochs,a.warmup_steps,a.validation_every,a.checkpoint_every)<1 or not 0<=a.workers<=32:raise ValueError('Invalid bounds')
    if a.mode=='calibrate' and a.teacher is None:raise ValueError('Final teacher checkpoint required')
    if a.mode!='calibrate' and a.calibration is None:raise ValueError('Frozen latent calibration required')
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.manual_seed(a.seed);random.seed(a.seed);np.random.seed(a.seed)
    frozen=verify(a.data);manifest=load_split(a.split)
    if frozen['source_split_hash']!=manifest['manifest_hash']:raise ValueError('Subset/full split mismatch')
    cal=None if a.mode=='calibrate' else json.loads(a.calibration.read_text())
    path=a.teacher if cal is None else Path(cal['teacher_path'])
    teacher,identity=load_teacher(path,manifest['manifest_hash'],'cuda',cal['teacher']['checkpoint_epoch'] if cal else a.teacher_epoch)
    if cal and (cal['split_hash']!=manifest['manifest_hash'] or cal['data_hash']!=frozen['hash'] or cal['teacher']!=identity):raise ValueError('Teacher/calibration/split mismatch')
    if identity['contract']['counts']!=manifest['counts']:raise ValueError('Phase0 population differs')
    manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    with lease(a.output):
        if a.mode=='calibrate':return calibrate(a,teacher,identity,frozen,manifest)
        if a.mode=='qualify':return qualify(a,teacher,cal,manifest)
        torch.manual_seed(a.seed);model=make_model(a,cal)
        if a.mode=='profile-validation':
            reporter=Reporter(a.output,dict(output=str(a.output)),mode='disabled',project='bargpt-phase1-v12-frozen16-mae')
            profiles=[]
            try:
                for repeat in range(2):
                    result=evaluate(model,teacher,manifest,a,reporter,repeat,limit_batches=a.profile_batches)
                    if result is None:reporter.finish('stopped');return
                    r=result[0];profiles.append(dict(repeat=repeat,windows=r['windows'],seconds=r['seconds'],startup_seconds=r['startup_seconds'],
                                                   pipeline_seconds=r['pipeline_seconds'],pipeline_windows_per_second=r['windows']/r['pipeline_seconds']))
                    write_json(a.output/'evaluation_profiles.json',profiles);print(profiles[-1],flush=True)
                reporter.finish('qualification_complete')
            except BaseException as exc:reporter.finish('failed',repr(exc));raise
            return
        contract=dict(version='phase1-v12',source=a.source_revision,config=vars(model.config),teacher=identity,
                      calibration_sha256=file_hash(a.calibration),split_hash=manifest['manifest_hash'],counts=manifest['counts'],
                      epochs=a.epochs,batch_size=a.batch_size,workers=a.workers,learning_rate=a.learning_rate,min_learning_rate=a.min_learning_rate,
                      warmup_steps=a.warmup_steps,weight_decay=.01,seed=a.seed,graphs=a.graphs,metric_graphs=a.metric_graphs,
                      objective='Training-only normalized frozen16D latents: hidden SmoothL1 +0.1 visible SmoothL1; raw metrics diagnostic only',
                      precision='Teacher FP32, temporal encoder '+a.precision+', decoder and latent loss FP32',
                      parameters=sum(p.numel() for p in model.parameters()),decoder_parameters=sum(p.numel() for p in model.decoder.parameters())+model.queries.numel())
        if a.resume:contract=resume_contract(contract,a.resume)
        config=dict(model=contract,engineering_revision=a.source_revision,output=str(a.output),resume=str(a.resume) if a.resume else None)
        if not a.resume:write_json(a.output/'manifest.json',config)
        else:write_json(a.output/f'resume-{int(time.time())}.json',config)
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False);os.environ.setdefault('WANDB_ENTITY','mehdifaraji')
        reporter=Reporter(a.output,config,mode='online',project='bargpt-phase1-v12-frozen16-mae')
        if reporter.run is None:raise RuntimeError('Online W&B logging required')
        reporter.run.save(str(a.calibration),base_path=str(a.calibration.parent),policy='now')
        try:reporter.finish(train(model,teacher,manifest,a,contract,reporter))
        except BaseException as exc:reporter.finish('failed',repr(exc));raise


if __name__=='__main__':main()
