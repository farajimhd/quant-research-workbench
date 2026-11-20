"""Read-only full Phase1 validation audit of the exact frozen Phase2 teacher."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import torch
from .model import Autoencoder
from .config import Config
from .pipeline import loader,DeviceBatches
from .full_step import forward,InferenceReplay
from .diagnostic_metrics import Diagnostics
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import load_checkpoint,lease
from ..v5.data_v3 import load_split,close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--checkpoint-sha256',required=True)
    p.add_argument('--calibration',type=Path,required=True)
    p.add_argument('--split',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json'))
    p.add_argument('--batch-size',type=int,default=4096);p.add_argument('--workers',type=int,default=16)
    p.add_argument('--limit-batches',type=int,default=0);p.add_argument('--no-graphs',action='store_true')
    a=p.parse_args();a.device='cuda';a.resume=False;a.preparation='raw_gpu';a.seed=731;a.prefetch=4
    a.validation_batch_size=a.batch_size;a.validation_workers=a.workers;a.shard_prefetch=16;a.shard_prefetch_mib=64
    preflight(a);torch.set_num_threads(4);torch.manual_seed(a.seed)
    state=dict(state='initializing',windows=0,started=time.time())
    def status(**kw):state.update(kw,updated=time.time());write_json(a.output/'status.json',state)
    source=None;device=None
    try:
        with lease(a.output):
            if file_hash(a.checkpoint)!=a.checkpoint_sha256:raise ValueError('Checkpoint hash mismatch')
            saved=torch.load(a.checkpoint,map_location='cpu',weights_only=False);saved=load_checkpoint(a.checkpoint,saved['contract'])
            if saved['epoch']!=25:raise ValueError('Expected completed epoch25 teacher')
            if file_hash(a.calibration)!=saved['contract']['calibration_sha256']:raise ValueError('Calibration mismatch')
            manifest=load_split(a.split)
            if manifest['manifest_hash']!=saved['contract']['split_hash']:raise ValueError('Checkpoint validation authority mismatch')
            manifest=attach(manifest,Path(manifest['dataset']));manifest['splits']={}
            write_json(a.output/'authority.json',dict(checkpoint_sha256=a.checkpoint_sha256,source_revision=a.source_revision,
                split_hash=manifest['manifest_hash'],counts=manifest['counts'],config=saved['contract']['config'],
                scope='Reconstruct input, NOT forecast. Masked16 selects hidden targets; pair metrics require both endpoints hidden. All-visible64 measures information retained in exported embedding. Exact-zero direction class. True-reference oracle diagnostic only. Range=(high/low-1)*100.'))
            model=Autoencoder(Config(**saved['contract']['config']),json.loads(a.calibration.read_text())['parameters']).cuda().eval()
            model.load_state_dict(saved['model']);del saved
            source=loader(manifest,a,'validation');device=DeviceBatches(source,'cuda')
            acc={mode:Diagnostics() for mode in ('masked','all_visible')};graphs={};generator=torch.Generator(device='cuda').manual_seed(731)
            n=0;started=time.perf_counter();stopped=False
            with torch.no_grad():
                for i in range(a.limit_batches or 2**63-1):
                    if (a.output/'STOP').exists():stopped=True;break
                    try:b=device.pop()
                    except StopIteration:break
                    for mode,visible in (('masked',16),('all_visible',64)):
                        if not a.no_graphs and len(b.target)==a.batch_size:
                            if mode not in graphs:graphs[mode]=InferenceReplay(model,b,visible,generator)
                            out,x,v,h=graphs[mode](b)
                        else:out,x,v,h=forward(model,b,visible,generator)
                        acc[mode].add(model,out,x,v,h,b,visible)
                    device.advance();n+=len(b.target)
                    if i%25==0:
                        torch.cuda.synchronize();elapsed=time.perf_counter()-started
                        status(state='running',windows=n,total=manifest['counts']['validation'],windows_per_second=n/elapsed,
                               eta_seconds=(manifest['counts']['validation']-n)/(n/elapsed),loader_wait_seconds=device.wait_seconds)
            torch.cuda.synchronize();elapsed=time.perf_counter()-started
            complete=not stopped and not a.limit_batches and n==manifest['counts']['validation']
            if not stopped and not a.limit_batches and not complete:raise ValueError('Validation incomplete')
            result=dict(windows=n,complete=complete,seconds=elapsed,windows_per_second=n/elapsed,metrics={k:v.finish() for k,v in acc.items()})
            write_json(a.output/'results.json',result)
            if any(v['nonfinite'] for v in result['metrics'].values()):raise FloatingPointError('Nonfinite predictions; retained report')
            status(state='complete' if complete else 'bounded' if a.limit_batches else 'stopped',windows=n,seconds=elapsed)
    except BaseException as exc:status(state='failed',error=repr(exc));raise
    finally:
        if device:device.close()
        if source:close_loader(source)


if __name__=='__main__':main()
