"""Matched real-source profiling; compute-only and actual-loader rates kept separate."""
from dataclasses import replace
import gc,time
import numpy as np
import torch
from .model import Autoencoder
from .data import prepare
from .objectives import loss
from .engine import loader,DeviceBatches,autocast
from .replay import CudaStep
from ..v5.encoder_v3 import EncoderConfig
from ..v5.encoder_v4 import MaskedAutoencoderV4
from ..v5.pretraining_v3 import prepare_batch,LocalReferencePolicy
from ..v5.data_v3 import EligibleStream,make_loader,close_loader
from ..v5.checkpoint_transition_v1 import state_hash


def run(manifest,stats,config,args,status,raw):
    if raw is None:raise ValueError('Profiles require fresh bounded source calibration samples')
    stats_device=stats.to(args.device);trials=[];parity={};pipeline_parity={}
    batches=[int(x) for x in args.batches.split(',')]
    if not batches or any(x<1 or x>8192 for x in batches):raise ValueError('Profile batch sizes must be1..8192')
    trial=0;total_trials=len(batches)*(2 if args.device=='cuda' and args.precision=='bf16' else 1)+5
    def synchronize():
        if args.device=='cuda':torch.cuda.synchronize()
    for count in batches:
        rows=raw[np.arange(count)%len(raw)]
        raw_args=(torch.from_numpy(rows['values'].copy()),torch.from_numpy(rows['valid'].astype(bool)),
                  torch.from_numpy(rows['end_us'].copy()),torch.from_numpy(rows['timeframe_us'].copy()))
        cpu=prepare(*raw_args,stats,torch.Generator().manual_seed(17),config);batch=cpu.to(args.device)
        for captured in ((False,True) if args.device=='cuda' and args.precision=='bf16' else (False,)):
            trial+=1
            torch.manual_seed(71);model=Autoencoder(config).to(args.device);model.train()
            optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01,fused=args.device=='cuda')
            replay=CudaStep(model,count,stats_device) if captured else None
            synchronize();begin=time.perf_counter();measured=None
            for step in range(args.warmup+args.steps):
                if step==args.warmup:synchronize();measured=time.perf_counter()
                if replay is not None:value,terms,norm=replay(batch)
                else:
                    optimizer.zero_grad(set_to_none=True)
                    with autocast(args):_,p,r=model.from_batch(batch);value,terms=loss(p,r,batch,stats_device)
                    value.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite profile')
                optimizer.step()
                status(stage='compute profile',profile_trial=trial,profile_trials=total_trials,
                       profile_done=step+1,profile_total=args.warmup+args.steps,
                       configuration=f'V5 D{config.width} B{count} '+('captured' if captured else 'eager'),train=dict(loss=float(value.detach()),
                           price_loss=float(terms[0].detach()),activity_loss=float(terms[1].detach()),reference_loss=float(terms[2].detach())))
            synchronize();seconds=time.perf_counter()-measured
            h=dict(model=state_hash(model.state_dict()),optimizer=state_hash(optimizer.state_dict()),
                   cpu_rng=state_hash(torch.get_rng_state()),cuda_rng=state_hash(torch.cuda.get_rng_state_all()) if args.device=='cuda' else '')
            parity[(count,captured)]=h
            trials.append(dict(kind='student_compute',batch=count,captured=captured,seconds=seconds,
                windows_per_second=count*args.steps/seconds,loss=float(value.detach()),encoder_tokens=config.visible_bars+1))
            status(profile_partial=dict(trials=trials))
            del replay,optimizer,model;gc.collect()
            if args.device=='cuda':torch.cuda.empty_cache()
    exact=all(parity[(b,False)]==parity.get((b,True),parity[(b,False)]) for b in batches)
    candidates=[t for t in trials if not t['captured'] or exact]
    fastest=max(candidates,key=lambda x:x['windows_per_second'])
    selected=replace_args(args,batch_size=fastest['batch'])
    for version in ('v4','v5-eager','v5-captured'):
        trial+=1
        if version=='v5-captured' and not (exact and args.device=='cuda' and args.precision=='bf16'):continue
        torch.manual_seed(71)
        if version=='v4':
            model=MaskedAutoencoderV4(EncoderConfig(width=512,layers=config.layers,heads=8,feedforward=2048,
                decoder_width=config.decoder_width,decoder_layers=1,decoder_heads=config.heads,dropout=0.,attention_dropout=0.)).to(args.device)
            stream=EligibleStream(manifest,'train',LocalReferencePolicy(),selected.batch_size,seed=17)
            stream.shard_prefetch=args.shard_prefetch;stream.shard_prefetch_bytes=args.shard_prefetch_mib*2**20
            source=make_loader(stream,args.workers,args.prefetch)
        else:model=Autoencoder(config).to(args.device);source=loader(manifest,stats,config,selected,'train')
        model.train();optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=.01,fused=args.device=='cuda')
        device=None;captured=None;compute=0.;seen=0;measured=None
        try:
            # DataLoader pinning can synchronize CUDA. Finish capture first.
            if version=='v5-captured':captured=CudaStep(model,selected.batch_size,stats_device)
            if version=='v4':
                from ..v5.engine_v3 import CudaBatches
                device=CudaBatches(source) if args.device=='cuda' else CpuBatches(source)
            else:device=DeviceBatches(source,args.device)
            for step in range(args.warmup+args.steps):
                if step==args.warmup:synchronize();measured=time.perf_counter()
                b=device.pop();synchronize();start=time.perf_counter()
                if captured is not None and len(b.target)==selected.batch_size:value,terms,norm=captured(b)
                else:
                    optimizer.zero_grad(set_to_none=True)
                    with autocast(args):
                        if version=='v4':
                            from ..v5.trainer_v1 import reconstruction_loss
                            _,p=model(b.visible,b.valid,b.feature_ids,b.hidden_ids,b.time,b.time_valid)
                            value=reconstruction_loss(p,b)[0]
                        else:_,p,r=model.from_batch(b);value,terms=loss(p,r,b,stats_device)
                    value.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite actual-loader profile')
                optimizer.step();synchronize();elapsed=time.perf_counter()-start
                if step>=args.warmup:
                    compute+=elapsed;seen+=len(b.visible)
                device.advance()
                status(stage='actual loader profile',configuration=version,profile_trial=trial,profile_trials=total_trials,profile_done=step+1,
                    profile_total=args.warmup+args.steps,step=step+1,
                    samples_per_second=seen/max(.001,time.perf_counter()-measured) if measured is not None else None,
                    gpu_used_mib=torch.cuda.memory_allocated()/2**20 if args.device=='cuda' else None)
            synchronize();seconds=time.perf_counter()-measured
            trials.append(dict(kind='actual_loader',version=version,batch=selected.batch_size,windows=seen,
                seconds=seconds,windows_per_second=seen/seconds,compute_seconds=compute,
                loader_wait_seconds=device.wait_seconds,loss=float(value.detach())))
            status(profile_partial=dict(trials=trials))
            if version!='v4':pipeline_parity[version]=dict(model=state_hash(model.state_dict()),
                optimizer=state_hash(optimizer.state_dict()),cpu_rng=state_hash(torch.get_rng_state()),
                cuda_rng=state_hash(torch.cuda.get_rng_state_all()) if args.device=='cuda' else '')
        finally:
            if hasattr(device,'close'):device.close()
            close_loader(source);del optimizer,model,captured;gc.collect()
            if args.device=='cuda':torch.cuda.empty_cache()
    rows=raw[np.arange(selected.batch_size)%len(raw)]
    full=prepare(torch.from_numpy(rows['values'].copy()),torch.from_numpy(rows['valid'].astype(bool)),
        torch.from_numpy(rows['end_us'].copy()),torch.from_numpy(rows['timeframe_us'].copy()),
        stats,torch.Generator().manual_seed(17),config,full=True).to(args.device)
    for version in ('v4','v5'):
        trial+=1
        if version=='v4':
            from ..v5.encoder_v3 import ColumnEncoderV3
            encoder=ColumnEncoderV3(EncoderConfig(width=512,layers=config.layers,heads=8,
                feedforward=2048,dropout=0.,attention_dropout=0.)).to(args.device).eval()
            encode=lambda:encoder.encode_full(full.visible[...,:18],full.valid[...,:18],full.visible[...,18:],full.valid[...,18:])
        else:
            encoder=Autoencoder(config).to(args.device).eval();encode=lambda:encoder.encode_full(full)
        with torch.no_grad():
            for step in range(args.warmup+args.steps):
                if step==args.warmup:synchronize();measured=time.perf_counter()
                with autocast(args):z=encode()
                status(stage='all-visible encoder profile',configuration=version,profile_trial=trial,
                    profile_trials=total_trials,profile_done=step+1,profile_total=args.warmup+args.steps)
            synchronize();seconds=time.perf_counter()-measured
            if not torch.isfinite(z).all():raise FloatingPointError('Nonfinite all-visible embedding')
        trials.append(dict(kind='inference_compute',version=version,batch=selected.batch_size,
            seconds=seconds,windows_per_second=args.steps*selected.batch_size/seconds,
            encoder_tokens=21 if version=='v4' else 65))
        del encoder;gc.collect()
        if args.device=='cuda':torch.cuda.empty_cache()
    pipeline_exact=pipeline_parity.get('v5-eager')==pipeline_parity.get('v5-captured') if 'v5-captured' in pipeline_parity else None
    return dict(trials=trials,capture_exact_full_state=exact,pipeline_capture_exact_full_state=pipeline_exact,
                recommended_batch=selected.batch_size,
                recommended_capture=exact and pipeline_exact is True and args.device=='cuda' and args.precision=='bf16',
                compute_scope='repeated fixed batch of real source windows; excludes normalization/loader',
                pipeline_scope='actual frozen balanced split; respective objectives; original512-wide V4 baseline; equal depth/dropout0',
                encoder_width=config.width,embedding_width=512,
                parity={str(k):v for k,v in parity.items()},pipeline_parity=pipeline_parity)


class CpuBatches:
    def __init__(self,source):self.source=iter(source);self.wait_seconds=0.;self.advance()
    def advance(self):
        began=time.perf_counter();self.next=next(self.source,None);self.wait_seconds+=time.perf_counter()-began
    def pop(self):
        if self.next is None:raise StopIteration
        b=self.next;self.next=None;return b


def replace_args(args,**values):
    from argparse import Namespace
    return Namespace(**(vars(args)|values))
