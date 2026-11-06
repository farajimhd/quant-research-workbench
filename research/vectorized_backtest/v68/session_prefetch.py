"""Bounded CPU-only preparation ahead of persistent GPU replay workers."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from threading import Event
from time import monotonic, perf_counter
import psutil
from .preparation import reserve_host_load, release_host_load


class SessionPrefetch:
    def __init__(self, load, *, workers=3, capacity=6, reserve_gib=128.,
                 admission_gib=32., snapshot=psutil.virtual_memory, report=lambda x:None):
        if not 1<=workers<=8 or not workers<=capacity<=32:raise ValueError('Bounded preparation queue required')
        self.load=load;self.capacity=capacity;self.reserve_gib=reserve_gib
        self.admission_gib=admission_gib;self.snapshot=snapshot;self.report=report
        self.pool=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='v67-cpu-prepare')
        self.pending=OrderedDict();self.retry_after={};self.cancel=Event();self.closed=False

    def _load(self,day,cancel):
        began=perf_counter();budget=None;item=None
        info=dict(day=day,cpu_only=True,outcome='deferred')
        try:
            available=self.snapshot().available
            budget,required=reserve_host_load(available,self.reserve_gib,self.admission_gib,speculative=True)
            info.update(available_before_bytes=available,required_before_bytes=required)
            if budget is None or cancel.is_set():return None
            item=self.load(day)
            if item.device.type!='cpu':raise ValueError('Preparation thread accessed a CUDA session')
            # Preparation never pins memory, allocates CUDA tensors, or uploads.
            if cancel.is_set():item.close();item=None;return None
            info.update(outcome='ready',available_after_bytes=self.snapshot().available)
            return item
        except BaseException as error:
            if item is not None:item.close()
            info.update(outcome='failed',error=repr(error));raise
        finally:
            if budget is not None:release_host_load(budget)
            info['load_seconds']=perf_counter()-began;self.report(info)

    def schedule(self,days,cached=()):
        if self.closed:raise RuntimeError('Preparation queue is closed')
        now=monotonic()
        for day in days:
            if day in cached:continue
            future=self.pending.get(day)
            if future is not None:
                if not future.done():continue
                if future.result() is not None:continue
                del self.pending[day];self.retry_after[day]=now+2.
            if len(self.pending)>=self.capacity:break
            if now<self.retry_after.get(day,0.):continue
            self.pending[day]=self.pool.submit(self._load,day,self.cancel)

    def take(self,day):
        future=self.pending.pop(day,None)
        return None if future is None else future.result()

    def ready_day(self,days,cached,check_stop):
        """Dispatch a completed session rather than waiting on an earlier load."""
        while True:
            check_stop();self.schedule(days,cached)
            for day in days:
                if day in cached:return day
                future=self.pending.get(day)
                if future is not None and future.done() and future.result() is not None:return day
            pending=[self.pending[d] for d in days if d in self.pending and not self.pending[d].done()]
            if not pending:return days[0]  # RAM admission deferred; demand guard owns retry.
            wait(pending,timeout=.2,return_when=FIRST_COMPLETED)

    def stats(self):
        return dict(preparation_queued=sum(not f.running() and not f.done() for f in self.pending.values()),
                    preparation_running=sum(f.running() for f in self.pending.values()),
                    preparation_ready=sum(f.done() and f.exception() is None and f.result() is not None for f in self.pending.values()))

    def clear(self):
        self.cancel.set();errors=[]
        for future in self.pending.values():
            if future.cancel():continue
            try:
                item=future.result()
                if item is not None:item.close()
            except BaseException as error:errors.append(error)
        self.pending.clear();self.cancel=Event()
        if errors:raise errors[0]

    def close(self):
        if self.closed:return
        try:self.clear()
        finally:self.closed=True;self.pool.shutdown(wait=True,cancel_futures=True)
