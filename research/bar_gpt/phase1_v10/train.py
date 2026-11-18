"""All-window training with full masked/all-visible validation and exact resume."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import itertools,json,time
from pathlib import Path
import numpy as np
import torch
from .run import make_model
from .data import loader,prepare_raw,read_calibration
from .step import forward,backward,make_replay
from .model import inverse,objective,PRICES,ACTIVITY,learning_rate
from .metrics import PriceRelative
from ..phase1_v5_raw.engine import DeviceBatches,FRAME_NAMES,report
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json,FRAMES,CHANNELS


class Accumulator:
    def __init__(self):
        self.sums=torch.zeros(6,18,device='cuda',dtype=torch.float64);self.counts=self.sums.clone();self.logs=self.sums.clone()
        self.ps=self.sums.clone();self.ys=self.sums.clone();self.p2=self.sums.clone();self.y2=self.sums.clone()
        self.zero=self.sums.clone();self.tiny=self.sums.clone()
        self.bad=torch.zeros(3,device='cuda',dtype=torch.int64)
        self.time_sum=torch.zeros(9,device='cuda',dtype=torch.float64);self.time_count=self.time_sum.clone()
        self.confusion=torch.zeros(4,device='cuda',dtype=torch.int64)
        self.reducers=[PriceRelative(),PriceRelative()];self.pool=ThreadPoolExecutor(max_workers=2);self.pending=[None,None];self.batches=0

    def add(self,model,output,values,valid,hidden,b):
        p,t,vl=output;raw=inverse(p);truth=b.raw.double();v=valid[...,:18]&hidden[...,None]
        error=(raw-truth).abs();loge=(p.double()-values[...,:18].double()).abs()
        self.bad[0]+=(~torch.isfinite(raw)&v).sum();self.bad[1]+=((raw<0)&v).sum()
        for base in (0,6,12):
            o,h,l,c=raw[...,base:base+4].unbind(-1)
            self.bad[2]+=(((h<torch.maximum(o,c))|(l>torch.minimum(o,c)))&v[...,base:base+4].all(-1)).sum()
        self.bad[2]+=((raw[...,6:10]>raw[...,12:16])&v[...,6:10]&v[...,12:16]).sum()
        fi=(b.frames[:,None]==torch.tensor(FRAMES,device='cuda')).long().argmax(1)
        for f in range(6):
            mask=v&(fi==f)[:,None,None]
            for dest,source in ((self.sums,error),(self.logs,loge),(self.ps,raw),(self.ys,truth),(self.p2,raw.square()),(self.y2,truth.square())):
                dest[f]+=torch.where(mask,source,0).sum((0,1))
            self.counts[f]+=mask.sum((0,1));self.zero[f]+=(mask&(truth==0)).sum((0,1));self.tiny[f]+=(mask&(truth>0)&(truth<=1e-12)).sum((0,1))
        tv=valid[...,18:]&hidden[...,None];te=(t-(values[...,18:]-model.input_mean[18:])/model.input_scale[18:]).abs()
        self.time_sum+=torch.where(tv,te,0).sum((0,1));self.time_count+=tv.sum((0,1))
        vv=vl>0;eligible=hidden[...,None]
        self.confusion+=torch.stack(((vv&valid&eligible).sum(),(vv&~valid&eligible).sum(),(~vv&valid&eligible).sum(),(~vv&~valid&eligible).sum()))
        lane=self.batches%2
        if self.pending[lane] is not None:self.pending[lane].result()
        cpu=[x.cpu() for x in (error.transpose(1,2),truth.transpose(1,2),v.transpose(1,2),fi)]
        self.pending[lane]=self.pool.submit(self.reducers[lane].add,*cpu);self.batches+=1

    def finish(self):
        self.pool.shutdown(wait=True)
        for job in self.pending:
            if job:job.result()
        self.reducers[0].merge(self.reducers[1]);n=self.counts.clamp_min(1)
        raw=(self.sums/n).cpu();logs=(self.logs/n).cpu();counts=self.counts.cpu()
        result=dict(nonfinite=int(self.bad[0]),negative=int(self.bad[1]),structure_violations=int(self.bad[2]),
            raw_mae={FRAME_NAMES[f]:{name:float(raw[f,c]) if counts[f,c] else None for c,name in enumerate(CHANNELS)} for f in range(6)},
            activity_log_mae=float(logs[:,ACTIVITY][counts[:,ACTIVITY]>0].mean()),log_mae=logs.tolist(),valid_counts=counts.long().tolist(),
            zero_denominators=self.zero.long().cpu().tolist(),tiny_denominators=self.tiny.long().cpu().tolist(),
            prediction_variance=(self.p2/n-(self.ps/n).square()).cpu().tolist(),target_variance=(self.y2/n-(self.ys/n).square()).cpu().tolist(),
            time_standardized_mae=(self.time_sum/self.time_count.clamp_min(1)).cpu().tolist(),validity_confusion=dict(zip(('tp','fp','fn','tn'),self.confusion.cpu().tolist())))
        result.update(self.reducers[0].metrics('raw_mae',FRAME_NAMES,CHANNELS))
        rel=[v for k,v in result.items() if k.startswith('price_mape_pct/') and v is not None]
        bands=[v for k,v in result.items() if k.startswith('price_mape_pct_by_price_band/') and v is not None]
        result.update(balanced_price_mape_pct=sum(rel)/len(rel),worst_price_band_mape_pct=max(bands))
        return result


@torch.no_grad()
def evaluate(model,manifest,a,status,limit=0):
    model.eval();source=loader(manifest,a,'validation');device=None;acc={k:Accumulator() for k in ('masked','all_visible')}
    generator=torch.Generator(device='cuda').manual_seed(731);n=0;started=time.perf_counter();recent=deque();stopped=False
    try:
        device=DeviceBatches(source,'cuda');startup=time.perf_counter()-started;wait=device.wait_seconds
        for i in range(limit or 2**63-1):
            if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():stopped=True;break
            try:b=device.pop()
            except StopIteration:break
            for mode,visible in (('masked',16),('all_visible',64)):
                out,x,v,hidden=forward(model,b,visible,generator);acc[mode].add(model,out,x,v,hidden,b)
            device.advance();n+=len(b.target);now=time.perf_counter();recent.append((now,n))
            while len(recent)>2 and now-recent[0][0]>60:recent.popleft()
            rate=(n-recent[0][1])/max(.001,now-recent[0][0]) if len(recent)>1 else 0
            total=(limit*(a.validation_batch_size or a.batch_size)) if limit else manifest['counts']['validation']
            status(stage='bounded validation profile' if limit else 'masked + all-visible full validation',validation_done=n,
                validation_total=total,validation_windows_per_second=rate,validation_eta_seconds=(max(0,total-n)/rate if rate else None),
                phase_startup_seconds=startup,phase_loader_wait_seconds=device.wait_seconds-wait,
                phase_loader_wait_fraction=(device.wait_seconds-wait)/max(.001,now-started-startup),gpu_mib=torch.cuda.memory_allocated()/2**20,gpu_reserved_mib=torch.cuda.memory_reserved()/2**20)
        if not stopped and not limit and n!=manifest['counts']['validation']:raise ValueError(f'Incomplete validation {n}')
        if stopped and not n:return None
        results={k:v.finish() for k,v in acc.items()}
        result=dict(windows=n,full_population=not limit and not stopped,stopped=stopped,seconds=time.perf_counter()-started,
            startup_seconds=startup,loader_wait_seconds=device.wait_seconds-wait,metrics=results,
            scope='Masked: only 48 hidden source-valid bars. All-visible: all64 source-valid bars. Histogram relative quantiles are approximate.')
        write_json(a.output/'validation-latest.json',result)
        if any(v['nonfinite'] or v['negative'] or v['structure_violations'] for v in results.values()):raise FloatingPointError('Native validation failure; evidence retained')
        return None if stopped else result
    finally:
        for v in acc.values():v.pool.shutdown(wait=True)
        if device:device.close()
        close_loader(source)


@torch.no_grad()
def diagnostics(model,a,step):
    from ..phase1_v7.metrics import evaluate as native,require_structured
    m=model.training;model.eval();rows=np.load(a.data/'development.npy',mmap_mode='r',allow_pickle=False)
    result=dict(step=step,windows=len(rows),scope='Frozen development subset only; every full validation uses the entire certified partition.')
    for mode,visible in (('masked',16),('all_visible',64)):
        predictions=[];masks=[];generator=torch.Generator(device='cuda').manual_seed(731)
        for start in range(0,len(rows),256):
            b=prepare_raw(rows[start:start+256],native=True).to('cuda');out,x,v,h=forward(model,b,visible,generator)
            predictions.append(inverse(out[0]).cpu().numpy());masks.append((v[...,:18]&h[...,None]).cpu().numpy())
        result[mode]=native(np.concatenate(predictions),rows,np.concatenate(masks));require_structured(result[mode],paired=True)
    write_json(a.output/'development-latest.json',result);model.train(m)
    return {f'development/{mode}/{k}':result[mode][k] for mode in ('masked','all_visible') for k in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean')}


def train(a,manifest,status):
    model=make_model(a);optimizer=torch.optim.AdamW(model.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True)
    prototype=loader(manifest,a,'train');steps_per_epoch=len(prototype);del prototype
    total_updates=steps_per_epoch*a.epochs
    contract=dict(version='phase1-v10-1',architecture=asdict(model.config),source_revision=a.source_revision,
        split=manifest['manifest_hash'],calibration=read_calibration(a.calibration)['hash'],batch_size=a.batch_size,workers=a.workers,
        prefetch=a.prefetch,shard_prefetch=a.shard_prefetch,shard_prefetch_mib=a.shard_prefetch_mib,
        validation_batch_size=a.validation_batch_size,validation_workers=a.validation_workers,epochs=a.epochs,
        precision=a.precision,cuda_graph=a.cuda_graph,seed=a.seed,learning_rate=a.learning_rate,min_learning_rate=a.min_learning_rate,
        total_updates=total_updates,steps_per_epoch=steps_per_epoch,weight_decay=.01,gradient_clip=1.,
        objective='MAE: fresh16 random visible bars per window; source-valid hidden48 log-relative Huber plus time/validity reconstruction',
        sampling='Every certified training window, deterministic ordered shuffled shard lanes, all worker tails retained',
        decoder_dependency='Only learned embedding; no copied references, raw bypass or target statistics',
        encoder_parameters=sum(p.numel() for p in model.encoder.parameters()),decoder_parameters=sum(p.numel() for p in model.decoder.parameters()),
        initialization='fresh seed17 unless exact engineering resume',torch=str(torch.__version__))
    epoch=step=cursor=origins=epoch_done=0;phase='train';best=float('inf');history=[]
    if a.resume:
        s=load_checkpoint(a.output/'checkpoint_last.pt',contract);model.load_state_dict(s['model']);optimizer.load_state_dict(s['optimizer']);restore_rng(s['rng'])
        epoch,step,cursor,origins,epoch_done,phase,best,history=(s[k] for k in ('epoch','step','batch_cursor','origins','epoch_done','phase','best','history'))
    else:write_json(a.output/'manifest.json',dict(contract=contract,arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()}))
    def checkpoint(name='checkpoint_last.pt'):
        save_checkpoint(a.output/name,dict(contract=contract,model=model.state_dict(),optimizer=optimizer.state_dict(),rng=rng_state(),
            epoch=epoch,step=step,cursor=(epoch,cursor),batch_cursor=cursor,origins=origins,epoch_done=epoch_done,phase=phase,best=best,history=history,
            scheduler=dict(step=step,total_updates=total_updates,peak=a.learning_rate,floor=a.min_learning_rate)))
    reporter=None;replay=None
    if a.wandb_mode=='online':
        from research.mlops.env import discover_env_files,load_env_files
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        import wandb
        ip=a.output/'wandb-identity.json';identity=json.loads(ip.read_text()) if ip.exists() else None
        if a.resume and not identity:raise ValueError('Missing original W&B identity')
        reporter=wandb.init(project='bargpt-phase1-v10-full-mae',entity='mehdifaraji',name=a.output.name,
            id=identity['id'] if identity else None,resume='must' if identity else None,config=None if identity else contract,dir=str(a.output))
        if not identity:write_json(ip,dict(id=reporter.id,url=reporter.url,name=reporter.name))
    try:
        if not a.resume:checkpoint()
        if a.cuda_graph:
            rows=np.load(a.data/'train.npy',mmap_mode='r',allow_pickle=False)
            example=prepare_raw(rows[:a.batch_size]).to('cuda');model.train();replay=make_replay(model,example);del example
        while epoch<a.epochs:
            if phase=='train':
                model.train();source=loader(manifest,a,'train',epoch);device=None
                status(stage='starting training loader',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins)
                try:
                    setup=time.perf_counter();state=rng_state();device=DeviceBatches(itertools.islice(source,cursor,None),'cuda');restore_rng(state)
                    startup=time.perf_counter()-setup;initial_wait=device.wait_seconds;began=time.perf_counter();seen=0;recent=deque([(began,0)])
                    while True:
                        if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():checkpoint();return 'stopped'
                        try:b=device.pop()
                        except StopIteration:break
                        lr=learning_rate(step,total_updates,a.learning_rate,a.min_learning_rate);optimizer.param_groups[0]['lr']=lr
                        if replay and len(b.target)==a.batch_size:value,channels,norm=replay(b)
                        else:optimizer.zero_grad(set_to_none=True);value,channels,norm=backward(model,b)
                        device.advance()
                        if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite loss/gradient before update')
                        optimizer.step();step+=1;cursor+=1;origins+=len(b.target);epoch_done+=len(b.target);seen+=len(b.target)
                        if step%20==0:
                            now=time.perf_counter();recent.append((now,seen))
                            while len(recent)>2 and now-recent[1][0]>60:recent.popleft()
                            metrics=dict(loss=float(value),price_loss=float(channels[list(PRICES)].mean()),activity_loss=float(channels[list(ACTIVITY)].mean()),
                                gradient_norm=float(norm),learning_rate=lr,windows_per_second=(seen-recent[0][1])/max(.001,now-recent[0][0]),
                                phase_startup_seconds=startup,phase_loader_wait_seconds=device.wait_seconds-initial_wait,
                                phase_loader_wait_fraction=(device.wait_seconds-initial_wait)/max(.001,now-began),gpu_mib=torch.cuda.memory_allocated()/2**20,gpu_reserved_mib=torch.cuda.memory_reserved()/2**20)
                            status(stage='training',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins,**metrics)
                            with (a.output/'metrics.jsonl').open('a') as f:f.write(json.dumps(dict(step=step,epoch=epoch+1,**metrics))+'\n')
                            report(reporter,{'train/'+k:v for k,v in metrics.items()},step)
                        if step%a.checkpoint_every==0:checkpoint()
                        if step%a.diagnostic_every==0:
                            state=rng_state();status(stage='frozen development diagnostics');d=diagnostics(model,a,step);restore_rng(state);report(reporter,d,step)
                        if a.max_updates and step>=a.max_updates:checkpoint();return 'qualification_complete'
                    if epoch_done!=manifest['counts']['train'] or cursor!=steps_per_epoch:raise ValueError('Training traversal/population mismatch')
                    phase='validation';checkpoint()
                finally:
                    if device:device.close()
                    close_loader(source)
            status(stage='masked + all-visible full validation',epoch=epoch+1,step=step,validation_done=0,validation_windows_per_second=None,validation_eta_seconds=None)
            state=rng_state();validation=evaluate(model,manifest,a,status);restore_rng(state)
            if validation is None:checkpoint();return 'stopped'
            write_json(a.output/f'validation-{epoch+1:03}.json',validation)
            masked=validation['metrics']['masked'];score=.5*masked['balanced_price_mape_pct']+masked['activity_log_mae']+.0005*masked['worst_price_band_mape_pct']
            improved=score<best;best=min(best,score);history.append(dict(epoch=epoch+1,step=step,score=score))
            published={}
            for mode,metrics in validation['metrics'].items():
                published.update({f'validation/{mode}/{k}':v for k,v in metrics.items() if isinstance(v,(int,float))})
                for frame,channels in metrics['raw_mae'].items():published.update({f'validation/{mode}/raw_mae/{frame}/{k}':v for k,v in channels.items() if v is not None})
            report(reporter,published,step)
            epoch+=1;cursor=epoch_done=0;phase='train';checkpoint()
            if improved:checkpoint('checkpoint_best.pt')
            status(latest_validation=dict(epoch=epoch,price_mape_pct=masked['balanced_price_mape_pct'],activity_log_mae=masked['activity_log_mae'],seconds=validation['seconds']))
        if step!=total_updates:raise ValueError('Schedule endpoint mismatch')
        return 'complete'
    except KeyboardInterrupt:checkpoint();return 'interrupted'
    finally:
        if reporter:reporter.finish()
