"""One admitted next session: verified results and bounded host transfer staging."""
import json,time,hashlib
from pathlib import Path
import psutil
import torch
from research.vectorized_backtest.v42.prefetch import CPUOnlyPrefetch
from research.vectorized_backtest.v42.io_pipeline import TransferStaging as BaseStaging


def fingerprint(path):
    s=Path(path).stat();return s.st_size,s.st_mtime_ns


def read_results(path):
    before=fingerprint(path);record=json.loads(Path(path).read_bytes())
    if record.get('format')=='v57-receipt-index-v1':
        raw=Path(record['receipt']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['receipt_sha256']:raise ValueError('Indexed result receipt integrity mismatch')
        receipt=json.loads(raw);keys=receipt['program_keys'];metrics=receipt['metrics']
        if receipt['result_identity']!=record['identity'] or any(len(v)!=len(keys) for v in metrics.values()):raise ValueError('Indexed result alignment changed')
        record['results']={key:{k:v[i] for k,v in metrics.items() if k!='result_reused'} for i,key in enumerate(keys)}
        if fingerprint(path)!=before:raise ValueError('Result cache changed while preparing')
        return before,record
    raw=json.dumps(record['results'],sort_keys=True,separators=(',',':'),allow_nan=False)
    if hashlib.sha256(raw.encode()).hexdigest()!=record['results_sha256']:raise ValueError('Result cache integrity mismatch')
    if fingerprint(path)!=before:raise ValueError('Result cache changed while preparing')
    return before,record


def prepare_next(item,cache_path,config):
    try:
        if config.get('reuse_results',True) and Path(cache_path).exists():
            item.prefetched_results=(str(cache_path),*read_results(cache_path))
        groups=[item.host_tensors,item.resident_features.host]
        size=sum((v.numel()*v.element_size()+255)//256*256 for g in groups for v in g.values())
        required=(config.get('host_reserve_gib',128.)+config.get('host_admission_gib',32.))*2**30
        if config.get('prefetch_transfer',False) and psutil.virtual_memory().available>=required+size:
            arena=torch.empty(size,dtype=torch.uint8,pin_memory=True);offset=0;staged=[]
            for group in groups:
                values={}
                for key,value in group.items():
                    n=value.numel()*value.element_size();target=arena[offset:offset+n].view(value.dtype).view(value.shape)
                    target.copy_(value);values[key]=target;offset+=(n+255)//256*256
                staged.append(values)
            item.prefetched_transfer=(arena,staged)
        return item
    except BaseException:
        item.close();raise


class NextSessionPrefetch(CPUOnlyPrefetch):
    def reconsider(self,day,load):
        if day is None:return
        now=time.monotonic()
        if getattr(self,'retry_day',None)!=day:self.retry_day=day;self.retry_count=0;self.retry_after=0.
        if now<self.retry_after:return
        self.retry_after=now+5.
        if self.future is not None:
            if not self.future.done():return
            # result() propagates preparation/certificate failures.
            if self.future.result() is not None:return
            self.take(day)
        if self.retry_count<3 and self.snapshot().available>=self.required:
            self.retry_count+=1;self.start(day,load)


class TransferStaging(BaseStaging):
    def activate(self,ready,device):
        device=torch.device(device)
        if device.type!='cuda' or len(ready)!=1 or not getattr(ready[0][1],'prefetched_transfer',None):return super().activate(ready,device)
        data=ready[0][1];arena,(thin,resident)=data.prefetched_transfer
        stream=torch.cuda.Stream(device=device)
        with torch.cuda.stream(stream):
            data.tensors={k:v.to(device,non_blocking=True) for k,v in thin.items()}
            bank=data.resident_features;bank.tensors={k:v.to(device,non_blocking=True) for k,v in resident.items()}
            bank.device=device;bank.column_plans.clear();data.device=device
            event=torch.cuda.Event();event.record(stream)
        torch.cuda.current_stream(device).wait_event(event)
        for tensor in (*data.tensors.values(),*bank.tensors.values()):tensor.record_stream(torch.cuda.current_stream(device))
        event.synchronize() # The single pinned staging slot is now safe to release.
        data.prefetched_transfer=None
