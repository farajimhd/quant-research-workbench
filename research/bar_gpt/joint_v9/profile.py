"""Bounded cached-pipeline batch sweep; never modifies a scientific checkpoint."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,time
from pathlib import Path
import numpy as np
import torch
from .data import load,cache
from .model import Config,JointModel
from .train import loss_for
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--batch-sizes',type=int,nargs='+',default=[1024,2048,4096,8192])
    p.add_argument('--sample-size',type=int,default=8192);p.add_argument('--steps',type=int,default=25)
    a=p.parse_args();a.resume=False;preflight(a);started=time.perf_counter();deadline=time.monotonic()+600
    if min(a.batch_sizes)<=0 or not 1<=a.steps<=100 or not 1<=a.sample_size<=32768:raise ValueError('Invalid bounded profile')
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    checksum=file_hash(a.checkpoint);state=torch.load(a.checkpoint,map_location='cpu',weights_only=False);identity=state['identity']
    rows,manifest=load(a.data,('train',))
    if identity['dataset_hash']!=manifest['hash']:raise ValueError('Dataset identity mismatch')
    ids=np.random.default_rng(917).choice(len(rows['train']),min(a.sample_size,len(rows['train'])),replace=False)
    data={k:v.to(a.device) for k,v in cache(rows['train'][ids]).items()};config=Config(**identity['config'])
    calibration={k:torch.tensor(v) for k,v in identity['parameters'].items()};results=[]
    startup=time.perf_counter()-started
    for bs in a.batch_sizes:
        if time.monotonic()>deadline:raise TimeoutError('Profile deadline')
        torch.manual_seed(917);model=JointModel(config,**calibration).to(a.device);model.load_state_dict(state['model']);model.train()
        if identity.get('freeze_encoder'):model.encoder.requires_grad_(False);model.encoder.eval()
        optimizer=torch.optim.AdamW(model.parameters(),lr=1e-5,fused=a.device=='cuda');generator=torch.Generator(device=a.device).manual_seed(917)
        loss=batch=parts=ix=None
        try:
            for step in range(5+a.steps):
                if time.monotonic()>deadline:raise TimeoutError('Profile deadline')
                if step==5:
                    if a.device=='cuda':torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
                    begin=time.perf_counter()
                ix=torch.randint(len(ids),(bs,),device=a.device,generator=generator);batch={k:v[ix] for k,v in data.items()}
                optimizer.zero_grad(set_to_none=True)
                loss,parts=loss_for(model,batch,identity['reconstruction_encoder_weight'],identity.get('masked_reconstruction_probability',0),identity.get('forecast_weight',1))
                if not torch.isfinite(loss):raise FloatingPointError('Nonfinite profile loss')
                loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True);optimizer.step()
            if a.device=='cuda':torch.cuda.synchronize()
            seconds=time.perf_counter()-begin
            results.append(dict(batch=bs,state='complete',steps=a.steps,seconds=seconds,origins_per_second=bs*a.steps/seconds,
                peak_bytes=torch.cuda.max_memory_allocated() if a.device=='cuda' else 0))
        except torch.cuda.OutOfMemoryError:
            results.append(dict(batch=bs,state='out-of-memory'))
        finally:
            del loss,batch,parts,ix,model,optimizer
            if a.device=='cuda':torch.cuda.empty_cache()
        write_json(a.output/'progress.json',dict(configurations=results,elapsed_seconds=time.perf_counter()-started))
        print(results[-1],flush=True)
    if file_hash(a.checkpoint)!=checksum:raise ValueError('Scientific checkpoint changed')
    if time.monotonic()>deadline:raise TimeoutError('Profile deadline')
    complete=[r for r in results if r['state']=='complete']
    if not complete:raise RuntimeError('No batch completed')
    write_json(a.output/'results.json',dict(state='complete',source_revision=a.source_revision,checkpoint_sha256=checksum,
        checkpoint_path=str(a.checkpoint),data_path=str(a.data),config=identity['config'],device=a.device,
        dataset_hash=manifest['hash'],startup_seconds=startup,total_seconds=time.perf_counter()-started,configurations=results,
        fastest=max(complete,key=lambda r:r['origins_per_second']),cache_origins=len(ids),
        scope='GPU-resident sampled training pipeline, including gathering, objective, backward, finite/gradient checks and optimizer. No streaming loader or full-data I/O claim. Disposable copies; original checkpoint unchanged.'))


if __name__=='__main__':main()
