"""Independent daily evidence preparation, one month ahead of canonical updates."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from contextlib import closing
import re
from threading import Event,Lock,Semaphore,Thread,current_thread

from pipelines.certified_research import source
from pipelines.certified_research.contracts import literal
from pipelines.market_sip.events import native_reporting_repair as native
from . import retained_reporting,archive_staging


class Reservations:
    def __init__(self,paths,reserve_bytes,reserve_fraction):
        self.paths,self.reserve_bytes,self.reserve_fraction=paths,reserve_bytes,reserve_fraction
        self.lock=Lock();self.active={}

    def admit(self,key,size):
        with self.lock:
            free=archive_staging.space_budget(self.paths.cache,reserve_bytes=self.reserve_bytes,
                                              reserve_fraction=self.reserve_fraction)['usable_bytes']
            if sum(v for k,v in self.active.items() if k!=key)+size>free:
                raise ValueError('Combined SQL staging reservations exceed SSD headroom')
            self.active[key]=size

    def release(self,key):
        with self.lock:self.active.pop(key,None)


def phase(message):
    """Translate producer stages into truthful work units, not time estimates."""
    match=re.search(r'(reconcile|publication)=(\d+)/(\d+)',message)
    if match:return dict(stage='Match trades' if match[1]=='reconcile' else 'Publish evidence',
                         done=int(match[2]),total=int(match[3]),unit='buckets')
    for token,name in (('sizing','Measure CSV'),('materialize','Classify CSV'),
                       ('canonical verification','Read event keys'),('verify source file','Verify source'),
                       ('captured','Prepared')):
        if token in message:return dict(stage=name)
    return dict(stage='Prepare evidence')


class Preparation:
    """Each day owns its date-filtered staging rows and a private HTTP client.

    Only the controller mutates canonical flags. One preparing/prepared month
    may lead it; SQL futures and staging-space reservations are bounded.
    """
    def __init__(self,months,inventory,captured,paths,claim,stop,progress,client_factory,prefetch,
                 *,workers=2,query_threads=4,query_memory_bytes=8*1024**3,reserve_bytes=256*1024**3,
                 reserve_fraction=.10,prefetch_bytes=32*1024**3):
        if not 1<=workers<=4:raise ValueError('SQL preparation requires 1 to 4 workers')
        self.months,self.inventory,self.captured=months,inventory,set(captured)
        self.paths,self.claim,self.stop,self.progress=paths,claim,stop,progress
        self.client_factory,self.prefetch=client_factory,prefetch
        self.workers,self.query_threads,self.query_memory_bytes=workers,query_threads,query_memory_bytes
        self.reserve_bytes,self.reserve_fraction,self.prefetch_bytes=reserve_bytes,reserve_fraction,prefetch_bytes
        self.space=Reservations(paths,reserve_bytes,reserve_fraction)
        self.ready={month:Event() for month in months};self.lead=Semaphore(1);self.reserved=set()
        self.error=None;self.clients={};self.lock=Lock();self.prepared=len(self.captured)
        self.thread=Thread(target=self.run,name='repair-preparation',daemon=False)

    def __enter__(self):
        self.progress.update(prepared=self.prepared,sql_workers=self.workers)
        for n in range(self.workers):self.progress.worker(f'SQL_{n}','Waiting for work')
        self.thread.start();return self

    def one(self,day):
        worker=current_thread().name
        def emit(message):
            self.progress.worker(worker,day=day,detail=message,**phase(message))
            self.progress.emit(f'{worker} | {message}')
        try:
            with closing(self.client_factory()) as client:
                with self.lock:self.clients[worker]=client
                # ClickHouse owns part snapshots. Reconciliation compares only
                # preserved fields and event_meta & 63, so the coordinator's
                # high-bit mutation cannot change its matching inputs.
                # Corrected-flag verification still waits in the coordinator.
                self.progress.worker(worker,'Waiting for SSD',day=day,detail='Waiting for verified CSV copy')
                result=retained_reporting.recover(client.query,self.paths,self.inventory,(day,),self.claim,self.stop,
                    reserve_bytes=self.reserve_bytes,reserve_fraction=self.reserve_fraction,
                    maximum_chunk_bytes=self.prefetch_bytes,query_threads=self.query_threads,
                    query_memory_bytes=self.query_memory_bytes,emit=emit,staged_copy=self.prefetch.get,
                    capture_tape=False,capture_only=True,storage_admit=lambda n:self.space.admit(day,n))
                if tuple(d for d,_ in result['captured'])!=(str(day),):raise ValueError('Prepared day receipt missing')
                self.progress.worker(worker,'Prepared',day=day,detail='Evidence saved; awaiting flag update')
                with self.lock:
                    self.prepared+=1;self.progress.update(prepared=self.prepared)
                return day
        except BaseException as error:
            self.progress.worker(worker,'Interrupted' if isinstance(error,InterruptedError) else 'Failed',day=day,detail=str(error))
            raise
        finally:
            with self.lock:self.clients.pop(worker,None)
            self.space.release(day)

    def run(self):
        try:
            with ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='SQL') as pool:
                for month,days in self.months.items():
                    todo=[d for d in days if d not in self.captured]
                    if todo:
                        while not self.lead.acquire(timeout=1):
                            if self.stop.is_set():return
                        with self.lock:self.reserved.add(month)
                    pending=set()
                    while todo or pending:
                        if self.stop.is_set():raise InterruptedError('Preparation stopped')
                        while todo and len(pending)<self.workers:
                            pending.add(pool.submit(self.one,todo.pop(0)))
                        done,_=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
                        for future in done:
                            try:future.result()
                            except BaseException as error:
                                self.error=error;self.stop.set();raise
                            pending.remove(future)
                    self.ready[month].set()
        except BaseException as error:
            self.error=error;self.stop.set()
            self.progress.emit('Preparation failed: '+str(error))
        finally:
            for event in self.ready.values():event.set()

    def wait(self,month):
        while not self.ready[month].wait(1):
            if self.error:raise self.error
            if self.stop.is_set():raise InterruptedError('Preparation stopped')
        if self.error:raise self.error
        if self.stop.is_set():raise InterruptedError('Preparation stopped')

    def release(self,month):
        with self.lock:
            if month in self.reserved:
                self.reserved.remove(month);self.lead.release()

    def __exit__(self,*unused):
        self.stop.set();self.thread.join()


def repair_month(query,days,inventory,claim,stop,emit):
    """Only this coordinator submits canonical mutations; all inputs are frozen."""
    units=[]
    for day in days:
        rows=query(f'SELECT evidence_hash FROM q_live.{retained_reporting.RECEIPTS} '
            f'WHERE source_date=toDate({literal(day)}) AND source_digest={literal(inventory["source_hashes"][str(day)])}')
        if len(rows)!=1:raise ValueError('Monthly repair requires exactly one capture receipt per day')
        units.append((inventory['rows'][day],rows[0]['evidence_hash']))
    def guard():
        claim.check()
        if stop.is_set():raise InterruptedError('Stopped; submitted canonical mutations remain resumable')
        current=source.inventory(query,min(days),max(days),sessions=days)
        if current['missing'] or current['incomplete'] or any(
            current['source_hashes'].get(str(d))!=inventory['source_hashes'][str(d)] for d in days):
            raise ValueError('Canonical source changed during monthly reporting migration')
    while True:
        guard();result=native.batch_step(query,units,claim,guard)
        emit(f'Retained reporting {str(days[0])[:7]} | days={len(days)} | '
             f'{result["status"]} | parts={result.get("parts_remaining",0)}')
        if result['status']=='complete':return
        if stop.wait(10):raise InterruptedError('Stopped; monthly mutation retained for resume')
