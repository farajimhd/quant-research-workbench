"""Bounded actual-loader profiling; never updates a production run."""
import time
import torch
from .model import Autoencoder
from .engine import loader,DeviceBatches,autocast
from .objectives import loss
from .replay import CudaStep
from ..v5.data_v3 import close_loader


def run(manifest,stats,config,args,status,raw):
    trials=[];device_stats=stats.to(args.device)
    for count in [int(x) for x in args.batches.split(',')]:
        selected=type(args)(**dict(vars(args),batch_size=count))
        torch.manual_seed(args.seed);model=Autoencoder(config).to(args.device).train()
        optimizer=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=.01,
                                    fused=args.device=='cuda')
        replay=CudaStep(model,count,device_stats) if args.cuda_graph else None
        source=loader(manifest,stats,config,selected,'train');device=None;seen=0;started=None
        try:
            device=DeviceBatches(source,args.device)
            for index in range(args.warmup+args.steps):
                if index==args.warmup:
                    if args.device=='cuda':torch.cuda.synchronize()
                    started=time.perf_counter();initial_wait=device.wait_seconds
                b=device.pop()
                if replay is not None and len(b.target)==count:value,terms,norm=replay(b)
                else:
                    optimizer.zero_grad(set_to_none=True)
                    with autocast(args):_,p=model.from_batch(b);value,terms=loss(p,b,device_stats)
                    value.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                device.advance()
                if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite raw profile')
                optimizer.step()
                if index>=args.warmup:seen+=len(b.target)
                status(stage='raw actual-loader profile',profile_done=index+1,profile_total=args.warmup+args.steps,
                    train=dict(loss=float(value.detach()),price_loss=float(terms[0].detach()),activity_loss=float(terms[1].detach())))
            if args.device=='cuda':torch.cuda.synchronize()
            seconds=time.perf_counter()-started
            trials.append(dict(batch=count,windows=seen,seconds=seconds,windows_per_second=seen/seconds,
                               loader_wait_seconds=device.wait_seconds-initial_wait,captured=bool(replay)))
        finally:
            if device:device.close()
            close_loader(source)
        del replay,optimizer,model
    return dict(trials=trials,scope='bounded raw baseline actual-loader profile; not full training')
