"""Bounded observer I/O and input lanes; never delay keys on a file read."""
import json
import queue
import threading
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os


def worker_limits():
    os.environ.update(PYTHONDONTWRITEBYTECODE='1',POLARS_MAX_THREADS='2',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')


def invoke(module, name, args):
    from importlib import import_module
    return getattr(import_module(module), name)(*args)


class ObserverIO:
    def __init__(self):
        self.pool=ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn'),initializer=worker_limits)
        self.cache={};self.pending=set();self.checked={};self.lock=threading.Lock()
        self.calls={}
        self.queue=queue.Queue(maxsize=128);self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,name='observer-files',daemon=True);self.thread.start()

    def call(self,module,name,*args):
        # Large JSON parsing and aggregation hold the GIL. Isolate them from
        # rendering/input, returning only the compact requested result.
        return self.pool.submit(invoke,module,name,args).result()

    def request(self,key,module,name,*args):
        if key not in self.calls:
            if len(self.calls)>=32:
                old=next((k for k,v in self.calls.items() if v.done()),None)
                if old is None:return None
                self.calls.pop(old)
            self.calls[key]=self.pool.submit(invoke,module,name,args)
        future=self.calls[key]
        return future.result() if future.done() else None

    def read(self,path,default=None):
        key=str(path)
        with self.lock:
            if key not in self.pending and time.monotonic()-self.checked.get(key,0)>.5:
                try:self.queue.put_nowait(key);self.pending.add(key)
                except queue.Full:pass
            return self.cache.get(key,(None,default or {}))[1]

    def run(self):
        while not self.stop.is_set():
            try:key=self.queue.get(timeout=.1)
            except queue.Empty:continue
            try:
                path=Path(key);stat=path.stat();stamp=(stat.st_mtime_ns,stat.st_size)
                if self.cache.get(key,(None,))[0]!=stamp:
                    value=self.call(__name__,'read_json',key) if stat.st_size>2**20 else read_json(key)
                    with self.lock:self.cache[key]=(stamp,value)
            except (OSError,ValueError):pass
            finally:
                with self.lock:
                    self.pending.discard(key);self.checked[key]=time.monotonic()
                    if len(self.cache)>128:self.cache.pop(next(iter(self.cache)))

    def close(self):
        self.stop.set();self.thread.join()
        self.pool.shutdown(wait=True,cancel_futures=True)


def read_json(path):
    return json.loads(Path(path).read_bytes())


def load_complete(job,root,generation):
    from .progress_sessions import load_global
    return load_global(job,root,generation,threading.Event())


def initial_session(path,expected,day,index):
    from .progress_sessions import read_verified
    from .full_common import aggregate
    receipt=read_verified(path,expected)
    if receipt['day']!=day or receipt['split']!='training' or receipt['validation_opened']:
        raise ValueError('Initial session scope differs')
    metrics={k:[v[index]] for k,v in receipt['metrics'].items()}
    panel=aggregate([metrics])
    return dict(session='training:'+day,**{k:float(panel[k][0]) for k in ('score','net_pnl','gross_losses','drawdown')})


class InputLane:
    def __init__(self,ready,read):
        self.events=queue.Queue(maxsize=128);self.stop=threading.Event()
        def collect():
            while not self.stop.wait(.01):
                if ready():
                    key=read()
                    while not self.stop.is_set():
                        try:self.events.put(key,timeout=.05);break
                        except queue.Full:pass
        self.thread=threading.Thread(target=collect,name='terminal-input',daemon=True);self.thread.start()
    def close(self):
        self.stop.set();self.thread.join(timeout=1)
