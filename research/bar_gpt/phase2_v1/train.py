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
from .data import checked_unit, plan_block
from .model import Forecaster, future_loss
from .tokenizer import SessionTokens


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
    if path.name.startswith('checkpoint_epoch_') and path.exists():
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


def batches(units, split, teacher, teacher_sha, args, *, cursor=(0, 0, 0), report=None):
    """Stable chronological session/block/origin traversal; no random sample dropping."""
    for ui, unit in enumerate(units):
        if ui < cursor[0] or not any(b[1] == split for b in unit.blocks):continue
        reader, session = checked_unit(unit), None
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
                yield token, meta, target, (ui, bi, stop), plan.origins[start:stop], liquid
        del session


@torch.no_grad()
def evaluate(model, units, teacher, teacher_sha, args, mean, reporter, step):
    state = rng_state()
    metrics = Metrics(args.device)
    model.eval()
    try:
        for tokens, metadata, target, _, _, _ in batches(units, 1, teacher, teacher_sha, args):
            if (args.output / 'STOP').exists():return None
            with autocast(args):_, predicted = model(tokens, metadata)
            if not torch.isfinite(predicted).all():raise FloatingPointError('Nonfinite validation prediction')
            metrics.add(predicted, target, tokens[:, 7])
            reporter.update(stage='validation', done=metrics.count, step=step)
        return metrics.result(mean)
    finally:
        restore_rng(state)
        model.train()


def autocast(args):
    return torch.autocast('cuda', dtype=torch.bfloat16) if (
        str(args.device).startswith('cuda') and args.precision == 'bf16') else nullcontext()


def train(model, teacher, teacher_sha, units, contract, args, reporter):
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=.01)
    scheduler = EpochCosine(args.learning_rate, args.min_learning_rate, args.epochs, args.warmup_updates)
    epoch = step = stale = 0
    cursor, phase, best, history = (0, 0, 0), 'train', math.inf, []
    metrics = Metrics(args.device)
    if args.resume:
        saved = load_checkpoint(args.resume, contract)
        model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        scheduler.load_state_dict(saved['scheduler'])
        epoch, step, cursor, phase = saved['epoch'], saved['step'], tuple(saved['cursor']), saved['phase']
        best, history, stale = saved['best'], saved['history'], saved['stale']
        metrics.restore(saved['training_metrics']);restore_rng(saved['rng'])

    def checkpoint(name):
        save_checkpoint(args.output / name, dict(contract=contract, model=model.state_dict(),
            optimizer=optimizer.state_dict(), scheduler=scheduler.state_dict(), epoch=epoch,
            step=step, cursor=cursor, phase=phase, best=best, stale=stale, history=history,
            training_metrics=metrics.state(), rng=rng_state()))

    reported_time,reported_count = time.perf_counter(),metrics.count
    if not args.resume:checkpoint('checkpoint_last.pt')
    try:
        while epoch < args.epochs:
            scheduler.epoch = epoch
            model.train()
            if phase == 'train':
                def report(value):
                    with (args.output / 'eligibility.jsonl').open('a') as out:
                        out.write(json.dumps(dict(epoch=epoch+1, **value))+'\n')
                for tokens, meta, target, next_cursor, origins, liquid in batches(
                        units, 0, teacher, teacher_sha, args, cursor=cursor, report=report):
                    if (args.output / 'STOP').exists():
                        checkpoint('checkpoint_last.pt');return 'stopped'
                    optimizer.param_groups[0]['lr'] = scheduler.value()
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
                        reporter.log(dict(train_loss=float(loss.detach()), gradient_norm=float(norm)), step)
                        now=time.perf_counter()
                        reporter.update(stage='training', epoch=epoch+1, epochs=args.epochs,
                                        done=metrics.count, step=step,
                                        samples_per_second=(metrics.count-reported_count)/max(.001,now-reported_time))
                        reported_time,reported_count=now,metrics.count
                    if step % args.checkpoint_every == 0:checkpoint('checkpoint_last.pt')
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
            history.append(dict(epoch=epoch+1, train=training, validation=validation, step=step))
            reporter.log({f'validation_{k}':v for k,v in validation.items()}, step)
            write_json(args.output/'results.json', history)
            epoch += 1;cursor = (0, 0, 0);phase = 'train';metrics = Metrics(args.device)
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
