"""Persistent CPU session ownership; only packed evaluation requests cross IPC."""
import multiprocessing as mp
import os
import queue
import threading
import traceback
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace


class PackedPopulation:
    def __init__(self, members, keys, missing, batches, seconds):
        self.members=members;self.keys=keys;self.missing=missing
        self.batches=batches;self.seconds=seconds


def prepare_population(members, cached_keys, batch):
    from .lineage import program_key
    from .network_pack import pack_networks
    import psutil
    if psutil.virtual_memory().available<160*2**30:
        raise MemoryError('Persistent preparation requires unchanged 160 GiB admission')
    started=perf_counter();keys=[program_key(m) for m in members]
    missing=[i for i,k in enumerate(keys) if k not in cached_keys]
    # Hard resource bound on the transfer representation, never a search cap.
    from .config import FAN_IN
    nodes=sum(len(n.nodes) for i in missing for n in members[i].networks.values())
    if nodes*(FAN_IN*8+32)>4*2**30:
        raise MemoryError('Packed population exceeds 4 GiB transfer admission')
    batches={}
    for offset in range(0,len(missing),batch):
        t=perf_counter()
        packed=pack_networks([members[i] for i in missing[offset:offset+batch]])
        batches[offset]=(packed,perf_counter()-t)
    slim=[SimpleNamespace(policy=m.policy,minimum_age=dict(m.minimum_age)) for m in members]
    return PackedPopulation(slim,keys,missing,batches,perf_counter()-started)


class RemoteProxy:
    def __init__(self, connection, progress, config, events, lane):
        self.connection=connection;self.events=events;self.progress=progress
        self.config=config;self.cached_keys=None;self.finished=threading.Event()
        self.relay=threading.Thread(target=self._progress,daemon=True);self.relay.start()
        self.call('create',config,lane)

    def _progress(self):
        while not self.finished.is_set():
            try:event=self.progress.get(timeout=.1)
            except queue.Empty:continue
            try:self.events.put_nowait(event)
            except queue.Full:pass

    def call(self, method, *args):
        self.connection.send((method,args))
        ok,value=self.connection.recv()
        if not ok:raise RuntimeError(value)
        return value

    def load(self,day):return self.call('load',day)

    def run_members(self,day,next_day,members,generation,token,width):
        if self.cached_keys is None:
            import json
            p=Path(self.config['output'])/'result-cache'/(day+'.json')
            self.cached_keys=set(json.loads(p.read_bytes())['results']) if p.exists() and self.config.get('reuse_results',True) else set()
        packet=prepare_population(members,self.cached_keys,self.config['batch_size'])
        result=self.call('run',day,next_day,packet,generation,token,width)
        self.cached_keys=set(packet.keys)
        return result

    def close(self):
        try:return self.call('close')
        finally:self.finished.set();self.relay.join()


def _run(connection,progress,args,kwargs):
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['V35_REPRODUCTION_WORKERS']='1'
    try:
        from .local_runner import run_session
        def factory(config,events,lane):return RemoteProxy(connection,progress,config,events,lane)
        config=dict(args[0],persistent_session_owner=True)
        result=run_session(config,*args[1:],worker_factory=factory,**kwargs)
        connection.send(('finished',(result,)))
    except BaseException:
        connection.send(('failed',(traceback.format_exc(),)))
    finally:connection.close()


def run_owned_session(service,*args,**kwargs):
    """Exactly one owner process per admitted session; joined before return."""
    context=mp.get_context('spawn');parent,child=context.Pipe()
    progress=context.Queue(maxsize=1)
    process=context.Process(target=_run,args=(child,progress,args,kwargs),name='v35-session-owner')
    process.start();child.close();proxy=None
    try:
        while True:
            if not parent.poll(.2):
                if not process.is_alive():raise RuntimeError(f'Session owner exited {process.exitcode}')
                continue
            method,values=parent.recv()
            if method=='finished':return values[0]
            if method=='failed':raise RuntimeError(values[0])
            try:
                if method=='create':
                    config,lane=values;proxy=service.factory(config,progress,lane);result=None
                else:
                    result=getattr(proxy,method)(*values)
                    if method=='close':proxy=None
                parent.send((True,result))
            except BaseException:
                parent.send((False,traceback.format_exc()))
    finally:
        if proxy is not None:proxy.close()
        parent.close();process.join(timeout=10)
        if process.is_alive():process.terminate();process.join()
        progress.close();progress.join_thread()
