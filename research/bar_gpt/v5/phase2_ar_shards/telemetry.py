"""One-way, bounded worker heartbeats. Observers never modify payloads or SQL."""

import json
import multiprocessing as mp
from pathlib import Path
import os
import threading
import time

import psutil

from ..shards.contract import write_json


class WorkerTelemetry:
    def __init__(self, root, run, role, slot, *, heartbeat=True):
        self.root = Path(root) / "worker-status" / run
        self.path = self.root / f"{role}-{slot:02d}.json"
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.tickers = set()
        self.unit = None
        self.seed = None
        self.data = dict(run=run, role=role, slot=slot, pid=os.getpid(),
                         created=psutil.Process().create_time(), started=time.time(),
                         thread=threading.get_native_id(), state="Ready", detail="Awaiting task",
                         assigned=0, completed=0, failed=0, progress_done=0,
                         progress_total=None, queries=0, query_seconds=0.,
                         query_started=None, cache_hits=0, bytes_done=0,
                         task_started=None, task_seconds=0., write_errors=0, closed=False)
        self.thread = None
        if heartbeat:
            try:
                self.thread = threading.Thread(target=self._loop, name=f"status-{role}-{slot}", daemon=True)
                self.thread.start()
            except RuntimeError:
                self.data["write_errors"] += 1

    def emit(self, **fields):
        with self.lock:
            self.data.update(fields)

    def begin(self, unit):
        with self.lock:
            self.unit = unit
            self.tickers.add(unit["ticker"])
            self.data.update(ticker=unit["ticker"], month=unit["month"], liquid=unit.get("liquid"),
                             state="Prepare", detail="Reading frozen inventory", assigned=self.data["assigned"]+1,
                             progress_done=0, progress_total=None, queries=0, cache_hits=0,
                             bytes_done=0, task_started=time.time(), task_seconds=0.,
                             query_started=None, query_seconds=0., error=None)

    def plan(self, tasks, tickers):
        """Fixed startup denominator; task-local counters remain separate."""
        self.emit(planned_tasks=tasks, planned_tickers=tickers, planned_done=0,
                  planned_deferred=0)

    def defer(self):
        with self.lock:
            self.data["planned_deferred"] = self.data.get("planned_deferred", 0) + 1

    def end_plan_task(self):
        with self.lock:
            self.data["planned_done"] = self.data.get("planned_done", 0) + 1

    def session(self):
        with self.lock:
            self.data["progress_done"] += 1
            self.data.update(state="Prepare", detail="Session prepared for publication")

    def query(self, entering):
        with self.lock:
            if entering:
                self.data.update(state="Query", query_started=time.time())
            else:
                started=self.data.get("query_started")
                self.data.update(state="Prepare", query_started=None,
                                 queries=self.data["queries"]+1,
                                 query_seconds=self.data["query_seconds"]+(time.time()-started if started else 0))

    def end(self, error=None):
        with self.lock:
            elapsed=time.time()-self.data["task_started"]
            self.data.update(task_seconds=elapsed, task_started=None, query_started=None,
                             state="Failed" if error else "Ready",
                             detail="Awaiting task" if not error else type(error).__name__,
                             error=str(error)[:240] if error else None)
            self.data["failed" if error else "completed"] += 1

    def snapshot(self):
        with self.lock:
            return dict(self.data, updated=time.time(), tickers=len(self.tickers))

    def write(self):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            write_json(self.path,self.snapshot())
        except (OSError,ValueError):
            with self.lock:self.data["write_errors"] += 1

    def _loop(self):
        while not self.stop.is_set():
            self.write()
            self.stop.wait(1.)

    def close(self):
        if self.data.get("closed"):return
        self.stop.set()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(2.)
        self.emit(closed=True)
        self.write()


class ObservedReader:
    """Delegate exact arguments/results; observe query gates and task geometry."""
    def __init__(self, reader, reporter, seed):
        self.reader,self.reporter,self.seed=reader,reporter,seed

    def __getattr__(self, name):
        if name in ("origin_counts", "history_fetch"):
            method=getattr(self.reader,name)  # Missing remains missing for a reader without preflight.
            return lambda *a,**kw:self._read(method,*a,**kw)
        return getattr(self.reader,name)

    def months(self, job):
        result=self.reader.months(job)
        try:
            if not isinstance(result,(list,tuple)):return result
            from .sampling import origin_clock
            unit=self.reporter.unit
            chosen=origin_clock(unit,self.seed)
            total=sum(r["source_date"].startswith(unit["month"]) and int(r["last_sip_timestamp_us"])>=chosen for _,rows in result for r in rows)
            self.reporter.emit(progress_total=total)
        except (KeyError,TypeError,AttributeError):
            pass  # Optional telemetry has no authority over the producer.
        return result

    def _read(self, method, *args, **kwargs):
        rows=args[1] if len(args)>1 else kwargs.get('rows',[])
        first=rows[0]["source_date"] if rows else ""
        unit=self.reporter.unit or {}
        self.reporter.emit(state="Fetch",detail=("Warm history " if first<unit.get('month','') else "Build bars ")+first)
        before=self.reporter.snapshot()["queries"]
        result=method(*args,**kwargs)
        timing=result[1]
        with self.reporter.lock:
            actual=self.reporter.data["queries"]-before
            self.reporter.data["queries"]+=max(0,int(timing.get('queries',0))-actual)
            self.reporter.data["cache_hits"]+=int(timing.get('coarse_cache_hits',0))
            self.reporter.data["cache_hits"]+=int(timing.get('context_memory_hits',0))+int(timing.get('context_disk_hits',0))
            self.reporter.data["state"]="Prepare"
        return result

    def fetch(self, *args, **kwargs):return self._read(self.reader.fetch,*args,**kwargs)


class PublicationTelemetry:
    def __init__(self, root, run):
        self.root,self.run=root,run
        self.lock=threading.Lock()
        self.reporters={}

    def worker(self, slot=None):
        if slot is None:
            slot=int(threading.current_thread().name.rsplit('_',1)[-1])
        with self.lock:
            if slot not in self.reporters:self.reporters[slot]=WorkerTelemetry(self.root,self.run,"io",slot)
            return self.reporters[slot]

    def close(self):
        for reporter in self.reporters.values():reporter.stop.set()
        for reporter in self.reporters.values():reporter.close()


def cpu_telemetry(root, settings):
    identity=mp.current_process()._identity
    slot=settings.get("worker_slot", ((identity[-1]-1) % settings["workers"]) if identity else 0)
    reporter=WorkerTelemetry(root,settings.get("telemetry_run","test"),"cpu",slot)
    if "planned_tasks" in settings:
        reporter.plan(settings["planned_tasks"], settings["planned_tickers"])
    from multiprocessing.util import Finalize
    Finalize(reporter,reporter.close,exitpriority=10)
    return reporter
