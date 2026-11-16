from types import SimpleNamespace
import torch
from ..phase2_v2.train import rng_state,restore_rng,autocast
from ..phase2_v2.device_pipeline import device_batches
from ..phase2_v2.full_pass import train_full_pass
from .data import batches,target_size
from .metrics import Metrics,RawMetrics
from .cuda_step import CudaStep
from .early_stop import SessionGuard


@torch.no_grad()
def evaluate(model,units,teacher,teacher_sha,args,mean,reporter,step):
    state=rng_state();was_training=model.training;model.eval()
    metrics=Metrics(args.device);raw=RawMetrics(args.device)
    source=device_batches(batches(units,1,teacher,teacher_sha,args),args.device,getattr(args,'gpu_prefetch',False))
    try:
        for tokens,meta,target,_,_,_ in source:
            if (args.output/'STOP').exists():return None
            with autocast(args):_,predicted=model(tokens,None)
            if not torch.isfinite(predicted).all():raise FloatingPointError('Nonfinite direct bar/reference prediction')
            metrics.add(predicted,target);raw.add(predicted,target)
            reporter.update(stage='validation',done=metrics.count,step=step)
        return dict(metrics.result(mean),**raw.result())
    finally:
        try:source.close()
        finally:restore_rng(state);model.train(was_training)


def train(model,teacher,teacher_sha,units,contract,args,reporter):
    if args.validation_every<=0:raise ValueError('Full-pass validation cadence is mandatory')
    guard=SessionGuard(units,args)
    components=SimpleNamespace(batches=batches,Metrics=Metrics,evaluate=evaluate,CudaStep=CudaStep,
        target_size=target_size,learning_guard=guard.review)
    return train_full_pass(model,teacher,teacher_sha,units,contract,args,reporter,components=components)
