"""Ten means ten dataset traversals; validation never advances the epoch."""
import json
import math
import time
import torch
from .schedule import FixedWarmup as FullPassCosine
from ..phase2_v2.schedule import PassProgress


def train_full_pass(model,teacher,teacher_sha,units,contract,args,reporter,*,components=None):
    from ..phase2_v2.train import (optimizer_for,Metrics,load_checkpoint,save_checkpoint,restore_rng,rng_state,
                        device_batches,batches,autocast,future_loss,evaluate,write_json)
    target_size=len
    if components is not None:
        batches,Metrics,evaluate=components.batches,components.Metrics,components.evaluate
        target_size=components.target_size
    optimizer=optimizer_for(model,args)
    loss_function=getattr(model,'training_loss',future_loss)
    scheduler=FullPassCosine(args.learning_rate,args.min_learning_rate,args.validation_every,
                             args.warmup_updates,getattr(args,'lr_restart_decay',.9))
    progress=PassProgress(units)
    epoch=step=stale=0;cursor=(0,0,0);phase='train';best=math.inf
    history=[];interval_history=[];last_validation_step=-1;exhausted=False;transition=None
    metrics=Metrics(args.device)
    census_state=None
    if args.resume:
        saved=load_checkpoint(args.resume,contract)
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        scheduler.load_state_dict(saved['scheduler'])
        epoch,step,cursor,phase=saved['epoch'],saved['step'],tuple(saved['cursor']),saved['phase']
        if scheduler.updates!=step or scheduler.epoch!=epoch or saved['data_pass']!=epoch+1:
            raise ValueError('Full-pass/optimizer/scheduler ledgers disagree')
        best,history,stale=saved['best'],saved['history'],saved['stale']
        interval_history=saved.get('interval_history',[])
        last_validation_step=saved.get('last_validation_step',-1)
        exhausted=saved.get('pass_exhausted',False)
        transition=saved.get('schedule_transition')
        metrics.restore(saved['training_metrics']);restore_rng(saved['rng'])
        census_state=saved.get('loader_census')
    from .loader_census import LoaderCensus,TAG
    if census_state and not getattr(args,'loader_census',False):raise ValueError('Cannot silently drop an active census ledger')
    census=LoaderCensus(step,epoch,cursor,census_state) if getattr(args,'loader_census',False) else None

    def checkpoint(name):
        save_checkpoint(args.output/name,dict(contract=contract,model=model.state_dict(),
            optimizer=optimizer.state_dict(),scheduler=scheduler.state_dict(),epoch=epoch,step=step,
            cursor=cursor,phase=phase,best=best,stale=stale,history=history,
            interval_history=interval_history,last_validation_step=last_validation_step,
            data_pass=epoch+1,pass_exhausted=exhausted,schedule_transition=transition,
            training_metrics=metrics.state(),rng=rng_state(),loader_census=census.state() if census else None))
        if census:write_json(args.output/'loader-census.json',census.state())

    def finish_pass():
        nonlocal epoch,cursor,exhausted
        epoch+=1;cursor=(0,0,0);exhausted=False;scheduler.next_pass()
        if census:census.last_epoch=epoch;census.last_cursor=list(cursor)
        checkpoint(f'checkpoint_pass_{epoch:03d}.pt');checkpoint('checkpoint_last.pt')

    captured=None
    if getattr(args,'cuda_step_replay',False):
        from .cuda_step import CudaStep
        if components is not None:CudaStep=components.CudaStep
        captured=CudaStep(model,args.batch_size,metrics=metrics,
            attention_backend=getattr(args,'student_attention_backend','default'))
    if not args.resume:checkpoint('checkpoint_last.pt')
    source=None;reported_time=time.perf_counter();reported_count=metrics.count
    # Keep append ownership across the run. Reopening this growing journal per
    # block forces Windows sharing/scanning work onto the input-prefetch path.
    journal=(args.output/'eligibility.jsonl').open('a',encoding='utf-8',buffering=1)
    try:
        while epoch<args.epochs:
            model.train()
            if phase=='train':
                def report(value):
                    journal.write(json.dumps(dict(epoch=epoch+1,**value))+'\n')
                # Recover a crash after an interval's final update, BEFORE taking
                # another update. Never replay a validation already committed.
                due=step>last_validation_step and step>0 and step%args.validation_every==0
                exhausted=False
                if not due:
                    source=device_batches(batches(units,0,teacher,teacher_sha,args,
                        cursor=cursor,report=report),args.device,getattr(args,'gpu_prefetch',False))
                    timing=dict(source_wait=0.,compute_guard=0.,optimizer_metrics=0.,reporting=0.,checkpoint=0.)
                    timing_steps=0;timing_started=fetch_mark=time.perf_counter()
                    try:
                        for tokens,meta,target,next_cursor,origins,liquid in source:
                            batch_count=target_size(target)
                            received=time.perf_counter();timing['source_wait']+=received-fetch_mark
                            if (args.output/'STOP').exists():
                                checkpoint('checkpoint_last.pt');return 'stopped'
                            optimizer.param_groups[0]['lr']=scheduler.value()
                            if captured is not None and batch_count==args.batch_size:
                                predicted,loss,norm=captured(tokens,meta,target)
                                if not (torch.isfinite(loss)&torch.isfinite(norm)):
                                    raise FloatingPointError('Nonfinite captured loss or gradient norm')
                            else:
                                optimizer.zero_grad(set_to_none=True)
                                with autocast(args):
                                    predicted,ids=model.training_prediction(tokens,meta);loss=loss_function(predicted,target,ids)
                                if not torch.isfinite(loss):raise FloatingPointError('Nonfinite future loss')
                                loss.backward()
                                norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
                            computed=time.perf_counter();timing['compute_guard']+=computed-received
                            optimizer.step();step+=1;scheduler.updates+=1;cursor=next_cursor
                            if census:census.commit(target[TAG],step,epoch,cursor)
                            if captured is not None and batch_count==args.batch_size:metrics.count+=batch_count
                            else:metrics.add(predicted,target,tokens[:,7])
                            optimized=time.perf_counter();timing['optimizer_metrics']+=optimized-computed
                            if step%args.log_every==0:
                                reporter.log(dict(train_loss=float(loss.detach()),**{'train/loss':float(loss.detach())},gradient_norm=float(norm),
                                    learning_rate=optimizer.param_groups[0]['lr'],full_pass=epoch+1),step)
                                now=time.perf_counter()
                                reporter.update(stage='training',epoch=epoch+1,epochs=args.epochs,
                                    epoch_kind='full dataset pass',data_pass=epoch+1,step=step,
                                    done=metrics.count,validation_every_updates=args.validation_every,
                                    interval_origins=metrics.count,pass_fraction=progress.fraction(cursor),
                                    total_training_blocks=int(progress.prefix[-1]),
                                    updates_since_validation=step-max(0,last_validation_step),
                                    learning_rate=optimizer.param_groups[0]['lr'],loader_census_origins=census.origins if census else 0,
                                    samples_per_second=(metrics.count-reported_count)/max(.001,now-reported_time))
                                reported_time,reported_count=now,metrics.count
                            reported=time.perf_counter();timing['reporting']+=reported-optimized
                            if step%args.checkpoint_every==0:checkpoint('checkpoint_last.pt')
                            checkpointed=time.perf_counter();timing['checkpoint']+=checkpointed-reported
                            timing_steps+=1
                            if timing_steps>=100:
                                reporter.update(pipeline_timing=dict(
                                    steps=timing_steps,seconds=checkpointed-timing_started,
                                    ms_per_update={k:1000*v/timing_steps for k,v in timing.items()}))
                                timing=dict.fromkeys(timing,0.);timing_steps=0;timing_started=time.perf_counter()
                            fetch_mark=time.perf_counter()
                            if step%args.validation_every==0:due=True;break
                        else:exhausted=True
                    finally:source.close();source=None
                if not metrics.count:
                    if exhausted and last_validation_step==step:
                        # The preceding validation happened exactly at EOF.
                        # Mark the completed pass without duplicate validation.
                        finish_pass();continue
                    raise ValueError('Training selection has no eligible origins')
                phase='validation';checkpoint('checkpoint_last.pt')
            mean=metrics.baseline() if hasattr(metrics,'baseline') else metrics.moments[2]/metrics.count
            validation=evaluate(model,units,teacher,teacher_sha,args,mean,reporter,step)
            if validation is None:checkpoint('checkpoint_last.pt');return 'stopped'
            improved=validation['loss']<best
            best=min(best,validation['loss']);stale=0 if improved else stale+1
            bounds=scheduler.bounds(epoch)
            history.append(dict(epoch=epoch+1,epoch_kind='full dataset pass',validation_index=len(history)+1,
                train=metrics.result(),validation=validation,step=step,data_pass=epoch+1,
                pass_complete=exhausted,epoch_start_learning_rate=bounds[0],epoch_min_learning_rate=bounds[1],
                last_update_learning_rate=optimizer.param_groups[0]['lr']))
            guard_review=None
            if components is not None and hasattr(components,'learning_guard'):
                guard_review=components.learning_guard(epoch=epoch,cursor=cursor,exhausted=exhausted,
                    history=history,stale=stale)
                history[-1]['learning_guard']=guard_review
            reporter.log(dict({f'validation_{k}':v for k,v in validation.items()},
                **(census.metrics() if census else {}),
                **({f'learning_guard/{k}':v for k,v in guard_review.items()
                    if type(v) in (int,float,bool)} if guard_review else {}),
                validation_is_epoch_end=exhausted,full_pass=epoch+1,validation_index=len(history)),step)
            write_json(args.output/'results.json',history)
            scheduler.validation(1. if exhausted else progress.fraction(cursor))
            last_validation_step=step;phase='train';metrics.reset()
            reported_time,reported_count=time.perf_counter(),0
            checkpoint('checkpoint_validation_last.pt');checkpoint('checkpoint_last.pt')
            if improved:checkpoint('checkpoint_best.pt')
            if exhausted:finish_pass()
            if (guard_review['stop'] if guard_review is not None else stale>=args.patience):
                write_json(args.output/'learning-review.json',dict(action='stop',reason='validation patience',
                    full_pass=epoch+1,patience=args.patience,**({'coverage':guard_review} if guard_review else {})))
                (args.output/'STOP').write_text('LEARNING_GUARD_STOP: validation patience\n')
                return 'learning_guard_stop'
        return 'complete'
    except KeyboardInterrupt:return 'interrupted_preserved_last_checkpoint'
    finally:
        try:
            if source is not None:source.close()
        finally:journal.close()


