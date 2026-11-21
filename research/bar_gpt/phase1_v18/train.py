import itertools,json,math,time
import numpy as np
import torch
from ..phase1_v12.model import positions
from ..phase1_v12.data import prepare
from .step import backward,Replay,InferenceReplay
from ..phase1_v17.metrics import FullMetrics as Metrics,FullMetricsReplay as MetricsReplay,full_scalars as scalars
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..phase1_v7.full_data import prepare_raw
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json
from .quality import score as quality_score


@torch.no_grad()
def evaluate(model,teacher,manifest,a,reporter,step,rows=None,limit_batches=None):
    was=model.training;model.eval();source=device=replay=metric_replay=None;start=time.perf_counter()
    generator=torch.Generator(device='cuda').manual_seed(917)
    if rows is not None and limit_batches is not None:raise ValueError('Ambiguous evaluation scope')
    stage='profile validation' if limit_batches else ('development' if rows is not None else 'full validation')
    total=len(rows) if rows is not None else manifest['counts']['validation']
    if limit_batches:total=min(total,limit_batches*a.validation_batch_size)
    scope='profile_validation' if limit_batches else ('development' if rows is not None else 'validation')
    reference_path=a.output/('teacher-'+scope+'.json')
    reference=json.loads(reference_path.read_text()) if reference_path.exists() else None
    if reference and any(r['windows']!=total for r in reference.values()):raise ValueError('Teacher baseline population mismatch')
    metrics=Metrics(teacher_reference=reference)
    reporter.update(stage='starting '+stage,validation_windows=0,validation_total=total,step=step)
    try:
        if rows is None:
            source=loader(manifest,a,'validation');device=DeviceBatches(source,'cuda')
            def batches():
                while True:
                    try:b=device.pop()
                    except StopIteration:return
                    yield b;device.advance()
            stream=itertools.islice(batches(),limit_batches) if limit_batches else batches()
        else:stream=(prepare_raw(rows[i:i+a.batch_size],True).to('cuda') for i in range(0,len(rows),a.batch_size))
        startup=time.perf_counter()-start
        for index,b in enumerate(stream):
            if (a.output/'STOP').exists():return None
            pos=positions(len(b.grid),'cuda',16,generator)
            if a.graphs and len(b.grid)==a.validation_batch_size:
                if replay is None:
                    z,meta,valid,x,v=prepare(teacher,b)
                    expected=model(z,meta,pos);expected_all=model(z,meta,positions(len(z),'cuda',64))
                    replay=InferenceReplay(model,teacher,b,pos)
                    actual=replay(b,pos)
                    torch.testing.assert_close(actual[5],expected,rtol=1e-4,atol=2e-5)
                    torch.testing.assert_close(actual[6],expected_all,rtol=1e-4,atol=2e-5)
                data=replay(b,pos)
            else:
                z,meta,valid,x,v=prepare(teacher,b)
                data=(z,meta,valid,x,v,model(z,meta,pos),model(z,meta,positions(len(z),'cuda',64)))
            if a.metric_graphs and replay is not None and len(b.grid)==a.validation_batch_size:
                if metric_replay is None and metrics.windows==0:metric_replay=MetricsReplay(metrics,model,teacher,replay)
                if metric_replay:metric_replay()
                else:metrics.add(model,teacher,b,data,pos)
            else:metrics.add(model,teacher,b,data,pos)
            if index%25==0:
                elapsed=time.perf_counter()-start;speed=metrics.windows/max(.001,elapsed)
                reporter.update(stage=stage,validation_windows=metrics.windows,validation_total=total,
                                validation_windows_per_second=speed,validation_eta_seconds=(total-metrics.windows)/max(1,speed))
        elapsed=time.perf_counter()-start
        result=metrics.result();result.update(seconds=elapsed,startup_seconds=startup,pipeline_seconds=elapsed-startup,
                                             full_population=rows is None and limit_batches is None,scope=scope)
        if result['windows']!=total:raise ValueError('Incomplete validation population')
        if result['nonfinite']:raise FloatingPointError('Nonfinite validation reconstruction')
        if reference is None:write_json(reference_path,{k:v for k,v in result['raw'].items() if k.startswith('teacher_')})
        measured=scalars(result)
        measured.update(seconds=result['seconds'],windows_per_second=total/max(.001,result['seconds']))
        if not all(math.isfinite(float(x)) for x in measured.values()):raise FloatingPointError('Nonfinite validation metrics')
        name=scope
        write_json(a.output/(name+'-latest.json'),result)
        reporter.log({name+'/'+k:v for k,v in measured.items()},step)
        reporter.update(stage=stage,validation_windows=total,validation_eta_seconds=0,
                        latest_evaluation=dict(scope=name,step=step,masked_loss=measured['masked/all/latent_loss'],
                        all_visible_loss=measured['all_visible/all/latent_loss'],constant_loss=measured['masked/all/constant_loss'],
                        visible_mean_loss=measured['masked/all/visible_mean_loss'],
                        close_mae_1s=measured.get('all_visible/raw_mae/1s/trade_close'),
                        teacher_close_mae_1s=measured.get('teacher_all_visible/raw_mae/1s/trade_close'),
                        candle_direction_1s=measured.get('all_visible/candle/1s/trade/moving_direction_pct'),
                        volume_direction_1s=measured.get('activity/all_visible/1s/trade_volume/moving_direction_pct'),seconds=result['seconds']))
        return result,measured
    finally:
        if device:device.close()
        if source:close_loader(source)
        model.train(was)


def train(model,teacher,manifest,a,contract,reporter):
    optimizer=torch.optim.AdamW(model.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True)
    step=epoch=cursor=epoch_done=windows=0;phase='train';best=float('inf');regressions=0
    if a.resume:
        saved=load_checkpoint(a.resume,contract);model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        step=saved['step'];epoch=saved['epoch'];cursor=saved['cursor'][1];epoch_done=saved['epoch_done'];windows=saved['windows'];phase=saved['phase'];best=saved['best'];restore_rng(saved['rng'])
        regressions=saved.get('regressions',0)
    def checkpoint(name='checkpoint_last.pt'):
        save_checkpoint(a.output/name,dict(contract=contract,model=model.state_dict(),optimizer=optimizer.state_dict(),rng=rng_state(),
            step=step,epoch=epoch,cursor=(epoch,cursor),epoch_done=epoch_done,windows=windows,phase=phase,best=best,regressions=regressions,
            scheduler=dict(kind='warmup_cosine_by_consumed_windows',progress=windows/(a.epochs*manifest['counts']['train']),peak=a.learning_rate,floor=a.min_learning_rate)))
        reporter.update(checkpoint_step=step)
    development=np.load(a.data/'development.npy',mmap_mode='r');model.train();teacher.eval();replay={}
    try:
        if not a.resume:
            initial=evaluate(model,teacher,manifest,a,reporter,step,development)
            if initial is None:checkpoint();return 'stopped'
            write_json(a.output/'development-reference.json',initial[1])
            checkpoint()
        reference=json.loads((a.output/'development-reference.json').read_text())
        initial_score=quality_score(reference,reference)[0]
        if not a.resume:best=initial_score;checkpoint('checkpoint_best.pt')
        full_reference=json.loads(a.full_reference.read_text())['metrics']
        while epoch<a.epochs:
            if phase=='train':
                reporter.update(stage='loading / restoring traversal',step=step,epoch=epoch+1,epochs=a.epochs)
                source=loader(manifest,a,'train',epoch);expected=len(source);device=None
                try:
                    state=rng_state();began=time.perf_counter();device=DeviceBatches(itertools.islice(source,cursor,None),'cuda');restore_rng(state)
                    reporter.update(loader_startup_seconds=time.perf_counter()-began)
                    measure=time.perf_counter();measure_windows=windows;measure_wait=device.wait_seconds
                    for index in range(cursor,expected):
                        if (a.output/'STOP').exists():checkpoint();return 'stopped'
                        b=device.pop();pos=positions(len(b.grid),'cuda',64 if (step+1)%4==0 else 16)
                        progress=windows/(a.epochs*manifest['counts']['train'])
                        lr=a.min_learning_rate+.5*(a.learning_rate-a.min_learning_rate)*(1+math.cos(math.pi*progress))
                        lr*=min(1.,(step+1)/a.warmup_steps)
                        for group in optimizer.param_groups:group['lr']=lr
                        if a.graphs and len(b.grid)==a.batch_size:
                            if pos.shape[1] not in replay:replay[pos.shape[1]]=Replay(model,teacher,b,pos)
                            loss,parts,norm=replay[pos.shape[1]](b,pos)
                        else:
                            optimizer.zero_grad(set_to_none=True);loss,parts,norm=backward(model,teacher,b,pos)
                        device.advance()
                        if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite update')
                        optimizer.step();step+=1;cursor=index+1;epoch_done+=len(b.grid);windows+=len(b.grid)
                        if step%25==0:
                            speed=(windows-measure_windows)/max(.001,time.perf_counter()-measure)
                            recent_wait=device.wait_seconds-measure_wait
                            reporter.log({'train/loss':float(loss),'train/body_loss':float(parts[0]),'train/activity_log_mse':float(parts[1]),'train/activity_delta_mse':float(parts[2]),'train/activity_tail':float(parts[3]),
                                          'train/learning_rate':lr,'train/gradient_norm':float(norm),'train/windows_per_second':speed,
                                          'train/loader_wait_seconds':recent_wait,'train/loader_wait_total_seconds':device.wait_seconds,'train/gpu_reserved_mib':torch.cuda.memory_reserved()/2**20},step)
                            reporter.update(stage='training',step=step,epoch=epoch+1,epochs=a.epochs,epoch_done=epoch_done,
                                            epoch_total=manifest['counts']['train'],loss=float(loss),windows_per_second=speed,learning_rate=lr,
                                            epoch_eta_seconds=(manifest['counts']['train']-epoch_done)/max(1,speed),loader_wait_seconds=recent_wait)
                            measure=time.perf_counter();measure_windows=windows;measure_wait=device.wait_seconds
                        if step%a.validation_every==0:
                            result=evaluate(model,teacher,manifest,a,reporter,step,development)
                            if result is None:checkpoint();return 'stopped'
                            score,details=quality_score(result[1],reference)
                            reporter.log({'quality/score':score,**{'quality/'+k:v for k,v in details.items()}},step)
                            if score<best:best=score;checkpoint('checkpoint_best.pt')
                            regressions=regressions+1 if score>1.3*initial_score or details['worst_raw_ratio']>10 else 0
                            if regressions>=2:
                                write_json(a.output/'quality-stop.json',dict(step=step,score=score,reference_score=initial_score,details=details))
                                checkpoint();return 'stopped'
                            measure=time.perf_counter();measure_windows=windows;measure_wait=device.wait_seconds
                        if step%a.checkpoint_every==0:checkpoint()
                        if a.max_updates and step>=a.max_updates:checkpoint();return 'qualification_complete'
                    if epoch_done!=manifest['counts']['train']:raise ValueError('Incomplete epoch')
                    phase='validation';checkpoint()
                finally:
                    if device:device.close()
                    close_loader(source)
            result=evaluate(model,teacher,manifest,a,reporter,step)
            if result is None:checkpoint();return 'stopped'
            write_json(a.output/f'validation-{epoch+1:03}.json',result[0])
            full_score,full_details=quality_score(result[1],full_reference)
            baseline_score=quality_score(full_reference,full_reference)[0]
            reporter.log({'quality/full_score':full_score,**{'quality/full_'+k:v for k,v in full_details.items()}},step)
            if full_score>1.3*baseline_score or full_details['worst_raw_ratio']>10 or (epoch==0 and full_score>=.995*baseline_score):
                write_json(a.output/'quality-stop.json',dict(epoch=epoch+1,step=step,score=full_score,reference_score=baseline_score,details=full_details,reason='Full validation regression or no0.5% balanced-score improvement after first complete pass'))
                checkpoint();return 'stopped'
            epoch+=1;cursor=epoch_done=0;phase='train';checkpoint()
        if windows!=a.epochs*manifest['counts']['train']:raise ValueError('Training population incomplete')
        torch.save(dict(config=vars(model.config),arm='scaled_slots_price',encoder=model.encoder.state_dict(),contract=contract),a.output/'encoder.pt')
        return 'complete'
    except KeyboardInterrupt:checkpoint();return 'stopped'
