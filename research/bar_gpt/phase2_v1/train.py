"""Deterministic rolling-origin batches, full-state recovery and validation."""
from contextlib import contextmanager, nullcontext
import json
import math
import os
from pathlib import Path
import random
import time
import uuid
import numpy as np
import torch
from torch.nn import functional as F
from ..v5.campaign_v4 import EpochCosine
from ..v5.checkpoint_transition_v1 import state_hash
from ..v5.shards.contract import digest, file_hash, write_json
from .data import checked_unit, plan_block, unit_readers
from .model import Forecaster, future_loss
from .tokenizer import SessionTokens
from .metrics import DecodedMetrics
from .device_pipeline import device_batches
from .schedule import ValidationCosine


@contextmanager
def lease(root):
    """One process per output; no process signalling or stale-lock takeover."""
    with (Path(root) / 'training.lock').open('a+b') as stream:
        stream.write(b'0'); stream.flush(); stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(stream, fcntl.LOCK_UN)


def rng_state():
    n = np.random.get_state()
    return dict(python=random.getstate(), numpy=(n[0],n[1].tolist(),n[2],n[3],n[4]),
                torch=torch.get_rng_state(), cuda=torch.cuda.get_rng_state_all()
                if torch.cuda.is_initialized() else [])


def restore_rng(state):
    random.setstate(state['python'])
    n=state['numpy'];np.random.set_state((n[0],np.asarray(n[1],dtype=np.uint32),n[2],n[3],n[4]))
    torch.set_rng_state(state['torch'])
    if state['cuda']:torch.cuda.set_rng_state_all(state['cuda'])


def save_checkpoint(path, state):
    """Preserve parent on failed writes, fsync/reload and independently hash file."""
    tensors=list(state['model'].values())+[v for row in state['optimizer']['state'].values()
                                          for v in row.values() if isinstance(v,torch.Tensor)]
    if any(not torch.isfinite(v).all() for v in tensors):
        raise FloatingPointError('Refusing nonfinite model/optimizer checkpoint')
    path = Path(path)
    if path.name.startswith(('checkpoint_epoch_','checkpoint_pass_')) and path.exists():
        existing=torch.load(path,map_location='cpu',weights_only=False)
        if state_hash(existing)!=state_hash(state):
            raise FileExistsError('Immutable epoch checkpoint collision')
        # A replayed validation may reach an already committed epoch. Preserve
        # the exact file; this also completes a crash after file rename/before proof.
        proof=path.with_suffix('.proof.json')
        if proof.exists():
            load_checkpoint(path,state['contract'])
        else:
            write_json(proof,dict(sha256=file_hash(path),contract=digest(state['contract']),
                                 state_sha256=state_hash(state),step=state['step'],cursor=state['cursor']))
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.partial')
    with temporary.open('wb') as stream:
        torch.save(state, stream); stream.flush(); os.fsync(stream.fileno())
    loaded = torch.load(temporary, map_location='cpu', weights_only=False)
    full_hash=state_hash(state)
    if state_hash(loaded)!=full_hash:
        raise ValueError('Checkpoint serialization readback failed')
    sha = file_hash(temporary)
    temporary.replace(path)
    if file_hash(path) != sha:raise ValueError('Checkpoint target readback changed')
    write_json(path.with_suffix('.proof.json'), dict(sha256=sha, contract=digest(state['contract']),
                                                  state_sha256=full_hash,step=state['step'], cursor=state['cursor']))


def load_checkpoint(path, contract):
    path = Path(path)
    proof = json.loads(path.with_suffix('.proof.json').read_text())
    if file_hash(path) != proof['sha256']:raise ValueError('Resume checkpoint hash changed')
    state = torch.load(path, map_location='cpu', weights_only=False)
    if digest(state['contract']) != digest(contract) or proof['contract'] != digest(contract):
        raise ValueError('Resume requires exact model, teacher, dataset and optimization contract')
    if state['step'] != proof['step'] or list(state['cursor']) != proof['cursor']:
        raise ValueError('Resume cursor/proof differs')
    if state_hash(state)!=proof['state_sha256']:
        raise ValueError('Resume full-state hash differs')
    return state


class Metrics:
    def __init__(self, device):
        self.count = 0
        self.sums = torch.zeros(3, device=device, dtype=torch.float64)
        self.moments = torch.zeros(4, 512, device=device, dtype=torch.float64)

    @torch.no_grad()
    def add(self, predicted, target, persistence):
        p, t = predicted.float(), target.float()
        self.count += len(t)
        self.sums += torch.stack(((p-t).square().mean(1).sum(),
                                  F.cosine_similarity(p, t).sum(),
                                  (persistence.float()-t).square().mean(1).sum())).double()
        self.moments += torch.stack((p.double().sum(0), p.double().square().sum(0),
                                    t.double().sum(0), t.double().square().sum(0)))

    def result(self, baseline=None):
        if not self.count:raise ValueError('No eligible origins in the selected split')
        mse, cosine, persistence = (self.sums / self.count).cpu().tolist()
        p, pp, t, tt = self.moments / self.count
        result = dict(origins=self.count, mse=mse, cosine=cosine, persistence_mse=persistence,
                      prediction_variance=float((pp-p.square()).mean()),
                      target_variance=float((tt-t.square()).mean()))
        if baseline is not None:
            m = baseline.to(t.device).double()
            result['training_mean_mse'] = float((tt-2*m*t+m.square()).mean())
        return result

    def state(self):
        return dict(count=self.count, sums=self.sums.cpu(), moments=self.moments.cpu())

    def restore(self, state):
        self.count = state['count']; self.sums.copy_(state['sums']); self.moments.copy_(state['moments'])


def source_batches(units, split, teacher, teacher_sha, args, *, cursor=(0, 0, 0), report=None,
            reconstruction=False):
    """Stable chronological session/block/origin traversal; no random sample dropping."""
    if getattr(args,'prepare_workers',0):
        from .publication_pipeline import publication_batches
        yield from publication_batches(units,split,teacher,teacher_sha,args,cursor=cursor,
                                        report=report,reconstruction=reconstruction)
        return
    for ui,unit,reader in unit_readers(units,split,cursor[0],workers=getattr(args,'io_workers',0),
                                     prefetch_mib=getattr(args,'prefetch_mib',2048)):
        session=None
        for bi, (ordinal, s, liquid, selection) in enumerate(unit.blocks):
            if s != split or (ui == cursor[0] and bi < cursor[1]):continue
            plan = plan_block(reader, ordinal)
            if report is not None:report(dict(root=str(unit.root), session=str(unit.path),
                                             split=s, liquid=liquid, selection=selection, **plan.report))
            begin = cursor[2] if (ui, bi) == cursor[:2] else 0
            if not 0 <= begin <= len(plan.origins):raise ValueError('Resume origin cursor out of bounds')
            if begin == len(plan.origins):continue
            if session is None:
                session = SessionTokens(reader, teacher, teacher_sha, device=args.device,
                                        precision=args.precision, memory_mib=args.cache_mib,
                                        token_batch=args.token_batch)
            session.prepare(plan)
            for start in range(begin, len(plan.origins), args.batch_size):
                stop = min(start+args.batch_size, len(plan.origins))
                token, meta, target = session.batch(plan, start, stop)
                row = (token, meta, target, (ui, bi, stop), plan.origins[start:stop], liquid)
                yield row + (session.reconstruction(plan,start,stop),) if reconstruction else row
        del session


def batches(units,split,teacher,teacher_sha,args,*,cursor=(0,0,0),report=None,reconstruction=False):
    """Pack consecutive examples; each example stays inside its own source block.

    The cursor records the exact consumed source suffix, including a partially
    consumed unit when a packed optimizer batch crosses a publication boundary.
    """
    source=source_batches(units,split,teacher,teacher_sha,args,cursor=cursor,report=report,
                          reconstruction=reconstruction)
    if not getattr(args,'pack_batches',True):
        yield from source;return
    chunks=[];count=0;last=None
    def packed():
        token,meta,target=(torch.cat([c[i] for c in chunks]) for i in range(3))
        origins=np.concatenate([c[3] for c in chunks]);liquid=np.concatenate([c[4] for c in chunks])
        row=(token,meta,target,last,origins,liquid)
        if reconstruction:
            row+=(dict((k,torch.cat([c[5][k] for c in chunks])) for k in chunks[0][5]),)
        return row
    try:
        for row in source:
            token,meta,target,next_cursor,origins,liquid=row[:6];start=0
            while start<len(target):
                take=min(args.batch_size-count,len(target)-start);end=start+take
                part=[token[start:end],meta[start:end],target[start:end],origins[start:end],
                      np.full(take,liquid,dtype=np.int8)]
                if reconstruction:part.append({k:v[start:end] for k,v in row[6].items()})
                chunks.append(part);count+=take
                last=(*next_cursor[:2],next_cursor[2]-len(target)+end)
                start=end
                if count==args.batch_size:
                    yield packed();chunks=[];count=0
        if count:yield packed()
    finally:
        source.close()


@torch.no_grad()
def evaluate(model, units, teacher, teacher_sha, args, mean, reporter, step):
    state = rng_state()
    metrics = Metrics(args.device)
    decoded = DecodedMetrics(args.device)
    decoder = teacher.reconstruction_decoder
    if decoder.training or any(p.requires_grad for p in decoder.parameters()):
        raise ValueError('Validation requires the frozen same-checkpoint decoder')
    was_training = model.training
    model.eval()
    source=device_batches(batches(units,1,teacher,teacher_sha,args,reconstruction=True),
                           args.device,getattr(args,'gpu_prefetch',False))
    try:
        for tokens, metadata, target, _, _, _, evidence in source:
            if (args.output / 'STOP').exists():return None
            with autocast(args):_, predicted = model(tokens, metadata)
            if not torch.isfinite(predicted).all():raise FloatingPointError('Nonfinite validation prediction')
            metrics.add(predicted, target, tokens[:, 7])
            decoded.add(decoder, predicted, target, evidence)
            reporter.update(stage='validation', done=metrics.count, step=step)
        return dict(metrics.result(mean), **decoded.result())
    finally:
        try:source.close()
        finally:
            restore_rng(state)
            model.train(was_training)


def autocast(args):
    return torch.autocast('cuda', dtype=torch.bfloat16) if (
        str(args.device).startswith('cuda') and args.precision == 'bf16') else nullcontext()


def optimizer_for(model,args):
    backend=getattr(args,'optimizer_backend','auto')
    if backend not in ('auto','fused'):raise ValueError('Unknown optimizer backend')
    if backend=='fused' and not str(args.device).startswith('cuda'):
        raise ValueError('Fused phase-two optimizer requires CUDA')
    return torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=.01,
                            **({'fused':True} if backend=='fused' else {}))


def train(model, teacher, teacher_sha, units, contract, args, reporter):
    if getattr(args,'validation_every',0)>0:
        from .full_pass import train_full_pass
        return train_full_pass(model,teacher,teacher_sha,units,contract,args,reporter)
    optimizer = optimizer_for(model,args)
    validation_every=getattr(args,'validation_every',0)
    logical_epochs=validation_every>0
    scheduler = (ValidationCosine(args.learning_rate,args.min_learning_rate,validation_every,
                                  args.warmup_updates,getattr(args,'lr_restart_decay',.9))
                 if logical_epochs else
                 EpochCosine(args.learning_rate,args.min_learning_rate,args.epochs,args.warmup_updates))
    epoch = step = stale = 0
    cursor, phase, best, history = (0, 0, 0), 'train', math.inf, []
    interval_history, last_validation_step = [], -1
    data_pass=1
    metrics = Metrics(args.device)
    if args.resume:
        saved = load_checkpoint(args.resume, contract)
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        scheduler.load_state_dict(saved['scheduler'])
        epoch, step, cursor, phase = saved['epoch'], saved['step'], tuple(saved['cursor']), saved['phase']
        if scheduler.updates!=step:raise ValueError('Optimizer/scheduler step ledgers differ')
        best, history, stale = saved['best'], saved['history'], saved['stale']
        interval_history = saved.get('interval_history',[])
        last_validation_step = saved.get('last_validation_step',-1)
        data_pass=saved.get('data_pass',1)
        metrics.restore(saved['training_metrics']);restore_rng(saved['rng'])

    def checkpoint(name):
        save_checkpoint(args.output / name, dict(contract=contract, model=model.state_dict(),
            optimizer=optimizer.state_dict(), scheduler=scheduler.state_dict(), epoch=epoch,
            step=step, cursor=cursor, phase=phase, best=best, stale=stale, history=history,
            interval_history=interval_history,last_validation_step=last_validation_step,
            data_pass=data_pass,
            training_metrics=metrics.state(), rng=rng_state()))

    reported_time,reported_count = time.perf_counter(),metrics.count
    captured=None
    if getattr(args,'cuda_step_replay',False):
        from .cuda_step import CudaStep
        captured=CudaStep(model,args.batch_size)
    if not args.resume:checkpoint('checkpoint_last.pt')
    source=None
    try:
        while epoch < args.epochs:
            scheduler.epoch = epoch
            model.train()
            cycle_end=(epoch+1)*validation_every if logical_epochs else None
            if logical_epochs and step>cycle_end:raise ValueError('Checkpoint passed its validation boundary')
            if phase == 'train':
                def report(value):
                    with (args.output / 'eligibility.jsonl').open('a') as out:
                        out.write(json.dumps(dict(epoch=epoch+1, **value))+'\n')
                before_step=step;started_at_beginning=cursor==(0,0,0)
                # A checkpoint may be committed immediately after the final
                # update, before phase='validation'. Do not take another step.
                source=((row for row in ()) if logical_epochs and step==cycle_end else
                        device_batches(batches(units,0,teacher,teacher_sha,args,cursor=cursor,report=report),
                                       args.device,getattr(args,'gpu_prefetch',False)))
                for tokens, meta, target, next_cursor, origins, liquid in source:
                    if (args.output / 'STOP').exists():
                        checkpoint('checkpoint_last.pt');return 'stopped'
                    optimizer.param_groups[0]['lr'] = scheduler.value()
                    if captured is not None and len(target)==args.batch_size:
                        predicted,loss,norm=captured(tokens,meta,target)
                        if not (torch.isfinite(loss) & torch.isfinite(norm)):
                            raise FloatingPointError('Nonfinite captured loss or gradient norm')
                    else:
                        optimizer.zero_grad(set_to_none=True)
                        with autocast(args):
                            _, predicted = model(tokens, meta)
                            loss = future_loss(predicted, target)
                        if not torch.isfinite(loss):raise FloatingPointError('Nonfinite future loss')
                        loss.backward()
                        norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
                    optimizer.step();scheduler.updates += 1;step += 1;cursor = next_cursor
                    metrics.add(predicted, target, tokens[:, 7])
                    if step % args.log_every == 0:
                        reporter.log(dict(train_loss=float(loss.detach()), gradient_norm=float(norm),
                                          learning_rate=optimizer.param_groups[0]['lr']), step)
                        now=time.perf_counter()
                        reporter.update(stage='training', epoch=epoch+1, epochs=args.epochs,
                                        done=metrics.count, step=step,
                                        data_pass=data_pass,cycle_updates=validation_every,
                                        cycle_done=step-epoch*validation_every if logical_epochs else None,
                                        learning_rate=optimizer.param_groups[0]['lr'],
                                        samples_per_second=(metrics.count-reported_count)/max(.001,now-reported_time))
                        reported_time,reported_count=now,metrics.count
                    if step % args.checkpoint_every == 0:checkpoint('checkpoint_last.pt')
                    if logical_epochs and step==cycle_end:break
                source.close();source=None
                if logical_epochs and step<cycle_end:
                    if step==before_step and started_at_beginning:
                        raise ValueError('Training selection has no eligible origins')
                    # Exhausted the full data pass before the logical boundary.
                    # Continue the SAME cycle and LR position from data start.
                    cursor=(0,0,0);data_pass+=1;checkpoint('checkpoint_last.pt');continue
                if not metrics.count:raise ValueError('Training selection has no eligible origins')
                phase = 'validation'
                checkpoint('checkpoint_last.pt')  # durable before a potentially long validation
            mean = metrics.moments[2]/metrics.count
            validation = evaluate(model, units, teacher, teacher_sha, args, mean, reporter, step)
            if validation is None:
                checkpoint('checkpoint_last.pt');return 'stopped'
            training = metrics.result()
            improved = validation['mse'] < best
            best = min(best, validation['mse']);stale = 0 if improved else stale+1
            bounds=scheduler.bounds(epoch) if logical_epochs else (None,None)
            history.append(dict(epoch=epoch+1,train=training,validation=validation,step=step,
                                data_pass=data_pass,epoch_start_learning_rate=bounds[0],
                                epoch_min_learning_rate=bounds[1],
                                last_update_learning_rate=optimizer.param_groups[0]['lr']))
            reporter.log(dict({f'validation_{k}':v for k,v in validation.items()},
                              validation_is_epoch_end=True),step)
            write_json(args.output/'results.json', history)
            last_validation_step=step
            epoch+=1
            if not logical_epochs:cursor=(0,0,0);data_pass+=1
            phase='train';metrics=Metrics(args.device)
            reported_time,reported_count=time.perf_counter(),0
            checkpoint(f'checkpoint_epoch_{epoch:03d}.pt');checkpoint('checkpoint_last.pt')
            if improved:checkpoint('checkpoint_best.pt')
            if stale >= args.patience:
                write_json(args.output/'learning-review.json', dict(action='stop', reason='validation patience',
                                                                   epoch=epoch, patience=args.patience))
                (args.output/'STOP').write_text('LEARNING_GUARD_STOP: validation patience\n')
                return 'learning_guard_stop'
        return 'complete'
    except KeyboardInterrupt:
        # An interrupt may arrive during an optimizer operation; preserve the last
        # already committed full-state checkpoint instead of publishing torn state.
        return 'interrupted_preserved_last_checkpoint'
    finally:
        if source is not None:source.close()
