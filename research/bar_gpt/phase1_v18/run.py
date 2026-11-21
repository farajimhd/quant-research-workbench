"""Qualified full-population20-epoch runner and complete-pipeline profiler."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1')
import argparse,gc,json,random,time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v16.model import Autoencoder
from ..phase1_v12.model import Config,positions
from ..phase1_v12.data import load_teacher,resume_contract
from .step import configure,backward,Replay
from .train import train
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..phase1_v8.runtime import preflight
from ..phase1_v11.data import verify
from ..phase2_v2.train import lease
from ..v5.data_v3 import load_split,close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json
from ..v5.reporting_v1 import Reporter
from research.mlops.env import discover_env_files,load_env_files


def make_model(saved,cal):
    model=Autoencoder(Config(**saved['contract']['config']),cal['mean'],cal['scale'],'scaled_slots_price').cuda()
    model.load_state_dict(saved['model']);configure(model,saved['contract']);return model


def verify_resume_workers(output,workers):
    # EligibleStream partitions entries and seeds row permutations by worker
    # count. Replaying a cursor with different workers would skip other rows.
    original=json.loads((output/'manifest.json').read_text())
    if original['workers']!=workers:raise ValueError('Resume must retain the original loader worker count and ordered traversal')


def profile(a,teacher,cal,manifest,saved):
    results=[]
    for pair in a.profile_configs.split(','):
        a.batch_size,a.workers=map(int,pair.split(':'))
        if not 1<=a.batch_size<=32768 or not 0<=a.workers<=32:raise ValueError('Profile bounds')
        model=make_model(saved,cal);opt=torch.optim.AdamW(model.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True)
        source=loader(manifest,a,'train');device=None;replays={};replay=b=pos=actual=expected=None;torch.cuda.reset_peak_memory_stats()
        started=time.perf_counter();n=0
        try:
            device=DeviceBatches(source,'cuda');startup=time.perf_counter()-started
            for i in range(a.profile_batches+8):
                b=device.pop();pos=positions(len(b.grid),'cuda',64 if (i+1)%4==0 else 16)
                if pos.shape[1] not in replays:
                    opt.zero_grad(set_to_none=True);expected=backward(model,teacher,b,pos)
                    gradients=[p.grad.clone() for p in model.parameters()]
                    replay=Replay(model,teacher,b,pos);actual=replay(b,pos)
                    torch.testing.assert_close(actual[0],expected[0],rtol=1e-4,atol=2e-5)
                    for parameter,gradient in zip(model.parameters(),gradients):torch.testing.assert_close(parameter.grad,gradient,rtol=1e-3,atol=2e-5)
                    del gradients;replays[pos.shape[1]]=replay
                else:actual=replays[pos.shape[1]](b,pos)
                device.advance()
                if not bool(torch.isfinite(actual[0])&torch.isfinite(actual[2])):raise FloatingPointError('Nonfinite profiling update')
                opt.step()
                if i==7:torch.cuda.synchronize();began=time.perf_counter();wait=device.wait_seconds
                if i>=8:n+=len(b.grid)
            torch.cuda.synchronize();elapsed=time.perf_counter()-began
            result=dict(batch_windows=a.batch_size,workers=a.workers,windows=n,seconds=elapsed,windows_per_second=n/elapsed,
                        startup_seconds=startup,loader_wait_seconds=device.wait_seconds-wait,gpu_peak_mib=torch.cuda.max_memory_allocated()/2**20,
                        source=a.source_revision,parent_sha=file_hash(a.checkpoint),gradient_parity='16and64 visible graphs passed')
            results.append(result);write_json(a.output/'profiles.json',results);print(result,flush=True)
        finally:
            if device:device.close()
            close_loader(source);del replays,replay,b,pos,actual,expected,model,opt,source,device;gc.collect();torch.cuda.empty_cache()
    write_json(a.output/'selection.json',max(results,key=lambda x:x['windows_per_second']))


def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['profile','train'],required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--full-reference',type=Path);p.add_argument('--qualification',type=Path)
    p.add_argument('--calibration',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v12-frozen16-mae-20261121/teacher/calibration.json'))
    p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'))
    p.add_argument('--split',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--epochs',type=int,default=20);p.add_argument('--batch-size',type=int,default=8192);p.add_argument('--workers',type=int,default=16)
    p.add_argument('--learning-rate',type=float,default=1e-5);p.add_argument('--min-learning-rate',type=float,default=1e-5);p.add_argument('--warmup-steps',type=int,default=1)
    p.add_argument('--validation-every',type=int,default=2000);p.add_argument('--checkpoint-every',type=int,default=500)
    p.add_argument('--max-updates',type=int,default=0);p.add_argument('--resume',type=Path)
    p.add_argument('--profile-batches',type=int,default=64);p.add_argument('--profile-configs',default='4096:16,8192:16,16384:16,16384:32,32768:32')
    a=p.parse_args();a.device='cuda';a.preparation='raw_gpu';a.seed=17;a.prefetch=4;a.shard_prefetch=16;a.shard_prefetch_mib=64
    a.validation_batch_size=8192;a.validation_workers=16;a.graphs=True;a.metric_graphs=True
    if not 1<=a.epochs<=20 or not 1<=a.batch_size<=32768 or not 0<=a.workers<=32 or min(a.warmup_steps,a.validation_every,a.checkpoint_every)<1:raise ValueError('Training bounds')
    if not 1e-7<=a.min_learning_rate<=a.learning_rate<=1e-4:raise ValueError('Learning rate bounds')
    if a.resume:verify_resume_workers(a.output,a.workers)
    receipt=json.loads((a.checkpoint.parent/'result.json').read_text());parent_sha=file_hash(a.checkpoint)
    if parent_sha!=receipt['checkpoint_sha']:raise ValueError('Candidate checkpoint changed')
    if a.mode=='train':
        if a.qualification is None or a.full_reference is None:raise ValueError('Full qualification required before training')
        gate=json.loads(a.qualification.read_text());full=json.loads(a.full_reference.read_text())
        if gate['status']!='approved_for_full_training' or gate['checkpoint_sha']!=parent_sha:raise ValueError('Candidate not qualified')
        if not full['full_population'] or full['metrics']['nonfinite']:raise ValueError('Incomplete/nonfinite full qualification')
    preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');torch.manual_seed(a.seed);random.seed(a.seed);np.random.seed(a.seed)
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    if saved['contract']['loss_arm'] not in ('joint','joint_calibrated'):raise ValueError('V18 candidate required')
    cal=json.loads(a.calibration.read_text());frozen=verify(a.data);manifest=load_split(a.split)
    if frozen['source_split_hash']!=manifest['manifest_hash'] or frozen['hash']!=cal['data_hash'] or cal['split_hash']!=manifest['manifest_hash']:raise ValueError('Data/calibration mismatch')
    teacher,identity=load_teacher(Path(cal['teacher_path']),manifest['manifest_hash'],'cuda')
    if identity!=cal['teacher'] or identity['contract']['counts']!=manifest['counts']:raise ValueError('Frozen teacher/population changed')
    if a.mode=='train' and full['windows']!=manifest['counts']['validation']:raise ValueError('Full validation count mismatch')
    manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    with lease(a.output):
        if a.mode=='profile':return profile(a,teacher,cal,manifest,saved)
        model=make_model(saved,cal)
        contract=dict(version='phase1-v18-full',source=a.source_revision,config=vars(model.config),arm='scaled_slots_price',
            loss_arm=model.loss_arm,activity_calibration=saved['contract']['activity_calibration'],parent_sha=parent_sha,
            teacher=identity,calibration_sha256=file_hash(a.calibration),split_hash=manifest['manifest_hash'],counts=manifest['counts'],
            epochs=a.epochs,batch_size=a.batch_size,learning_rate=a.learning_rate,min_learning_rate=a.min_learning_rate,warmup_steps=a.warmup_steps,
            weight_decay=.01,seed=a.seed,loss_source_sha=file_hash(Path(__file__).with_name('objectives.py')),
            objective='V18 joint source-valid activity levels and tails on all bars; visible-pair changes; candle body0.5; price0.01; latent MAE retained',
            visibility='16random in3/4 updates,64in1/4',full_reference_sha=file_hash(a.full_reference),
            quality_rule='50%visible raw ratios+25%masked raw ratios+25%direction error; stop after2 development regressions or failed first full-pass improvement',
            parameters=sum(p.numel() for p in model.parameters()),decoder_parameters=sum(p.numel() for p in model.decoder.parameters()))
        if a.resume:contract=resume_contract(contract,a.resume)
        config=dict(model=contract,engineering_revision=a.source_revision,workers=a.workers,graphs=True,metric_graphs=True,output=str(a.output),resume=str(a.resume) if a.resume else None)
        write_json(a.output/(f'resume-{int(time.time())}.json' if a.resume else 'manifest.json'),config)
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False);os.environ.setdefault('WANDB_ENTITY','mehdifaraji')
        reporter=Reporter(a.output,config,mode='online',project='bargpt-phase1-v18-full20')
        if reporter.run is None:raise RuntimeError('Online W&B required')
        try:reporter.finish(train(model,teacher,manifest,a,contract,reporter))
        except BaseException as exc:reporter.finish('failed',repr(exc));raise


if __name__=='__main__':main()
