"""Durable worker snapshots and bounded concurrent SSD staging for reporting repair."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock, Thread, current_thread
from time import monotonic,sleep

from . import archive_staging


class Progress:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = RLock()
        self.last_write = 0.
        self.state = dict(step='trade-reporting-repair',status='starting',pid=os.getpid(),
                          workers={},completed=0,reused=0,failed=0,retried=0,queued=0,total=0)

    def update(self, **values):
        with self.lock:
            self.state.update(values)
            self.flush(force=True)

    def worker(self, worker, stage, *, day='', done=None, total=None, detail='',unit='bytes',kind=None):
        with self.lock:
            old = self.state['workers'].get(worker, {})
            now = monotonic()
            same = old.get('stage') == stage and old.get('day') == str(day)
            started = old.get('started', now) if same else now
            elapsed = now-started
            self.state['workers'][worker] = dict(stage=stage,day=str(day),done=done,total=total,
                detail=detail,started=started,elapsed_seconds=elapsed,unit=unit,kind=kind,
                rate_bytes_second=(done/elapsed if unit=='bytes' and done is not None and elapsed > 0 else None),
                updated_at=datetime.now(timezone.utc).isoformat())
            self.flush(force=not same)

    def emit(self, message):
        with self.lock:
            line = datetime.now(timezone.utc).isoformat()+' '+message
            print(line, flush=True)
            with (self.root/'events.log').open('a',encoding='utf-8') as stream:
                stream.write(line+'\n')
            self.state['message'] = message
            self.flush(force=True)

    def flush(self, force=False):
        if not force and monotonic()-self.last_write < 1:
            return
        self.state['updated_at'] = datetime.now(timezone.utc).isoformat()
        temporary = self.root/'status.tmp'
        temporary.write_text(json.dumps(self.state,indent=2,default=str),encoding='utf-8')
        # Windows readers can briefly hold status.json without FILE_SHARE_DELETE.
        # Keep the last complete snapshot visible and retry only sharing/access
        # races; permanent failures still propagate after this bounded window.
        for attempt in range(20):
            try:
                temporary.replace(self.root/'status.json');break
            except PermissionError as error:
                if getattr(error,'winerror',None) not in (5,32) or attempt==19:raise
                sleep(.05)
        self.last_write = monotonic()


class Prefetch:
    """Continuously stage up to max_bytes, including files leased to SQL.

    No SQL clients are shared with these threads. All workers are joined on exit.
    Cached files are hash-compared against G before they can be handed to repair.
    """
    def __init__(self, files, paths, stop, progress, *, workers=2, max_bytes=32*1024**3,
                 reserve_bytes=256*1024**3, reserve_fraction=.10):
        if not 1 <= workers <= 4 or max_bytes <= 0:
            raise ValueError('Invalid copy worker or staging budget')
        if any(info['bytes'] > max_bytes for _,info in files):
            raise ValueError('A trade file exceeds the explicit prefetch budget')
        self.todo = list(files)
        self.paths,self.stop,self.progress = paths,stop,progress
        self.workers,self.max_bytes = workers,max_bytes
        self.reserve_bytes,self.reserve_fraction = reserve_bytes,reserve_fraction
        self.pool = ThreadPoolExecutor(max_workers=workers,thread_name_prefix='COPY')
        self.pending = {}
        self.info=dict(files)
        self.leased=set()
        self.error=None
        self.bytes = 0
        self.lock=RLock()
        self.feeder=Thread(target=self.feed,name='copy-feeder',daemon=False)

    def __enter__(self):
        try:
            self.fill()
            self.feeder.start()
            return self
        except BaseException:
            self.close()
            raise

    def fill(self):
        # Caller owns the lock once the feeder has started. Bound active copies,
        # not the number of ready files; every resident byte stays reserved.
        while self.todo and sum(not f.done() for f,_ in self.pending.values()) < self.workers and not self.stop.is_set():
            day,info = self.todo[0]
            if self.bytes+info['bytes'] > self.max_bytes:
                break
            # Reserve the complete outstanding set before admitting more copies.
            space=archive_staging.space_budget(self.paths.cache,reserve_bytes=self.reserve_bytes,
                                               reserve_fraction=self.reserve_fraction)
            inflight=sum(size for future,size in self.pending.values() if not future.done())
            if inflight+info['bytes'] > space['usable_bytes']:break
            self.todo.pop(0)
            self.bytes += info['bytes']
            self.pending[day]=(self.pool.submit(self.copy,day,info),info['bytes'])

    def feed(self):
        try:
            while not self.stop.wait(1):
                with self.lock:
                    for future,_ in self.pending.values():
                        if future.done() and future.exception():raise future.exception()
                    self.fill()
                    values=dict(staging_bytes=self.bytes,staging_limit_bytes=self.max_bytes,
                        copy_queued=len(self.todo),staged_ready=sum(f.done() for d,(f,_) in self.pending.items() if d not in self.leased),
                        staging_in_use=len(self.leased),copy_active=sum(not f.done() for f,_ in self.pending.values()))
                    values['copy_queue_status']=('Copying' if values['copy_active'] else
                        'All files staged' if not self.todo else
                        'Cache full; waiting for SQL cleanup' if self.bytes+self.todo[0][1]['bytes']>self.max_bytes else
                        'Waiting for SSD headroom')
                    self.progress.update(**values)
        except BaseException as error:
            self.error=error;self.stop.set()
            self.progress.emit('Copy feeder failed: '+str(error))

    def copy(self, day, info):
        worker=current_thread().name
        target=self.paths.cache/'_retained_reporting'/info['key']
        last_log=[0.,None]
        def report(stage, done=0, total=info['bytes']):
            self.progress.worker(worker,stage,day=day,done=done,total=total,detail=str(info['key']))
            if stage!=last_log[1] or monotonic()-last_log[0]>=10:
                self.progress.emit(f'{worker} | {day} | {stage} | {done}/{total} bytes ({done/total:.1%})')
                last_log[:]=[monotonic(),stage]
        try:
            report('Copy G -> D')
            result=archive_staging.copy_file(info['source'],target,self.paths.archive,
                self.paths.cache/'_retained_reporting',info['bytes'],stop=self.stop,
                progress=lambda n,t:report('Copy G -> D',n,t),
                verify_progress=lambda n,t:report('Verify D copy',n,t),
                reserve_bytes=self.reserve_bytes,reserve_fraction=self.reserve_fraction)
            if result['status']=='cached':
                before=info['source'].stat()
                hashes=[]
                for path,label in ((info['source'],'Verify cached G source'),(target,'Verify cached D copy')):
                    value=sha256();n=0
                    with path.open('rb') as stream:
                        while chunk:=stream.read(8*1024**2):
                            if self.stop.is_set():raise InterruptedError('Copy verification stopped')
                            value.update(chunk);n+=len(chunk);report(label,n)
                    hashes.append(value.hexdigest())
                after=info['source'].stat()
                if hashes[0]!=hashes[1] or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise ValueError('Cached D file differs from G source')
                result=dict(status='staged',bytes=info['bytes'],sha256=hashes[0])
            if result['status']!='staged':raise ValueError('Archive staging failed: '+result['status'])
            report('Ready on SSD',info['bytes'])
            self.progress.emit(f'Copy {day} verified on D | {info["bytes"]} bytes')
            return result
        except BaseException as error:
            self.progress.worker(worker,'Interrupted' if self.stop.is_set() else 'Failed',day=day,detail=str(error))
            raise

    def get(self, day):
        while True:
            with self.lock:
                if self.error:raise self.error
                if day in self.pending:
                    future,size=self.pending[day];break
                if not any(d==day for d,_ in self.todo):raise ValueError('Unplanned staged source: '+str(day))
            if self.stop.wait(.1):raise InterruptedError('Stopped while waiting for prefetch admission')
        result=future.result()
        with self.lock:
            self.leased.add(day)
        return result

    def release(self,day):
        """Recovery has verified its receipt and deleted this owned staged file."""
        with self.lock:
            if day not in self.pending:return # D originals are not owned copies.
            if day not in self.leased:raise ValueError('Cannot release an unconsumed staged file')
            target=(self.paths.cache/'_retained_reporting'/self.info[day]['key']).resolve()
            if not target.is_relative_to((self.paths.cache/'_retained_reporting').resolve()):
                raise ValueError('Staged file escaped owned root')
            if target.exists():raise ValueError('Cannot release staging budget before file deletion')
            _,size=self.pending.pop(day);self.bytes-=size;self.leased.remove(day)
            self.progress.emit(f'Copy {day} | completed staging file removed | released {size} bytes')
            self.fill()

    def close(self):
        self.stop.set()
        if self.feeder.is_alive():self.feeder.join()
        self.pool.shutdown(wait=True,cancel_futures=True)

    def __exit__(self,*unused):
        self.close()


# Kept as an import for callers of the original public renderer.
from .repair_terminal import render
