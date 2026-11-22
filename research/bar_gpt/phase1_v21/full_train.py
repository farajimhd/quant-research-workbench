"""Full-data joint MAE training with verified stateful recovery."""
import gc
import itertools
import json
import math
import time
import numpy as np
import torch
from .full_step import backward, qualify
from .step import PARTS
from .validate import Accumulator, Replay as ValidationReplay
from .experiment import evaluate as development_evaluate
from ..phase0_v3.experiment import cache
from ..phase1_v12.model import positions
from ..phase1_v11.pipeline import loader, DeviceBatches
from ..phase2_v2.train import rng_state, restore_rng, save_checkpoint, load_checkpoint
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json
from ..phase1_v18.quality import score
from ..phase1_v20.encoders import export


def learning_rate(windows, total, peak, floor, warmup_windows):
    if windows < warmup_windows:
        return floor + (peak-floor)*windows/max(1, warmup_windows)
    progress = min(1., (windows-warmup_windows)/max(1, total-warmup_windows))
    return floor + .5*(peak-floor)*(1+math.cos(math.pi*progress))


def quality(metrics, reference):
    base, details = score(metrics, reference)
    bins = [1-v/100 for k, v in metrics.items()
            if k.startswith(('masked/range/', 'all_visible/range/')) and k.endswith('bin_accuracy_pct')]
    if not bins:
        raise ValueError('Missing range metrics')
    return float(.9*base + .1*sum(bins)/len(bins)), details


@torch.no_grad()
def validate(model, manifest, a, reporter, step, epoch):
    state = rng_state(); model.eval()
    source = device = replay = None
    accumulator = Accumulator(); count = 0
    total = manifest['counts']['validation']; start = time.perf_counter()
    generator = torch.Generator(device='cuda').manual_seed(917)
    try:
        reporter.update(stage='validation startup', validation_windows=0, validation_total=total)
        source = loader(manifest, a, 'validation'); device = DeviceBatches(source, 'cuda')
        startup = time.perf_counter()-start
        for index in range(len(source)):
            if (a.output/'STOP').exists():
                return None
            batch = device.pop(); pos = positions(len(batch.grid), 'cuda', 16, generator)
            if len(batch.grid) == a.validation_batch_size:
                if replay is None:
                    replay = ValidationReplay(model, accumulator, batch, pos)
                replay(batch, pos)
            else:
                accumulator.add(model, batch, pos)
            count += len(batch.grid)
            if index % 25 == 0:
                elapsed = time.perf_counter()-start; rate = count/elapsed
                reporter.update(stage='full validation', validation_windows=count, validation_total=total,
                                validation_windows_per_second=rate, validation_eta_seconds=(total-count)/max(1, rate),
                                validation_loader_wait_seconds=device.wait_seconds)
            if index+1 < len(source):
                device.advance()
        if count != total:
            raise ValueError('Incomplete validation population')
        detail, metrics = accumulator.result()
        if any(not math.isfinite(float(v)) for v in metrics.values()) or any(
                v for k, v in metrics.items() if 'nonfinite' in k):
            raise FloatingPointError('Nonfinite validation')
        seconds = time.perf_counter()-start
        result = dict(metrics=metrics, windows=count, full_population=True, epoch=epoch+1, step=step,
                      seconds=seconds, startup_seconds=startup, loader_wait_seconds=device.wait_seconds)
        write_json(a.output/f'validation-{epoch+1:03}.json', result)
        write_json(a.output/'validation-latest.json', result)
        reporter.log({'validation/'+k:v for k,v in metrics.items()}, step)
        reporter.log({'validation/windows_per_second':count/seconds, 'validation/seconds':seconds,
                      'validation/loader_wait_seconds':device.wait_seconds}, step)
        reporter.update(validation_windows=count, validation_eta_seconds=0,
                        latest_evaluation=brief(metrics, 'full validation', step, seconds))
        return metrics
    finally:
        if device: device.close()
        if source: close_loader(source)
        del replay, accumulator
        model.train(); restore_rng(state)


def brief(metrics, scope, step, seconds):
    return dict(scope=scope, step=step, seconds=seconds,
                close_mae_1s=metrics['all_visible/raw_mae/1s/trade_close'],
                masked_close_mae_1s=metrics['masked/raw_mae/1s/trade_close'],
                candle_direction_1s=metrics['all_visible/candle/1s/trade/moving_direction_pct'],
                volume_direction_1s=metrics['activity/all_visible/1s/trade_volume/moving_direction_pct'],
                daily_bid_range=metrics['all_visible/range/daily/bid/bin_accuracy_pct'])


def train(model, teacher, manifest, a, contract, reporter):
    groups=[]
    for prefix,multiplier in (('bar.',a.bar_lr_multiplier),('temporal.',a.temporal_lr_multiplier),('decoder.',1.)):
        groups.append(dict(params=[p for n,p in model.named_parameters() if n.startswith(prefix)],
                           lr=a.learning_rate*multiplier,lr_multiplier=multiplier))
    opt = torch.optim.AdamW(groups, lr=a.learning_rate, weight_decay=0., fused=True)
    scale = torch.ones(6, 6, device='cuda')  # unused by joint_repaired; retained API
    step=epoch=cursor=epoch_done=windows=0; phase='train'; best=float('inf')
    if a.resume:
        saved=load_checkpoint(a.resume, contract)
        model.load_state_dict(saved['model']); opt.load_state_dict(saved['optimizer'])
        step,epoch,cursor,epoch_done,windows,phase,best=(saved[k] for k in ('step','epoch','cursor','epoch_done','windows','phase','best'))
        cursor=cursor[1]; restore_rng(saved['rng'])
    def checkpoint(name='checkpoint_last.pt'):
        save_checkpoint(a.output/name, dict(contract=contract, model=model.state_dict(), optimizer=opt.state_dict(),
            rng=rng_state(), step=step, epoch=epoch, cursor=(epoch,cursor), epoch_done=epoch_done,
            windows=windows, phase=phase, best=best,
            scheduler=dict(kind='warmup_cosine_windows', consumed=windows, total=a.epochs*manifest['counts']['train'])))
        reporter.update(checkpoint_step=step)
    # Development-only cache is small and remains fixed across evaluations.
    reporter.update(stage='loading development cache')
    state=rng_state()
    dev=cache(np.load(a.data/'development.npy', mmap_mode='r'))
    with torch.no_grad():
        dev=(*dev,torch.cat([teacher.encode(dev[0][i:i+512],dev[1][i:i+512]) for i in range(0,len(dev[0]),512)]))
    restore_rng(state)
    def development():
        state=rng_state(); reporter.update(stage='development validation')
        try:
            detail, metrics=development_evaluate(model,teacher,dev)
            write_json(a.output/'development-latest.json',dict(step=step,metrics=metrics))
            reporter.log({'development/'+k:v for k,v in metrics.items()}, step)
            reporter.update(latest_evaluation=brief(metrics,'development',step,metrics['evaluation_seconds']))
            return metrics
        finally: restore_rng(state)
    replays={}; model.train()
    try:
        if not a.resume:
            reference=development(); write_json(a.output/'development-reference.json',reference)
            best=quality(reference,reference)[0]; checkpoint(); checkpoint('checkpoint_best.pt')
        else: reference=json.loads((a.output/'development-reference.json').read_text())
        while epoch<a.epochs:
            if phase=='train':
                reporter.update(stage='loading / restoring traversal',epoch=epoch+1,epochs=a.epochs,step=step)
                state=rng_state(); source=loader(manifest,a,'train',epoch); device=None
                try:
                    began=time.perf_counter()
                    device=DeviceBatches(itertools.islice(source,cursor,None),'cuda'); restore_rng(state)
                    reporter.update(loader_startup_seconds=time.perf_counter()-began)
                    tick=time.perf_counter(); before=windows; wait=device.wait_seconds
                    for index in range(cursor,len(source)):
                        if (a.output/'STOP').exists(): checkpoint(); return 'stopped'
                        batch=device.pop(); pos=positions(len(batch.grid),'cuda',64 if (step+1)%4==0 else 16)
                        lr=learning_rate(windows,a.epochs*manifest['counts']['train'],a.learning_rate,a.min_learning_rate,
                                         a.warmup_steps*a.batch_size)
                        for group in opt.param_groups: group['lr']=lr*group['lr_multiplier']
                        if len(batch.grid)==a.batch_size:
                            if pos.shape[1] not in replays:
                                replays[pos.shape[1]],actual=qualify(model,teacher,batch,pos,scale)
                            else: actual=replays[pos.shape[1]](batch,pos)
                        else:
                            opt.zero_grad(set_to_none=True); actual=backward(model,teacher,batch,pos,scale)
                        value,parts,norm=actual
                        if index+1<len(source): device.advance()
                        if not bool(torch.isfinite(value)&torch.isfinite(norm)):
                            raise FloatingPointError('Nonfinite loss/gradient; preserving last committed checkpoint')
                        opt.step(); step+=1; cursor=index+1; epoch_done+=len(batch.grid); windows+=len(batch.grid)
                        if step%25==0:
                            elapsed=time.perf_counter()-tick; speed=(windows-before)/max(.001,elapsed)
                            reporter.log({'train/loss':float(value),'train/learning_rate':lr,'train/gradient_norm':float(norm),
                                'train/bar_learning_rate':lr*a.bar_lr_multiplier,'train/temporal_learning_rate':lr*a.temporal_lr_multiplier,
                                'train/windows_per_second':speed,'train/loader_wait_seconds':device.wait_seconds-wait,
                                **{'train/'+k:float(v) for k,v in zip(PARTS,parts)}},step)
                            reporter.update(stage='training',step=step,epoch=epoch+1,epochs=a.epochs,epoch_done=epoch_done,
                                epoch_total=manifest['counts']['train'],windows=windows,loss=float(value),learning_rate=lr,
                                windows_per_second=speed,epoch_eta_seconds=(manifest['counts']['train']-epoch_done)/max(1,speed),
                                loader_wait_seconds=device.wait_seconds-wait,loader_wait_total_seconds=device.wait_seconds)
                            tick=time.perf_counter();before=windows;wait=device.wait_seconds
                        if step%a.validation_every==0:
                            measured=development(); current,details=quality(measured,reference)
                            reporter.log({'quality/score':current,**{'quality/'+k:v for k,v in details.items()}},step)
                            if current<best: best=current;checkpoint('checkpoint_best.pt')
                            # Explicit emergency bound only; lesser regressions remain reported.
                            if details['worst_raw_ratio']>10:
                                write_json(a.output/'quality-alert.json',dict(step=step,reason='10x raw-MAE group regression',details=details))
                                checkpoint();return 'quality_attention'
                            tick=time.perf_counter();before=windows;wait=device.wait_seconds
                        if step%a.checkpoint_every==0: checkpoint()
                        if a.max_updates and step>=a.max_updates: checkpoint();return 'qualification_complete'
                    if epoch_done!=manifest['counts']['train']: raise ValueError('Incomplete epoch')
                    phase='validation';checkpoint()
                finally:
                    if device:device.close()
                    close_loader(source)
            measured=validate(model,manifest,a,reporter,step,epoch)
            if measured is None:checkpoint();return 'stopped'
            full_reference=json.loads(a.full_reference.read_text())['metrics']
            q,details=quality(measured,full_reference)
            reporter.log({'quality/full_score':q,**{'quality/full_'+k:v for k,v in details.items()}},step)
            epoch+=1;cursor=epoch_done=0;phase='train';checkpoint()
        if windows!=a.epochs*manifest['counts']['train']: raise ValueError('Training population incomplete')
        export(model,contract,a.output/'encoder-exports')
        return 'complete'
    except KeyboardInterrupt:
        checkpoint();return 'stopped'
    finally:
        replays.clear();gc.collect()
