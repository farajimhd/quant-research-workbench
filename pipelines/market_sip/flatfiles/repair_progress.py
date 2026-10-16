"""Durable worker snapshots and bounded concurrent SSD staging for reporting repair."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock, current_thread
from time import monotonic

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

    def worker(self, worker, stage, *, day='', done=None, total=None, detail=''):
        with self.lock:
            old = self.state['workers'].get(worker, {})
            now = monotonic()
            same = old.get('stage') == stage and old.get('day') == str(day)
            started = old.get('started', now) if same else now
            elapsed = now-started
            self.state['workers'][worker] = dict(stage=stage,day=str(day),done=done,total=total,
                detail=detail,started=started,elapsed_seconds=elapsed,
                rate_bytes_second=(done/elapsed if done is not None and elapsed > 0 else None),
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
        temporary.replace(self.root/'status.json')
        self.last_write = monotonic()


class Prefetch:
    """At most workers*2 files and max_bytes outstanding, plus the consumed SQL file.

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
        self.bytes = 0

    def __enter__(self):
        try:
            self.fill()
            return self
        except BaseException:
            self.close()
            raise

    def fill(self):
        while self.todo and len(self.pending) < self.workers*2 and not self.stop.is_set():
            day,info = self.todo[0]
            if self.bytes+info['bytes'] > self.max_bytes:
                break
            # Reserve the complete outstanding set before admitting more copies.
            space=archive_staging.space_budget(self.paths.cache,reserve_bytes=self.reserve_bytes,
                                               reserve_fraction=self.reserve_fraction)
            if self.bytes+info['bytes'] > space['usable_bytes']:
                raise ValueError('Concurrent copy reservations exceed SSD headroom')
            self.todo.pop(0)
            self.bytes += info['bytes']
            self.pending[day]=(self.pool.submit(self.copy,day,info),info['bytes'])

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
        if day not in self.pending:raise ValueError('Unplanned staged source: '+str(day))
        future,size=self.pending[day]
        result=future.result()
        del self.pending[day];self.bytes-=size
        self.fill()
        return result

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True,cancel_futures=True)

    def __exit__(self,*unused):
        self.close()


def render(state, width=110, height=30):
    """One bounded viewport. Snapshot timestamps expose stale telemetry."""
    from rich.table import Table
    from rich.console import Group
    from rich.text import Text
    title=f'Trade Reporting Repair | {state.get("status")} | {state.get("start", "")} -> {state.get("end", "")}'
    summary=f'Days {state.get("total",0)} | '+'  '.join(f'{name} {state.get(name,0)}' for name in ('completed','reused','queued','failed','retried'))
    updated=state.get('updated_at','')
    try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(updated)).total_seconds()
    except ValueError:age=0
    table=Table(expand=True,box=None)
    table.add_column('Worker',width=7,min_width=7,no_wrap=True)
    table.add_column('Day',width=10,min_width=10,no_wrap=True)
    table.add_column('Stage',width=18 if width>=75 else 12,no_wrap=True,overflow='ellipsis')
    table.add_column('Progress',ratio=1,overflow='ellipsis',no_wrap=True)
    if width>=110:table.add_column('Elapsed / rate / ETA',width=28,overflow='ellipsis',no_wrap=True)
    for worker,row in sorted(state.get('workers',{}).items())[:max(1,height-8)]:
        done,total=row.get('done'),row.get('total')
        amount=(f'{done/1024**3:.2f}/{total/1024**3:.2f} GiB ({done/total:.0%})' if total else row.get('detail',''))
        cells=[worker,row.get('day',''),row.get('stage',''),amount]
        if width>=110:
            rate=row.get('rate_bytes_second')
            eta=(f' ETA {(total-done)/rate:.0f}s' if rate and total and done<total else '')
            cells.append(f'{row.get("elapsed_seconds",0):.0f}s'+(f' / {rate/1024**2:.1f} MiB/s{eta}' if rate else ''))
        table.add_row(*cells)
    return Group(Text(title,style='bold'),Text(summary),table,
                 Text(state.get('message',''),overflow='ellipsis',no_wrap=True),
                 Text(f'UTC snapshot {updated} | age {age:.0f}s'+(' | STALE' if age>15 else '')),
                 Text('Ctrl+C: leave monitor. Stop worker: create stop.request in the run directory.'))
