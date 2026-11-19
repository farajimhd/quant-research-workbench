import time
from types import SimpleNamespace
import torch
from ..phase2_v2.train import rng_state,restore_rng,autocast
from ..phase2_v2.device_pipeline import device_batches
from .data import batches,target_size
from .metrics import Metrics,ValidationMetrics
from .cuda_step import CudaStep
from ..v5.shards.phase1_contract import CHANNELS


@torch.no_grad()
def evaluate(model,units,teacher,teacher_sha,args,mean,reporter,step):
    state=rng_state();was=model.training;model.eval();metrics=Metrics(args.device);native=ValidationMetrics(args.device)
    source=device_batches(batches(units,1,teacher,teacher_sha,args),args.device,args.gpu_prefetch)
    started=time.perf_counter()
    try:
        for tokens,meta,target,_,_,_ in source:
            if (args.output/'STOP').exists():return None
            with autocast(args):_,p=model(tokens,meta)
            if not torch.isfinite(p).all():raise FloatingPointError('Nonfinite forecast')
            metrics.add(p,target);native.add(p,target,mean)
            seconds=time.perf_counter()-started;rate=metrics.count/max(seconds,.001)
            reporter.update(stage='validation',done=metrics.count,step=step,validation_samples_per_second=rate,validation_seconds=seconds)
        result=dict(metrics.result(),**native.result(),seconds=time.perf_counter()-started)
        if 'horizon_counts' in result:
            for bar,errors in enumerate(result['log_mae_by_horizon_channel']):
                for c,(channel,value) in enumerate(zip(CHANNELS,errors)):
                    result[f'horizon/bar_{bar+1}/log_mae/{channel}']=value if result['horizon_counts']['counts'][bar][c] else None
        reporter.update(latest_validation_loss=result['loss'],latest_validation_step=step)
        return result
    finally:
        try:source.close();native.native.pool.shutdown(wait=True)
        finally:restore_rng(state);model.train(was)


def train(model,teacher,teacher_sha,units,contract,args,reporter):
    from .full_pass import train_full_pass
    return train_full_pass(model,teacher,teacher_sha,units,contract,args,reporter,
        components=SimpleNamespace(batches=batches,Metrics=Metrics,evaluate=evaluate,CudaStep=CudaStep,target_size=target_size))
