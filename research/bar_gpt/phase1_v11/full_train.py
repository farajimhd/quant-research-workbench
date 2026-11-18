"""All certified windows, epoch-specific tails, bounded checkpoints and exact resumes."""
from collections import deque
import itertools,json,math,sys,time
from pathlib import Path
import numpy as np
import torch
from .full_step import Replay,backward
from .full_validation import evaluate
from .evaluation import evaluate_model
from .data import cache
from ..phase1_v7.full_data import loader,prepare_raw
from ..phase1_v10.train import training_learning_rate,verify_traversal
from ..phase1_v5_raw.engine import DeviceBatches,report
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json,file_hash


def train(model,parent,a,manifest,status):
    optimizer=torch.optim.AdamW(model.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True)
    if a.retain_optimizer and not a.resume:optimizer.load_state_dict(parent['optimizer'])
    prototype=loader(manifest,a,'train');steps_per_epoch=len(prototype);del prototype
    total_updates=steps_per_epoch*a.epochs
    contract=dict(version='phase1-v11-full-1',config=vars(model.config),source_revision=a.resume_contract_source or a.source_revision,
        split_hash=manifest['manifest_hash'],calibration_sha256=file_hash(a.calibration),batch_size=a.batch_size,workers=a.workers,prefetch=a.prefetch,
        shard_prefetch=a.shard_prefetch,shard_prefetch_mib=a.shard_prefetch_mib,validation_batch_size=a.validation_batch_size,validation_workers=a.validation_workers,
        epochs=a.epochs,seed=a.seed,learning_rate=a.learning_rate,min_learning_rate=a.min_learning_rate,total_updates=total_updates,steps_per_epoch=steps_per_epoch,
        weight_decay=.01,gradient_clip=1.,initialization_checkpoint=str(a.checkpoint),initialization_sha256=file_hash(a.checkpoint),retain_optimizer=a.retain_optimizer,
        sampling='Every certified training window; original chronological split, deterministic shuffled worker lanes, all tails retained.',
        decoder_dependency='Exported learned embedding only; all nine references predicted; visible-only median computation.',
        encoder_parameters=sum(p.numel() for p in model.encoder.parameters()),decoder_parameters=sum(p.numel() for p in model.parameters())-sum(p.numel() for p in model.encoder.parameters()))
    epoch=step=cursor=origins=epoch_done=0;phase='train';best=development_best=float('inf');history=[]
    if a.resume:
        saved=load_checkpoint(a.output/'checkpoint_last.pt',contract);model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer']);restore_rng(saved['rng'])
        epoch,step,cursor,origins,epoch_done,phase,best,development_best,history=(saved[k] for k in ('epoch','step','batch_cursor','origins','epoch_done','phase','best','development_best','history'))
        write_json(a.output/f'engineering-resume-{time.time_ns()}.json',dict(execution_source=a.source_revision,previous_execution_source=saved['execution_source'],checkpoint_proof=json.loads((a.output/'checkpoint_last.proof.json').read_text())))
    else:write_json(a.output/'manifest.json',dict(contract=contract,arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()}))
    import wandb
    identity_path=a.output/'wandb-identity.json';identity=json.loads(identity_path.read_text()) if identity_path.exists() else None
    if a.resume and not identity:raise ValueError('Original W&B identity required')
    run=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v11-full',name=a.output.name,id=identity['id'] if identity else None,resume='must' if identity else None,
        dir=str(a.output),config=None if identity else contract)
    if not identity:write_json(identity_path,dict(id=run.id,url=run.url))
    report_floor=int(run.summary.get('optimizer_step',-1)) if a.resume else -1
    def publish(metrics):
        if step>report_floor:report(run,metrics,step)
    def checkpoint(name='checkpoint_last.pt'):
        save_checkpoint(a.output/name,dict(model=model.state_dict(),optimizer=optimizer.state_dict(),rng=rng_state(),contract=contract,
            epoch=epoch,step=step,cursor=(epoch,cursor),batch_cursor=cursor,origins=origins,epoch_done=epoch_done,phase=phase,best=best,development_best=development_best,history=history,
            execution_source=a.source_revision,scheduler=dict(step=step,total_updates=total_updates,peak=a.learning_rate,floor=a.min_learning_rate)))
        if step>=report_floor:run.summary.update({'checkpoint/step':step,'checkpoint/sha256':file_hash(a.output/name),'checkpoint/path':str(a.output/name),'optimizer_step':step})
    development_rows=np.load(a.data/'development.npy',allow_pickle=False);development=cache(development_rows,'cuda')
    def diagnostics():
        state=rng_state();was=model.training
        try:
            result=evaluate_model(model,development,development_rows,a.output,lambda **kw:status(**kw,step=step,epoch=epoch+1))
            write_json(a.output/'development-latest.json',result)
            metrics={f'development/{mode}/{key}':m[key] for mode,m in result.items() for key in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean')}
            publish(metrics)
            with (a.output/'development-history.jsonl').open('a') as f:f.write(json.dumps(dict(step=step,**metrics))+'\n')
            return result['masked']['selection_score']
        finally:restore_rng(state);model.train(was)
    replay=None
    try:
        if not a.resume:
            development_best=diagnostics();checkpoint('checkpoint_development_best.pt');checkpoint()
        rows=np.load(a.data/'train.npy',mmap_mode='r',allow_pickle=False)
        example=prepare_raw(rows[:a.batch_size]).to('cuda');model.train();replay=Replay(model,example);del example
        while epoch<a.epochs:
            if phase=='train':
                model.train();source=loader(manifest,a,'train',epoch);epoch_steps=len(source);device=None
                status(stage='starting training loader',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins)
                try:
                    setup=time.perf_counter();state=rng_state();device=DeviceBatches(itertools.islice(source,cursor,None),'cuda');restore_rng(state)
                    startup=time.perf_counter()-setup;wait=device.wait_seconds;began=time.perf_counter();seen=0;recent=deque([(began,0)])
                    while True:
                        if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():checkpoint();return 'stopped'
                        try:b=device.pop()
                        except StopIteration:break
                        lr=training_learning_rate(step,total_updates,epoch,cursor,epoch_steps,a.epochs,a.learning_rate,a.min_learning_rate);optimizer.param_groups[0]['lr']=lr
                        if len(b.target)==a.batch_size:loss,norm,parts=replay(b)
                        else:optimizer.zero_grad(set_to_none=True);loss,norm,parts=backward(model,b)
                        device.advance()
                        if not bool(torch.isfinite(loss)&torch.isfinite(norm)):
                            torch.save({name:getattr(b,name).detach().cpu() for name in ('grid','slots','valid','frames')},a.output/'failure-batch.pt')
                            raise FloatingPointError('Nonfinite update rejected; last durable checkpoint preserved')
                        optimizer.step();step+=1;cursor+=1;origins+=len(b.target);epoch_done+=len(b.target);seen+=len(b.target)
                        if step%20==0:
                            now=time.perf_counter();recent.append((now,seen))
                            while len(recent)>2 and now-recent[1][0]>60:recent.popleft()
                            metrics=dict(loss=float(loss),price_loss=float(parts[0]),activity_loss=float(parts[1]),reference_price_loss=float(parts[2]),reference_activity_loss=float(parts[3]),
                                gradient_norm=float(norm),learning_rate=lr,windows_per_second=(seen-recent[0][1])/max(.001,now-recent[0][0]),
                                phase_startup_seconds=startup,phase_loader_wait_seconds=device.wait_seconds-wait,phase_loader_wait_fraction=(device.wait_seconds-wait)/max(.001,now-began),
                                gpu_mib=torch.cuda.memory_allocated()/2**20,gpu_reserved_mib=torch.cuda.memory_reserved()/2**20)
                            status(stage='training',epoch=epoch+1,step=step,epoch_done=epoch_done,origins=origins,**metrics);publish({'train/'+k:v for k,v in metrics.items()})
                            with (a.output/'metrics.jsonl').open('a') as f:f.write(json.dumps(dict(step=step,epoch=epoch+1,**metrics))+'\n')
                        if step%a.diagnostic_every==0:
                            score=diagnostics()
                            if not math.isfinite(score):raise FloatingPointError('Nonfinite development reconstruction')
                            if score<development_best:development_best=score;checkpoint('checkpoint_development_best.pt')
                        if step%a.checkpoint_every==0:checkpoint()
                        if a.max_updates and step>=a.max_updates:checkpoint();return 'qualification_complete'
                    verify_traversal(epoch_done,manifest['counts']['train'],cursor,epoch_steps);phase='validation';checkpoint()
                finally:
                    if device:device.close()
                    close_loader(source)
            state=rng_state();validation=evaluate(model,manifest,a,status);restore_rng(state)
            if validation is None:checkpoint();return 'stopped'
            write_json(a.output/f'validation-{epoch+1:03}.json',validation)
            masked=validation['metrics']['masked'];score=.5*masked['balanced_price_mape_pct']+masked['activity_log_mae']+.0005*masked['worst_price_band_mape_pct']
            improved=score<best;best=min(best,score);history.append(dict(epoch=epoch+1,step=step,score=score))
            metrics={}
            for mode,m in validation['metrics'].items():
                metrics.update({f'validation/{mode}/{k}':v for k,v in m.items() if isinstance(v,(int,float))})
                for frame,channels in m['raw_mae'].items():metrics.update({f'validation/{mode}/raw_mae/{frame}/{k}':v for k,v in channels.items() if v is not None})
            publish(metrics);epoch+=1;cursor=epoch_done=0;phase='train';checkpoint()
            if improved:checkpoint('checkpoint_best.pt')
            status(latest_validation=dict(epoch=epoch,price_mape_pct=masked['balanced_price_mape_pct'],activity_log_mae=masked['activity_log_mae'],seconds=validation['seconds']))
        if origins!=manifest['counts']['train']*a.epochs:raise ValueError('Completed training population mismatch')
        return 'complete'
    except KeyboardInterrupt:checkpoint();return 'interrupted'
    finally:run.finish(exit_code=1 if sys.exc_info()[0] is not None else 0)
