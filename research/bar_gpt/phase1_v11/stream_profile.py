"""Bounded full-authority pipeline measurement, never a full training launch."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import torch
from .config import Config
from .model import Autoencoder
from .full_step import Replay
from ..phase1_v7.full_data import loader,gpu_inputs
from ..phase1_v5_raw.engine import DeviceBatches
from ..v5.data_v3 import load_split,close_loader
from ..v5.loader_index_v1 import attach
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import write_json,file_hash
from ..phase2_v2.train import load_checkpoint


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--calibration',type=Path,required=True);p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--split',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--workers',type=int,default=16);p.add_argument('--batch-size',type=int,default=1024);p.add_argument('--steps',type=int,default=100)
    p.add_argument('--mode',choices=['training','validation'],default='training');p.add_argument('--learning-rate',type=float,default=1e-5)
    p.add_argument('--validation-batch-size',type=int,default=0);p.add_argument('--validation-workers',type=int,default=-1)
    p.add_argument('--validation-graphs',action='store_true')
    p.add_argument('--prefetch',type=int,default=4);p.add_argument('--shard-prefetch',type=int,default=16);p.add_argument('--shard-prefetch-mib',type=int,default=64)
    p.add_argument('--seed',type=int,default=110731,help='Use the intended full-run seed for representative traversal profiling')
    a=p.parse_args();a.device='cuda';a.resume=False;a.preparation='raw_gpu'
    if not 0<=a.workers<=32 or not 1<=a.prefetch<=4 or not 10<=a.steps<=5000:raise ValueError('Bounded profile required')
    preflight(a);torch.set_num_threads(4);torch.manual_seed(a.seed);started=time.monotonic()
    manifest=load_split(a.split);manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
    calibration=json.loads(a.calibration.read_text());saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False)
    saved=load_checkpoint(a.checkpoint,saved['contract'])
    if saved['contract']['calibration_sha256']!=file_hash(a.calibration):raise ValueError('Calibration differs')
    frozen=json.loads(Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset/manifest.json').read_text())
    if frozen['hash']!=saved['contract']['data_hash'] or frozen['source_split_hash']!=manifest['manifest_hash']:raise ValueError('Full split/calibration dependency differs')
    m=Autoencoder(Config(**saved['contract']['config']),calibration['parameters']).cuda();m.load_state_dict(saved['model']);m.train()
    if a.mode=='validation':
        from .full_validation import evaluate
        def status(**kw):
            if time.monotonic()-started>300:raise TimeoutError('Five-minute validation profile deadline')
            write_json(a.output/'progress.json',kw)
        result=evaluate(m,manifest,a,status,limit=a.steps)
        if result is None:raise InterruptedError('Validation profile stopped')
        result.update(state='complete',windows_per_second=result['windows']/result['seconds'],steady_windows_per_second=result['windows']/max(.001,result['seconds']-result['startup_seconds']),
            total_wall_seconds=time.monotonic()-started,validation_graphs=a.validation_graphs,reserved_mib=torch.cuda.memory_reserved()/2**20,
            source_revision=a.source_revision,checkpoint_sha256=file_hash(a.checkpoint),workers=a.workers,batch=a.validation_batch_size or a.batch_size,seed=a.seed)
        write_json(a.output/'result.json',result);print(json.dumps({k:v for k,v in result.items() if k!='metrics'}),flush=True);return
    opt=torch.optim.AdamW(m.parameters(),lr=a.learning_rate,fused=True);source=loader(manifest,a,'train');device=None
    try:
        device=DeviceBatches(source,'cuda');b=device.pop();replay=Replay(m,b)
        startup=time.monotonic()-started;windows=0;begin=time.monotonic();wait=device.wait_seconds
        for step in range(a.steps):
            if time.monotonic()-started>300:raise TimeoutError('Five-minute stream profile deadline')
            if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():raise InterruptedError('Human STOP')
            if step:b=device.pop()
            loss,norm,_=replay(b);device.advance();opt.step();windows+=len(b.target)
            if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Stream profile finite gate')
            if step%20==0:write_json(a.output/'progress.json',dict(stage='full stream profile',step=step+1,elapsed_seconds=time.monotonic()-started,deadline_seconds=300,
                windows_per_second=windows/max(time.monotonic()-begin,.001),loss=float(loss)))
        torch.cuda.synchronize();seconds=time.monotonic()-begin
        result=dict(state='complete',windows=windows,seconds=seconds,windows_per_second=windows/seconds,startup_seconds=startup,
            loader_wait_seconds=device.wait_seconds-wait,workers=a.workers,prefetch=a.prefetch,batch=a.batch_size,reserved_mib=torch.cuda.memory_reserved()/2**20,
            source_revision=a.source_revision,checkpoint_sha256=file_hash(a.checkpoint),split_hash=manifest['manifest_hash'],seed=a.seed,scope='Bounded prefix of full certified training stream; updates discarded, checkpoint unchanged.')
        write_json(a.output/'result.json',result);print(json.dumps(result),flush=True)
    finally:
        if device:device.close()
        close_loader(source)


if __name__=='__main__':main()
