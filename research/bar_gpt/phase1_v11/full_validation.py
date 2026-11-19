"""Reuse certified native-unit reducers; add predicted-reference recovery."""
from collections import deque
from types import SimpleNamespace
import time
import torch
from ..phase1_v10.train import Accumulator as NativeAccumulator
from .pipeline import loader,DeviceBatches
from ..phase1_v5_raw.engine import FRAME_NAMES
from ..v5.data_v3 import close_loader
from ..v5.shards.phase1_contract import FRAMES,write_json
from .full_step import forward,InferenceReplay
from .transforms import references
from .validation_objective import ObjectiveAccumulator


class Accumulator(NativeAccumulator):
    def __init__(self,visible):
        super().__init__();self.visible=visible;self.reference_sum=torch.zeros(6,9,device='cuda',dtype=torch.float64);self.reference_count=self.reference_sum.clone()
        self.frame_values=torch.tensor(FRAMES,device='cuda');self.adapter=None

    def add(self,model,out,values,valid,hidden,b):
        if self.adapter is None:
            self.adapter=SimpleNamespace(input_mean=torch.cat((torch.zeros(18,device='cuda'),model.encoder.time_mean)),input_scale=torch.cat((torch.ones(18,device='cuda'),model.encoder.time_scale)))
        super().add(self.adapter,(out['logs'],out['time'],out['validity']),values,valid,hidden,b)
        observed=valid[...,:18] if self.visible==64 else valid[...,:18]&(~hidden[...,None])
        target,keep=references(values[...,:18],observed,model.encoder.reference_mean)
        delta=out['references'].double()-target.double();error=delta.abs()
        error[:,0::3]=torch.expm1(delta[:,0::3]).abs()
        group=torch.nn.functional.one_hot((b.frames[:,None]==self.frame_values).long().argmax(1),6).T.double()
        self.reference_sum+=group@torch.where(keep,error,0);self.reference_count+=group@keep.double()

    def finish(self):
        result=super().finish();mean=(self.reference_sum/self.reference_count.clamp_min(1)).cpu()
        result['reference_error_by_timeframe']={name:mean[i].tolist() for i,name in enumerate(FRAME_NAMES)}
        result['reference_counts']=self.reference_count.long().cpu().tolist()
        result['reference_price_relative_mean']=float(mean[:,0::3].mean());result['reference_activity_log_mae']=float(mean[:,[1,2,4,5,7,8]].mean())
        return result


@torch.no_grad()
def evaluate(model,manifest,a,status,limit=0):
    model.eval();source=loader(manifest,a,'validation');device=None;acc={name:Accumulator(n) for name,n in (('masked',16),('all_visible',64))}
    generator=torch.Generator(device='cuda').manual_seed(731);started=time.perf_counter();n=0;recent=deque();stopped=False;graphs={}
    objectives={mode:ObjectiveAccumulator(value.visible) for mode,value in acc.items()}
    try:
        device=DeviceBatches(source,'cuda');startup=time.perf_counter()-started;wait=device.wait_seconds
        for i in range(limit or 2**63-1):
            if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():stopped=True;break
            try:b=device.pop()
            except StopIteration:break
            for mode,visible in (('masked',16),('all_visible',64)):
                if getattr(a,'validation_graphs',False) and len(b.target)==(a.validation_batch_size or a.batch_size):
                    if mode not in graphs:graphs[mode]=InferenceReplay(model,b,visible,generator,with_objective=True)
                    out,x,v,h,loss_parts=graphs[mode](b)
                else:out,x,v,h,loss_parts=forward(model,b,visible,generator,with_objective=True)
                acc[mode].add(model,out,x,v,h,b)
                objectives[mode].add_values(*loss_parts,len(x))
            device.advance();n+=len(b.target);now=time.perf_counter();recent.append((now,n))
            while len(recent)>2 and now-recent[0][0]>60:recent.popleft()
            rate=(n-recent[0][1])/max(.001,now-recent[0][0]) if len(recent)>1 else 0
            total=limit*(a.validation_batch_size or a.batch_size) if limit else manifest['counts']['validation']
            status(stage='bounded validation profile' if limit else 'masked + all-visible full validation',validation_done=n,validation_total=total,
                validation_windows_per_second=rate,validation_eta_seconds=max(0,total-n)/rate if rate else None,
                phase_startup_seconds=startup,phase_loader_wait_seconds=device.wait_seconds-wait,
                phase_loader_wait_fraction=(device.wait_seconds-wait)/max(.001,now-started-startup),gpu_reserved_mib=torch.cuda.memory_reserved()/2**20)
        if stopped and not n:return None
        if not stopped and not limit and n!=manifest['counts']['validation']:raise ValueError('Incomplete certified validation population')
        results={k:dict(v.finish(),**objectives[k].finish()) for k,v in acc.items()};result=dict(windows=n,full_population=not limit and not stopped,stopped=stopped,
            seconds=time.perf_counter()-started,startup_seconds=startup,loader_wait_seconds=device.wait_seconds-wait,metrics=results,
            scope='Hidden48 and all-visible64 source-valid reconstruction separately. Full validation uses all certified windows; relative quantiles are histogram approximations. Loss is the window-weighted mean of the unchanged training objective evaluated per batch; masked loss is primary.')
        write_json(a.output/'validation-latest.json',result)
        if any(v['nonfinite'] or v['negative'] or v['structure_violations'] for v in results.values()):raise FloatingPointError('Native validation failure; evidence retained')
        return None if stopped else result
    finally:
        for v in acc.values():v.pool.shutdown(wait=True)
        if device:device.close()
        close_loader(source)
