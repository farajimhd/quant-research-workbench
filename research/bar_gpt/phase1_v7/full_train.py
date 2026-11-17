"""Full-population epochs with exact resume identity and bounded checkpoints."""
from collections import deque
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
def evaluate(model,manifest,args,status):
    source=loader(manifest,args,'validation');device=None;model.eval();began=time.perf_counter()
    sums=torch.zeros(6,18,device='cuda',dtype=torch.float64);counts=sums.clone();logs=sums.clone()
    negative=nonfinite=violations=0;n=0;prices=PriceRelative()
    try:
        device=DeviceBatches(source,'cuda')
        while True:
            if (args.output/'STOP').exists():return None
            try:b=device.pop()
            except StopIteration:break
            p=forward(model,b,args.precision);raw=model.transform.inverse(p);truth=b.raw.double();valid=b.valid
            e=(raw-truth).abs();le=(p.double()-b.target.double()).abs()
            nonfinite+=int((~torch.isfinite(raw)&valid).sum());negative+=int(((raw<0)&valid).sum())
            for base in (0,6,12):
                o,h,l,c=raw[...,base:base+4].unbind(-1)
                violations+=int(((h<torch.maximum(o,c))|(l>torch.minimum(o,c))).logical_and(valid[...,base:base+4].all(-1)).sum())
            violations+=int(((raw[...,6:10]>raw[...,12:16])&valid[...,6:10]&valid[...,12:16]).sum())
            fi=(b.frames[:,None]==torch.tensor(FRAMES,device='cuda')).long().argmax(1)
            for f in range(6):
                mask=valid&(fi==f)[:,None,None]
                sums[f]+=torch.where(mask,e,0).sum((0,1));logs[f]+=torch.where(mask,le,0).sum((0,1));counts[f]+=mask.sum((0,1))
            prices.add(e.transpose(1,2),truth.transpose(1,2),valid.transpose(1,2),fi)
            n+=len(b.target);device.advance()
            status(stage='full validation',validation_done=n,validation_seconds=time.perf_counter()-began)
        if n!=manifest['counts']['validation']:raise ValueError(f'Incomplete validation: {n}')
        raw_mae=(sums/counts.clamp_min(1)).cpu();log_mae=(logs/counts.clamp_min(1)).cpu();counts=counts.cpu()
        result=dict(origins=n,full_population=True,seconds=time.perf_counter()-began,nonfinite=nonfinite,negative=negative,
            structure_violations=violations,raw_mae={FRAME_NAMES[f]:{name:float(raw_mae[f,c]) if counts[f,c] else None for c,name in enumerate(CHANNELS)} for f in range(6)},
            log_mae=log_mae.tolist(),valid_counts=counts.long().tolist(),activity_log_mae=float(log_mae[:,ACTIVITY].mean()))
        result.update(prices.metrics('full_raw_mae',FRAME_NAMES,CHANNELS))
        rel=[v for k,v in result.items() if k.startswith('full_price_mape_pct/') and v is not None]
        result['balanced_price_mape_pct']=sum(rel)/len(rel)
        result['selection_score']=result['balanced_price_mape_pct']*.5+result['activity_log_mae']
        if nonfinite or negative or violations:raise FloatingPointError('Validation structural/native failure')
        return result
    finally:
        if device:device.close()
        close_loader(source)


def train(args,manifest,status):
    import numpy as np
    model=make_model();optimizer=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=.01,fused=True)
    contract=dict(version='v7-full-1',architecture=asdict(model.config),source_revision=args.source_revision,
        split=manifest['manifest_hash'],batch_size=args.batch_size,workers=args.workers,prefetch=args.prefetch,
        precision=args.precision,cuda_graph=args.cuda_graph,epochs=args.epochs,seed=args.seed,
        learning_rate=args.learning_rate,min_learning_rate=args.min_learning_rate,warmup_updates=args.warmup_updates,
        sampling='all certified windows, deterministic shuffled shard lanes; all lane tails retained',
        objective='equal price/activity channel means; Huber on 100*log-price residual and log1p-activity residual',
        normalization='no fitted population statistics; visible-input references exported in embedding',
        initialization='fresh seed, not subset checkpoint',torch=str(torch.__version__))
    epoch=step=cursor=origins=epoch_done=0;phase='train';best=float('inf');history=[]
    if args.resume:
        s=load_checkpoint(args.output/'checkpoint_last.pt',contract)
        model.load_state_dict(s['model']);optimizer.load_state_dict(s['optimizer']);restore_rng(s['rng'])
        epoch,step,cursor,origins,epoch_done,phase,best,history=(s[k] for k in ('epoch','step','batch_cursor','origins','epoch_done','phase','best','history'))
    else:write_json(args.output/'manifest.json',dict(contract=contract,arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}))
    def checkpoint(name='checkpoint_last.pt'):
        save_checkpoint(args.output/name,dict(contract=contract,model=model.state_dict(),optimizer=optimizer.state_dict(),
            rng=rng_state(),epoch=epoch,step=step,cursor=(epoch,cursor),batch_cursor=cursor,origins=origins,epoch_done=epoch_done,
            phase=phase,best=best,history=history,scheduler=dict(epoch=epoch,step=step,peak=args.learning_rate,floor=args.min_learning_rate,warmup=args.warmup_updates)))
    reporter=None;replay=None
    if args.wandb_mode=='online':
        from research.mlops.env import discover_env_files,load_env_files
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        import wandb
        ip=args.output/'wandb-identity.json';identity=json.loads(ip.read_text()) if ip.exists() else None
        if args.resume and not identity:raise ValueError('Missing original W&B identity')
        reporter=wandb.init(project='bargpt-v7-full',entity='mehdifaraji',name=args.output.name,
            id=identity['id'] if identity else None,resume='must' if identity else None,config=contract,dir=str(args.output))
        if not identity:write_json(ip,dict(id=reporter.id,url=reporter.url,name=reporter.name))
    try:
        if not args.resume:checkpoint()
        # Real-data graph capture before loader pin-memory threads start.
        if args.cuda_graph:
            rows=np.load(args.data/'train.npy',mmap_mode='r',allow_pickle=False)
            example=prepare(rows[:args.batch_size]).to('cuda');replay=Replay(model,example,args.precision);del example
        while epoch<args.epochs:
            if phase=='train':
                model.train();source=loader(manifest,args,'train',epoch);device=None
                status(stage='starting training loader',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins)
                try:
                    device=DeviceBatches(itertools.islice(source,cursor,None),'cuda')
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
                                loader_wait_seconds=device.wait_seconds,gpu_mib=torch.cuda.memory_allocated()/2**20)
                            status(stage='training',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins,**metrics)
                            with (args.output/'metrics.jsonl').open('a') as f:f.write(json.dumps(dict(step=step,epoch=epoch+1,**metrics))+'\n')
                            if reporter:reporter.log({'train/'+k:v for k,v in metrics.items()},step=max(step,reporter.step))
                        if step%args.checkpoint_every==0:checkpoint()
                    if epoch_done!=manifest['counts']['train']:raise ValueError(f'Training population mismatch: {epoch_done}')
                    phase='validation';checkpoint()
                finally:
                    if device:device.close()
                    close_loader(source)
            status(stage='full validation',validation_done=0)
            validation=evaluate(model,manifest,args,status)
            if validation is None:checkpoint();return 'stopped'
            write_json(args.output/f'validation-{epoch+1:03}.json',validation)
            improved=validation['selection_score']<best;best=min(best,validation['selection_score'])
            history.append(dict(epoch=epoch+1,step=step,score=validation['selection_score']))
            if reporter:reporter.log({'validation/'+k:v for k,v in validation.items() if isinstance(v,(float,int))},step=max(step,reporter.step))
            epoch+=1;cursor=epoch_done=0;phase='train';checkpoint()
            if improved:checkpoint('checkpoint_best.pt')
            status(latest_validation=dict(epoch=epoch,price_mape_pct=validation['balanced_price_mape_pct'],activity_log_mae=validation['activity_log_mae'],seconds=validation['seconds']))
        return 'complete'
    except KeyboardInterrupt:
        checkpoint();return 'interrupted'
    finally:
        if reporter:reporter.finish()
