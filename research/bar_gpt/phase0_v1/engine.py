import itertools,math,time
import numpy as np
import torch
from .model import forward_batch,objective
from .metrics import Metrics,scalars
from ..phase1_v11.pipeline import loader,DeviceBatches
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v10.step import stable_clip
from ..phase2_v2.train import rng_state,restore_rng,save_checkpoint,load_checkpoint
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import write_json


def backward(model,b):
    out,x,v=forward_batch(model,b);loss,parts=objective(model,out,x,v);loss.backward()
    return loss.detach(),parts.detach(),stable_clip(model.parameters())


class Replay:
    def __init__(self,model,b):
        self.batch=b.map(lambda x:x.clone());self.graph=torch.cuda.CUDAGraph();stream=torch.cuda.Stream();state=rng_state()
        stream.wait_stream(torch.cuda.current_stream())
        try:
            with torch.cuda.stream(stream):
                for _ in range(3):model.zero_grad(set_to_none=True);backward(model,self.batch)
            torch.cuda.current_stream().wait_stream(stream);model.zero_grad(set_to_none=True)
            with torch.cuda.graph(self.graph,stream=stream,capture_error_mode='thread_local'):self.output=backward(model,self.batch)
            self.params=list(model.parameters());self.grads=[p.grad for p in self.params]
        finally:restore_rng(state)
    def __call__(self,b):
        copied=set()
        for name in ('grid','slots','valid','frames'):
            dest=getattr(self.batch,name)
            if dest.data_ptr() not in copied:dest.copy_(getattr(b,name),non_blocking=True)
            copied.add(dest.data_ptr())
        for p,g in zip(self.params,self.grads):p.grad=g
        self.graph.replay();return self.output


@torch.no_grad()
def evaluate(model,manifest,a,reporter,step,rows=None):
    was=model.training;model.eval();metric=Metrics();source=None;device=None;start=time.perf_counter()
    try:
        if rows is None:
            source=loader(manifest,a,'validation');device=DeviceBatches(source,'cuda')
            def stream():
                while True:
                    try:b=device.pop()
                    except StopIteration:return
                    yield b;device.advance()
            iterator=stream()
        else:iterator=(prepare_raw(rows[i:i+a.batch_size],True).to('cuda') for i in range(0,len(rows),a.batch_size))
        for index,b in enumerate(iterator):
            if (a.output/'STOP').exists():return None
            out,x,v=forward_batch(model,b);metric.add(model,out,x,v,b)
            if index%25==0:
                elapsed=time.perf_counter()-start
                reporter.update(stage='development' if rows is not None else 'full validation',step=step,validation_windows=metric.windows,
                    validation_total=len(rows) if rows is not None else manifest['counts']['validation'],validation_bars_per_second=64*metric.windows/max(.001,elapsed))
        result=metric.result();result.update(seconds=time.perf_counter()-start,full_population=rows is None)
        if result['nonfinite']:raise FloatingPointError('Nonfinite validation output')
        if rows is None and result['windows']!=manifest['counts']['validation']:raise ValueError('Validation population incomplete')
        if rows is not None and result['windows']!=len(rows):raise ValueError('Development incomplete')
        write_json(a.output/('development-latest.json' if rows is not None else 'validation-latest.json'),result)
        measured=scalars(result)
        reporter.log({('development/' if rows is not None else 'validation/')+k:v for k,v in measured.items()},step)
        reporter.update(latest_evaluation=dict(scope='development' if rows is not None else 'full validation',step=step,loss=result['loss'],
            trade_close_mae_1s=measured.get('raw_mae/1s/trade_close'),candle_direction_1s=measured.get('candle/1s/trade/moving_direction_pct'),
            range_accuracy_1s=measured.get('range/1s/trade/magnitude_accuracy_pct'),seconds=result['seconds']))
        return result
    finally:
        if device:device.close()
        if source:close_loader(source)
        model.train(was)


def train(model,manifest,a,contract,reporter):
    optimizer=torch.optim.AdamW(model.parameters(),lr=a.learning_rate,weight_decay=0.,fused=True)
    step=epoch=cursor=epoch_done=windows=0;phase='train';best=float('inf')
    if a.resume:
        saved=load_checkpoint(a.resume,contract);model.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer'])
        step=saved['step'];epoch=saved['epoch'];cursor=saved['cursor'][1];epoch_done=saved['epoch_done'];windows=saved['windows'];phase=saved['phase'];best=saved['best'];restore_rng(saved['rng'])
    def checkpoint(name='checkpoint_last.pt'):
        save_checkpoint(a.output/name,dict(contract=contract,model=model.state_dict(),optimizer=optimizer.state_dict(),rng=rng_state(),
            step=step,epoch=epoch,cursor=(epoch,cursor),epoch_done=epoch_done,windows=windows,phase=phase,best=best,
            scheduler=dict(kind='cosine_by_consumed_windows',progress=windows/(a.epochs*manifest['counts']['train']),peak=a.learning_rate,floor=a.min_learning_rate)))
        reporter.update(checkpoint_step=step)
    development=np.load(a.data/'development.npy',mmap_mode='r');model.train();replay=None
    try:
        if not a.resume:
            evaluate(model,manifest,a,reporter,step,development);checkpoint()
        while epoch<a.epochs:
            if phase=='train':
                source=loader(manifest,a,'train',epoch);expected_batches=len(source);device=None
                try:
                    state=rng_state();began=time.perf_counter();device=DeviceBatches(itertools.islice(source,cursor,None),'cuda');restore_rng(state)
                    seen=0;measure=time.perf_counter();measure_windows=windows
                    for index in range(cursor,expected_batches):
                        if (a.output/'STOP').exists():checkpoint();return 'stopped'
                        b=device.pop()
                        progress=windows/(a.epochs*manifest['counts']['train'])
                        lr=a.min_learning_rate+.5*(a.learning_rate-a.min_learning_rate)*(1+math.cos(math.pi*progress))
                        for group in optimizer.param_groups:group['lr']=lr
                        if a.graphs and len(b.target)==a.batch_size:
                            if replay is None:replay=Replay(model,b)
                            loss,parts,norm=replay(b)
                        else:optimizer.zero_grad(set_to_none=True);loss,parts,norm=backward(model,b)
                        device.advance()
                        if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite update rejected')
                        optimizer.step();step+=1;cursor=index+1;epoch_done+=len(b.target);windows+=len(b.target);seen+=len(b.target)
                        if step%25==0:
                            elapsed=time.perf_counter()-measure;speed=(windows-measure_windows)/max(.001,elapsed)
                            reporter.log({'train/loss':float(loss),'train/price_loss':float(parts[0]),'train/activity_loss':float(parts[1]),
                                'train/coordinate_loss':float(parts[2]),'train/gradient_norm':float(norm),'train/learning_rate':lr,
                                'train/windows_per_second':speed,'train/bars_per_second':speed*64,'train/loader_wait_seconds':device.wait_seconds,
                                'train/gpu_reserved_mib':torch.cuda.memory_reserved()/2**20},step)
                            reporter.update(stage='training',step=step,epoch=epoch+1,epochs=a.epochs,epoch_done=epoch_done,epoch_total=manifest['counts']['train'],
                                total_bars=windows*64,bars_per_second=speed*64,windows_per_second=speed,loss=float(loss),learning_rate=lr,
                                epoch_eta_seconds=(manifest['counts']['train']-epoch_done)/max(1,speed),loader_wait_seconds=device.wait_seconds)
                            measure=time.perf_counter();measure_windows=windows
                        if step%a.validation_every==0:
                            result=evaluate(model,manifest,a,reporter,step,development)
                            if result is None:checkpoint();return 'stopped'
                            if result['loss']<best:best=result['loss'];checkpoint('checkpoint_best.pt')
                            measure=time.perf_counter();measure_windows=windows
                        if step%a.checkpoint_every==0:checkpoint()
                        if a.max_updates and step>=a.max_updates:checkpoint();return 'qualification_complete'
                    if epoch_done!=manifest['counts']['train']:raise ValueError('Incomplete training epoch')
                    phase='validation';checkpoint()
                finally:
                    if device:device.close()
                    close_loader(source)
            result=evaluate(model,manifest,a,reporter,step)
            if result is None:checkpoint();return 'stopped'
            write_json(a.output/f'validation-{epoch+1:03}.json',result)
            epoch+=1;cursor=epoch_done=0;phase='train';checkpoint()
        if windows!=a.epochs*manifest['counts']['train']:raise ValueError('Training population incomplete')
        torch.save(dict(config=vars(model.config),parameters=dict(mean=model.mean.cpu(),scale=model.scale.cpu()),encoder=model.encoder.state_dict(),contract=contract),a.output/'encoder.pt')
        return 'complete'
    except KeyboardInterrupt:checkpoint();return 'stopped'
