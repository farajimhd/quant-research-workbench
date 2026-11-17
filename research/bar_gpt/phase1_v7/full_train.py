"""Full-population epochs with exact resume identity and bounded checkpoints."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import itertools,json,math,time
from pathlib import Path
import torch
from .full_data import loader,prepare
from .full_step import Replay,forward,backward
from .full_profile import model as make_model
from .model import PRICES,ACTIVITY
from ..phase1_v5_raw.engine import DeviceBatches,FRAME_NAMES
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json,FRAMES,CHANNELS
from ..v5.price_relative_v3 import PriceRelative


@torch.no_grad()
def evaluate(model,manifest,args,status,limit=0):
    model.eval();began=time.perf_counter();replay=None
    batch_size=args.validation_batch_size or args.batch_size
    if args.validation_graph:
        import numpy as np
        from .full_step import InferenceReplay
        rows=np.load(args.data/'train.npy',mmap_mode='r',allow_pickle=False)
        example=prepare(rows[:batch_size],native=True,compact=args.preparation=='gpu',raw_gpu=args.preparation=='raw_gpu').to('cuda')
        replay=InferenceReplay(model,example,args.precision);del example
    source=loader(manifest,args,'validation');device=None
    sums=torch.zeros(6,18,device='cuda',dtype=torch.float64);counts=sums.clone();logs=sums.clone()
    negative=torch.zeros((),device='cuda',dtype=torch.int64);nonfinite=negative.clone();violations=negative.clone();n=0;prices=PriceRelative()
    reducers=[PriceRelative() for _ in range(4)];pending=[None]*4;pool=ThreadPoolExecutor(max_workers=4)
    batches=0;steady_started=None;steady_n=0
    try:
        device=DeviceBatches(source,'cuda')
        startup=time.perf_counter()-began;initial_wait=device.wait_seconds;recent=deque([(time.perf_counter(),0)])
        while True:
            if (args.output/'STOP').exists():return None
            try:b=device.pop()
            except StopIteration:break
            if replay and len(b.target)==batch_size:p,target,raw=replay(b)
            else:
                p,target=forward(model,b,args.precision,with_target=True);raw=model.transform.inverse(p)
            truth=b.raw.double();valid=b.valid
            e=(raw-truth).abs();le=(p.double()-target.double()).abs()
            nonfinite+=(~torch.isfinite(raw)&valid).sum();negative+=((raw<0)&valid).sum()
            for base in (0,6,12):
                o,h,l,c=raw[...,base:base+4].unbind(-1)
                violations+=((h<torch.maximum(o,c))|(l>torch.minimum(o,c))).logical_and(valid[...,base:base+4].all(-1)).sum()
            violations+=((raw[...,6:10]>raw[...,12:16])&valid[...,6:10]&valid[...,12:16]).sum()
            fi=(b.frames[:,None]==torch.tensor(FRAMES,device='cuda')).long().argmax(1)
            for f in range(6):
                mask=valid&(fi==f)[:,None,None]
                sums[f]+=torch.where(mask,e,0).sum((0,1));logs[f]+=torch.where(mask,le,0).sum((0,1));counts[f]+=mask.sum((0,1))
            lane=batches%4
            if pending[lane] is not None:pending[lane].result()
            cpu=[x.cpu() for x in (e.transpose(1,2),truth.transpose(1,2),valid.transpose(1,2),fi)]
            pending[lane]=pool.submit(reducers[lane].add,*cpu);batches+=1
            n+=len(b.target);device.advance()
            if batches==8:steady_started=time.perf_counter();steady_n=n
            now=time.perf_counter();recent.append((now,n))
            while len(recent)>2 and now-recent[1][0]>60:recent.popleft()
            rate=(n-recent[0][1])/max(.001,now-recent[0][0]);total=limit*batch_size if limit else manifest['counts']['validation']
            status(stage='bounded validation profile' if limit else 'full validation',validation_done=n,validation_seconds=now-began,
                validation_windows_per_second=rate,validation_eta_seconds=max(0,total-n)/rate,
                phase_startup_seconds=startup,phase_loader_wait_seconds=device.wait_seconds-initial_wait,
                phase_loader_wait_fraction=(device.wait_seconds-initial_wait)/max(.001,now-began-startup),
                gpu_mib=torch.cuda.memory_allocated()/2**20)
            if limit and n>=limit*batch_size:break
        if not limit and n!=manifest['counts']['validation']:raise ValueError(f'Incomplete validation: {n}')
        for future in pending:
            if future is not None:future.result()
        for reducer in reducers:prices.merge(reducer)
        nonfinite,negative,violations=(int(x) for x in (nonfinite,negative,violations))
        raw_mae=(sums/counts.clamp_min(1)).cpu();log_mae=(logs/counts.clamp_min(1)).cpu();counts=counts.cpu()
        result=dict(origins=n,full_population=not bool(limit),seconds=time.perf_counter()-began,startup_seconds=startup,
            loader_wait_seconds=device.wait_seconds-initial_wait,validation_batch_size=batch_size,validation_graph=args.validation_graph,
            nonfinite=nonfinite,negative=negative,
            structure_violations=violations,raw_mae={FRAME_NAMES[f]:{name:float(raw_mae[f,c]) if counts[f,c] else None for c,name in enumerate(CHANNELS)} for f in range(6)},
            log_mae=log_mae.tolist(),valid_counts=counts.long().tolist(),activity_log_mae=float(log_mae[:,ACTIVITY].mean()))
        result.update(prices.metrics('full_raw_mae',FRAME_NAMES,CHANNELS))
        result['steady_windows_per_second']=(n-steady_n)/max(.001,time.perf_counter()-steady_started) if steady_started and n>steady_n else None
        rel=[v for k,v in result.items() if k.startswith('full_price_mape_pct/') and v is not None]
        result['balanced_price_mape_pct']=sum(rel)/len(rel)
        bands=[v for k,v in result.items() if k.startswith('full_price_mape_pct_by_price_band/') and v is not None]
        result['worst_price_band_mape_pct']=max(bands)
        result['selection_score']=result['balanced_price_mape_pct']*.5+result['activity_log_mae']+result['worst_price_band_mape_pct']*.0005
        if nonfinite or negative or violations:raise FloatingPointError('Validation structural/native failure')
        return result
    finally:
        pool.shutdown(wait=True)
        if device:device.close()
        close_loader(source)


def train(args,manifest,status):
    import numpy as np
    model=make_model();optimizer=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=.01,fused=True)
    contract=dict(version='v7-full-1',architecture=asdict(model.config),source_revision=args.source_revision,
        split=manifest['manifest_hash'],batch_size=args.batch_size,workers=args.workers,prefetch=args.prefetch,
        precision=args.precision,cuda_graph=args.cuda_graph,preparation=args.preparation,epochs=args.epochs,seed=args.seed,
        shard_prefetch=args.shard_prefetch,shard_prefetch_mib=args.shard_prefetch_mib,
        validation_batch_size=args.validation_batch_size,validation_graph=args.validation_graph,
        learning_rate=args.learning_rate,min_learning_rate=args.min_learning_rate,warmup_updates=args.warmup_updates,
        sampling='all certified windows, deterministic shuffled shard lanes; all lane tails retained',
        objective='equal price/activity channel means; Huber on 100*log-price residual and log1p-activity residual',
        normalization='no fitted population statistics; visible-input references exported in embedding',
        initialization='fresh seed, not subset checkpoint',torch=str(torch.__version__))
    epoch=step=cursor=origins=epoch_done=0;phase='train';best=float('inf');history=[];engineering_history=[];migration=None
    if args.resume:
        from .full_engineering import resume
        s,migration=resume(args.output/'checkpoint_last.pt',contract,args.engineering_receipt)
        model.load_state_dict(s['model']);optimizer.load_state_dict(s['optimizer']);restore_rng(s['rng'])
        epoch,step,cursor,origins,epoch_done,phase,best,history=(s[k] for k in ('epoch','step','batch_cursor','origins','epoch_done','phase','best','history'))
        engineering_history=s.get('engineering_history',[])
        if migration:engineering_history=engineering_history+[migration]
    else:write_json(args.output/'manifest.json',dict(contract=contract,arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}))
    def checkpoint(name='checkpoint_last.pt'):
        save_checkpoint(args.output/name,dict(contract=contract,model=model.state_dict(),optimizer=optimizer.state_dict(),
            rng=rng_state(),epoch=epoch,step=step,cursor=(epoch,cursor),batch_cursor=cursor,origins=origins,epoch_done=epoch_done,
            phase=phase,best=best,history=history,engineering_history=engineering_history,
            scheduler=dict(epoch=epoch,step=step,peak=args.learning_rate,floor=args.min_learning_rate,warmup=args.warmup_updates)))
    reporter=None;replay=None
    if args.wandb_mode=='online':
        from research.mlops.env import discover_env_files,load_env_files
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        import wandb
        ip=args.output/'wandb-identity.json';identity=json.loads(ip.read_text()) if ip.exists() else None
        if args.resume and not identity:raise ValueError('Missing original W&B identity')
        reporter=wandb.init(project='bargpt-v7-full',entity='mehdifaraji',name=args.output.name,
            id=identity['id'] if identity else None,resume='must' if identity else None,config=None if identity else contract,dir=str(args.output))
        if engineering_history:reporter.config.update({'engineering/current_source_revision':args.source_revision,
            'engineering/current_preparation':args.preparation,'engineering/history':engineering_history},allow_val_change=True)
        if not identity:write_json(ip,dict(id=reporter.id,url=reporter.url,name=reporter.name))
    try:
        if not args.resume or migration:checkpoint()
        # Real-data graph capture before loader pin-memory threads start.
        if args.cuda_graph:
            rows=np.load(args.data/'train.npy',mmap_mode='r',allow_pickle=False)
            example=prepare(rows[:args.batch_size],compact=args.preparation=='gpu',raw_gpu=args.preparation=='raw_gpu').to('cuda');replay=Replay(model,example,args.precision);del example
        while epoch<args.epochs:
            if phase=='train':
                model.train();source=loader(manifest,args,'train',epoch);device=None
                status(stage='starting training loader',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins)
                try:
                    startup_began=time.perf_counter();device=DeviceBatches(itertools.islice(source,cursor,None),'cuda')
                    startup=time.perf_counter()-startup_began;initial_wait=device.wait_seconds
                    began=time.perf_counter();seen=0;recent=deque([(began,0)])
                    while True:
                        if (args.output/'STOP').exists() or (args.output.parent/'STOP').exists():checkpoint();return 'stopped'
                        try:b=device.pop()
                        except StopIteration:break
                        lr=args.min_learning_rate+(args.learning_rate-args.min_learning_rate)*.5*(1+math.cos(math.pi*epoch/max(1,args.epochs-1)))
                        if step<args.warmup_updates:lr=args.min_learning_rate+(args.learning_rate-args.min_learning_rate)*(step+1)/args.warmup_updates
                        optimizer.param_groups[0]['lr']=lr
                        if replay and len(b.target)==args.batch_size:value,channels,norm=replay(b)
                        else:
                            optimizer.zero_grad(set_to_none=True);value,channels,norm=backward(model,b,args.precision)
                        device.advance()
                        if not (torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite loss/gradient before optimizer update')
                        optimizer.step();step+=1;cursor+=1;origins+=len(b.target);epoch_done+=len(b.target);seen+=len(b.target)
                        if step%20==0:
                            now=time.perf_counter();recent.append((now,seen))
                            while len(recent)>2 and now-recent[1][0]>60:recent.popleft()
                            metrics=dict(loss=float(value),price_loss=float(channels[PRICES].mean()),activity_loss=float(channels[ACTIVITY].mean()),
                                gradient_norm=float(norm),learning_rate=lr,windows_per_second=(seen-recent[0][1])/max(.001,now-recent[0][0]),
                                loader_wait_seconds=device.wait_seconds,phase_startup_seconds=startup,
                                phase_loader_wait_seconds=device.wait_seconds-initial_wait,
                                phase_loader_wait_fraction=(device.wait_seconds-initial_wait)/max(.001,now-began),
                                gpu_mib=torch.cuda.memory_allocated()/2**20)
                            status(stage='training',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins,**metrics)
                            with (args.output/'metrics.jsonl').open('a') as f:f.write(json.dumps(dict(step=step,epoch=epoch+1,**metrics))+'\n')
                            if reporter:reporter.log({'train/'+k:v for k,v in metrics.items()},step=max(step,reporter.step))
                        if step%args.checkpoint_every==0:checkpoint()
                        if step%5000==0:
                            from .full_diagnostics import run
                            status(stage='frozen development diagnostics')
                            diagnostic=run(model,args,step)
                            if reporter:reporter.log(diagnostic,step=max(step,reporter.step))
                        if args.max_updates and step>=args.max_updates:checkpoint();return 'qualification_complete'
                    if epoch_done!=manifest['counts']['train']:raise ValueError(f'Training population mismatch: {epoch_done}')
                    phase='validation';checkpoint()
                finally:
                    if device:device.close()
                    close_loader(source)
            status(stage='full validation',epoch=epoch+1,step=step,epoch_done=epoch_done,validation_done=0,
                validation_windows_per_second=None,validation_eta_seconds=None,phase_loader_wait_seconds=None,phase_startup_seconds=None)
            validation=evaluate(model,manifest,args,status)
            if validation is None:checkpoint();return 'stopped'
            write_json(args.output/f'validation-{epoch+1:03}.json',validation)
            improved=validation['selection_score']<best;best=min(best,validation['selection_score'])
            history.append(dict(epoch=epoch+1,step=step,score=validation['selection_score']))
            if reporter:
                published={'validation/'+k:v for k,v in validation.items() if isinstance(v,(float,int))}
                published.update({f'validation/raw_mae/{frame}/{channel}':value for frame,channels in validation['raw_mae'].items() for channel,value in channels.items() if value is not None})
                published.update({f'validation/log_mae/{frame}/{channel}':validation['log_mae'][f][c] for f,frame in enumerate(FRAME_NAMES) for c,channel in enumerate(CHANNELS) if validation['valid_counts'][f][c]})
                reporter.log(published,step=max(step,reporter.step))
            epoch+=1;cursor=epoch_done=0;phase='train';checkpoint()
            if improved:checkpoint('checkpoint_best.pt')
            status(latest_validation=dict(epoch=epoch,price_mape_pct=validation['balanced_price_mape_pct'],activity_log_mae=validation['activity_log_mae'],seconds=validation['seconds']))
        return 'complete'
    except KeyboardInterrupt:
        checkpoint();return 'interrupted'
    finally:
        if reporter:reporter.finish()
