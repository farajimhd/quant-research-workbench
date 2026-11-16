"""Bounded loader, CUDA transfer, masked evaluation and full-state checkpoints."""
from dataclasses import fields,replace
from contextlib import nullcontext
from collections import deque
from pathlib import Path
import json,time,math
import numpy as np
import torch
from .data import Batch,Stream
from .objectives import loss,reconstruct
from ..v5.data_v3 import make_loader,close_loader
from ..v5.shards.contract import FRAMES,write_json
from ..v5.shards.phase1_contract import CHANNELS
from ..v5.price_relative_v3 import PriceRelative
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint

FRAME_NAMES=('100ms','1s','10s','1m','1h','daily')


def report(reporter,metrics,step):
    if reporter is not None:
        # finish()/resume may have committed the last training step already.
        # Retain its actual optimizer step without writing to a closed SDK row.
        reporter.log(dict(metrics,optimizer_step=step),step=max(step,reporter.step))


def loader(manifest,stats,config,args,split,epoch=0,full=False,native=False):
    (args.output/'workers'/split).mkdir(parents=True,exist_ok=True)
    stream=Stream(manifest,split,stats,args.batch_size,seed=args.seed,epoch=epoch,
        config=config,full=full,native=native,telemetry=str(args.output/'workers'/split))
    stream.shard_prefetch=args.shard_prefetch;stream.shard_prefetch_bytes=args.shard_prefetch_mib*2**20
    stream.prepare_threads=getattr(args,'prepare_threads',1)
    return make_loader(stream,args.workers,args.prefetch,ordered=True)


class DeviceBatches:
    def __init__(self,source,device):
        self.source=iter(source);self.device=torch.device(device)
        self.stream=torch.cuda.Stream(device=self.device) if self.device.type=='cuda' else None
        self.wait_seconds=0.;self.next=None;self.advance()

    def advance(self):
        began=time.perf_counter()
        try:batch=next(self.source)
        except StopIteration:self.next=None;return
        self.wait_seconds+=time.perf_counter()-began
        with torch.cuda.stream(self.stream) if self.stream is not None else nullcontext():
            self.next=batch.to(self.device,non_blocking=self.stream is not None)
            if self.stream is not None:self.ready=torch.cuda.Event();self.ready.record(self.stream)

    def pop(self):
        if self.next is None:raise StopIteration
        b=self.next
        if self.stream is not None:
            current=torch.cuda.current_stream(self.device);current.wait_event(self.ready)
            for f in fields(b):getattr(b,f.name).record_stream(current)
        self.next=None;return b

    def close(self):
        if self.stream is not None:self.stream.synchronize()


def autocast(args):
    return torch.autocast('cuda',dtype=torch.bfloat16) if args.device=='cuda' and args.precision=='bf16' else nullcontext()


def masked_from_full(b,config,generator):
    order=torch.rand(len(b.target),64,generator=generator).argsort(1)
    vi=order[:,:config.visible_bars].sort(1).values.to(b.target.device)
    hi=order[:,config.visible_bars:].sort(1).values.to(b.target.device)
    take=lambda x,ids:x.gather(1,ids[...,None].expand(-1,-1,x.shape[-1]))
    return replace(b,visible=take(b.visible,vi),valid=take(b.valid,vi),positions=vi,
        target=take(b.target,hi),target_valid=take(b.target_valid,hi),target_positions=hi,
        native_raw=take(b.native_raw,hi))


@torch.no_grad()
def evaluate(model,source,args,stats,status,step,*,limit=0):
    model.eval();stats=stats.to(args.device);device=DeviceBatches(source,args.device)
    sums=torch.zeros(3,device=args.device,dtype=torch.float64);full_sums=sums.clone();n=0
    raw_sum=torch.zeros(6,18,device=args.device,dtype=torch.float64);raw_n=torch.zeros_like(raw_sum)
    raw_bad=torch.zeros_like(raw_sum);ref_error=torch.zeros(7,device=args.device,dtype=torch.float64)
    ref_n=ref_error.clone();price=PriceRelative();frames=torch.tensor(FRAMES,device=args.device)
    ref_bad=ref_error.clone();negative_activity=torch.zeros(6,device=args.device,dtype=torch.int64)
    try:
        for index in range(limit or 2**63-1):
            if (args.output/'STOP').exists():return None
            try:b=device.pop()
            except StopIteration:break
            with autocast(args):
                _,p,r=model.from_batch(b);_,fv=loss(p,r,b,stats)
                masked=masked_from_full(b,model.config,torch.Generator().manual_seed(args.seed+index))
                _,mp,mr=model.from_batch(masked);_,mv=loss(mp,mr,masked,stats)
            if not (torch.isfinite(p).all()&torch.isfinite(r).all()):raise FloatingPointError('Nonfinite validation prediction')
            raw,refs=reconstruct(p,r,stats);truth=b.native_raw.double();mask=b.target_valid
            errors=(raw-truth).abs();ok=torch.isfinite(errors);frame=(b.frames[:,None]==frames).long().argmax(1)
            if not (b.frames[:,None]==frames).any(1).all():raise ValueError('Unknown validation timeframe')
            for f in range(6):
                selected=(frame==f)[:,None,None]&mask
                raw_sum[f]+=torch.where(selected&ok,errors,0).sum((0,1));raw_n[f]+=selected.sum((0,1));raw_bad[f]+=(selected&~ok).sum((0,1))
            actual=b.native_reference
            re=(refs-actual).abs();rf=torch.isfinite(re)
            ref_error+=torch.where(b.reference_valid&rf,re,0).sum(0);ref_n+=b.reference_valid.sum(0)
            ref_bad+=(b.reference_valid&~rf).sum(0)
            negative_activity+=((raw[...,[4,5,10,11,16,17]]<0)&mask[...,[4,5,10,11,16,17]]).sum((0,1))
            price.add(errors.transpose(1,2),truth.transpose(1,2),mask.transpose(1,2),frame)
            count=len(b.target);sums+=mv.double()*count;full_sums+=fv.double()*count;n+=count
            device.advance();status(stage='validation',step=step,validation_done=n)
    finally:device.close()
    if not n:raise ValueError('Empty V5 validation population')
    result=dict(origins=n,masked_objective=float(sums.mean()/n),
        masked_families=(sums/n).cpu().tolist(),all_visible_families=(full_sums/n).cpu().tolist(),
        full_raw_mae={FRAME_NAMES[f]:{name:float(raw_sum[f,c]/raw_n[f,c]) if raw_n[f,c]>0 and raw_bad[f,c]==0 else None
            for c,name in enumerate(CHANNELS)} for f in range(6)},
        reference_raw_mae=[float(ref_error[i]/ref_n[i]) if ref_n[i]>0 and ref_bad[i]==0 else None for i in range(7)],
        reference_nonfinite=int(ref_bad.sum()),negative_activity_predictions=negative_activity.cpu().tolist(),
        raw_nonfinite=int(raw_bad.sum()),full_population=not bool(limit))
    result.update(price.metrics('full_raw_mae',FRAME_NAMES,CHANNELS));return result


def train(model,manifest,stats,contract,args,status):
    optimizer=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=.01,
                                fused=args.device=='cuda')
    epoch=step=cursor=origins=epoch_origins=0;phase='train';history=[];best=float('inf')
    if args.resume:
        saved=load_checkpoint(args.resume,contract);model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        epoch,step,origins,phase=(saved[k] for k in ('epoch','step','origins','phase'))
        if saved['cursor'][0]!=epoch:raise ValueError('Epoch/cursor mismatch')
        cursor=saved['cursor'][1];epoch_origins=saved['epoch_origins']
        history,best=saved['history'],saved['best'];restore_rng(saved['rng'])
    stats_device=stats.to(args.device)
    def checkpoint(name):
        save_checkpoint(args.output/name,dict(contract=contract,model=model.state_dict(),optimizer=optimizer.state_dict(),
            epoch=epoch,step=step,cursor=(epoch,cursor),origins=origins,epoch_origins=epoch_origins,
            phase=phase,history=history,best=best,rng=rng_state(),
            scheduler=dict(epoch=epoch,updates=step,peak=args.learning_rate,floor=args.min_learning_rate,warmup=args.warmup_updates)))
    reporter=None
    if args.wandb_mode=='online':
        from research.mlops.env import discover_env_files,load_env_files
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        import wandb
        identity_path=args.output/'wandb-identity.json'
        identity=json.loads(identity_path.read_text()) if identity_path.exists() else None
        if args.resume and identity is None:raise ValueError('Online resume requires original W&B identity')
        reporter=wandb.init(project='bargpt-bar-mae-v5',name=args.output.name,id=identity['id'] if identity else None,
            resume='must' if identity else None,config=contract,dir=str(args.output))
        if identity is None:write_json(identity_path,dict(id=reporter.id,name=reporter.name,url=reporter.url))
    try:
        if reporter:
            from .reporting import configure,publish
            configure(reporter)
            for completed in history:
                publish(reporter,completed['validation'],completed['epoch'],completed['step'],args.output)
        if not args.resume:checkpoint('checkpoint_last.pt')
        if args.resume:
            status(state='running',stage=phase,epoch=epoch+1,step=step,
                   epoch_done=epoch_origins,origins=origins)
        replay=None
        while epoch<args.epochs:
            if phase=='train':
                model.train()
                from .replay import CudaStep
                # Capture BEFORE starting pin-memory threads. cudaHostAlloc may
                # synchronize the device and invalidate an active graph capture.
                # A graph owns its backward buffers and stream dependencies for
                # this model's lifetime. Reuse it after validation instead of
                # recapturing while old graph-backed gradients still exist.
                if args.cuda_graph and replay is None:
                    replay=CudaStep(model,args.batch_size,stats_device)
                source=loader(manifest,stats,model.config,args,'train',epoch);device=None
                try:
                    # Deterministic replay preserves the worker lane ordering and augmentation seeds.
                    import itertools
                    device=DeviceBatches(itertools.islice(source,cursor,None),args.device)
                    started=time.perf_counter();seen=0;initial_wait=device.wait_seconds
                    recent=deque([(started,0)])
                    while True:
                        if (args.output/'STOP').exists():checkpoint('checkpoint_last.pt');return 'stopped'
                        try:b=device.pop()
                        except StopIteration:break
                        peak=args.learning_rate;floor=args.min_learning_rate
                        lr=floor+(peak-floor)*.5*(1+math.cos(math.pi*epoch/max(1,args.epochs-1)))
                        if step<args.warmup_updates:lr=floor+(peak-floor)*(step+1)/max(1,args.warmup_updates)
                        optimizer.param_groups[0]['lr']=lr
                        if replay is not None and len(b.target)==args.batch_size:value,terms,norm=replay(b)
                        else:
                            optimizer.zero_grad(set_to_none=True)
                            with autocast(args):_,p,r=model.from_batch(b);value,terms=loss(p,r,b,stats_device)
                            value.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                        # Request/transfer the next independent batch while the
                        # current GPU step is still running. The finite check
                        # still completes BEFORE applying this optimizer update.
                        device.advance()
                        if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('V5 nonfinite loss/gradient')
                        optimizer.step();step+=1;cursor+=1;origins+=len(b.target);seen+=len(b.target);epoch_origins+=len(b.target)
                        if step%args.log_every==0:
                            now=time.perf_counter();recent.append((now,seen))
                            while len(recent)>2 and now-recent[1][0]>=60:recent.popleft()
                            recent_rate=(seen-recent[0][1])/max(.001,now-recent[0][0])
                            metrics=dict(loss=float(value.detach()),price_loss=float(terms[0].detach()),activity_loss=float(terms[1].detach()),reference_loss=float(terms[2].detach()),
                                gradient_norm=float(norm),learning_rate=lr,
                                recent_samples_per_second=recent_rate,
                                loader_wait_seconds=device.wait_seconds-initial_wait)
                            with (args.output/'metrics.jsonl').open('a') as f:f.write(json.dumps(dict(step=step,**metrics))+'\n')
                            status(state='running',stage='training',step=step,epoch=epoch+1,epoch_done=epoch_origins,origins=origins,
                                samples_per_second=seen/max(.001,time.perf_counter()-started),
                                recent_samples_per_second=recent_rate,
                                loader_wait_seconds=device.wait_seconds-initial_wait,train=metrics)
                            report(reporter,metrics,step)
                        if step%getattr(args,'checkpoint_every',1000)==0:checkpoint('checkpoint_last.pt')
                        if args.train_steps and cursor>=args.train_steps:break
                    phase='validation';checkpoint('checkpoint_last.pt')
                finally:
                    if device:device.close()
                    close_loader(source)
            status(state='validating',stage='validation',epoch=epoch+1,step=step,
                   epoch_done=epoch_origins,origins=origins,validation_done=0)
            source=loader(manifest,stats,model.config,args,'validation',epoch,full=True,native=True)
            try:validation=evaluate(model,source,args,stats,status,step,limit=args.validation_steps)
            finally:close_loader(source)
            if validation is None:checkpoint('checkpoint_last.pt');return 'stopped'
            improved=validation['masked_objective']<best;best=min(best,validation['masked_objective'])
            history.append(dict(epoch=epoch+1,step=step,validation=validation,
                                complete_training_pass=not bool(args.train_steps)))
            write_json(args.output/'results.json',history)
            report(reporter,{'validation/'+k:v for k,v in validation.items() if isinstance(v,(float,int))},step)
            if reporter:publish(reporter,validation,epoch+1,step,args.output)
            epoch+=1;cursor=0;epoch_origins=0;phase='train';checkpoint('checkpoint_last.pt')
            if improved:checkpoint('checkpoint_best.pt')
            status(epoch=epoch,validation=validation,validation_done=validation['origins'])
        return 'complete'
    except KeyboardInterrupt:return 'interrupted_preserved_last_checkpoint'
    finally:
        if reporter:reporter.finish()
