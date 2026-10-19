"""One CPU-only speculative load; demand admission remains authoritative."""
from threading import Event
from time import perf_counter
import psutil


class CPUOnlyPrefetch:
    def __init__(self,pool,*,reserve_gib,admission_gib,snapshot=psutil.virtual_memory,report=lambda value:None):
        self.pool=pool;self.required=(reserve_gib+admission_gib)*2**30
        self.snapshot=snapshot;self.report=report;self.future=None;self.day=None;self.cancel=None
        self.last=None

    def start(self,day,load):
        if self.future is not None:raise RuntimeError('Only one prefetched session is allowed')
        self.day=day;self.cancel=Event();cancel=self.cancel
        def read():
            started=perf_counter();before=self.snapshot().available;item=None
            info=dict(day=day,required_available_bytes=self.required,available_before_bytes=before,cpu_only=True)
            try:
                if cancel.is_set() or before<self.required:
                    info['outcome']='deferred';return None
                item=load(day)
                if item.device.type!='cpu':raise ValueError('Prefetch must not activate CUDA')
                after=self.snapshot().available;info['available_after_bytes']=after
                if cancel.is_set() or after<self.required:
                    item.close();item=None;info['outcome']='released';return None
                info['outcome']='ready';return item
            except BaseException as error:
                if item is not None:item.close()
                info.update(outcome='failed',error=repr(error));raise
            finally:
                info['load_seconds']=perf_counter()-started;self.report(info)
        self.future=self.pool.submit(read)

    def take(self,day):
        if self.future is None:return None
        future=self.future;matched=day==self.day
        if not matched:self.cancel.set()
        try:
            item=future.result()
            self.last=dict(day=self.day,used=matched and item is not None)
            if not matched and item is not None:item.close();return None
            return item
        finally:self.future=None;self.day=None;self.cancel=None

    def release_under_pressure(self):
        if self.future is not None and self.snapshot().available<self.required:
            self.cancel.set()
            if self.future.done():self.take(None)

    def close(self):
        if self.future is not None:
            self.cancel.set()
            if self.future.cancel():self.future=None;self.day=None;self.cancel=None
            else:self.take(None) # Propagate failed loads; never hide certificate errors.
