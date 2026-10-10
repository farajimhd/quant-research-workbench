"""Bounded all30 feature preparation; reuses completed session/tile receipts."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from time import perf_counter
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,write_json,file_hash
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from research.vectorized_backtest.v6.torch_backtest.run_structure import training_days
from .data import SessionData
from .feature_cache import prepare,VERSION

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','history','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--workers',type=int,default=4);p.add_argument('--maximum-gib',type=float,default=1200.)
    a=p.parse_args()
    if not 1<=a.workers<=8 or a.maximum_gib<=0:raise ValueError('Explicit bounded preparation envelope required')
    root=require_runtime(a.output);days=training_days(a.inputs);started=perf_counter();lock=threading.Lock()
    records={};failures={};active={};stop=threading.Event()
    def status():
        write_json(root/'status.json',dict(completed=len(records),failed=failures,active=active,
            queued=30-len(records)-len(failures)-len(active),total=30,wall_seconds=perf_counter()-started,validation_opened=False))
    def worker(day):
        if stop.is_set() or (root/'STOP').exists():raise InterruptedError('Preparation stopped before loading session')
        with lock:active[day]=dict(stage='verify existing inputs and histories');status()
        data=SessionData(a.inputs/day,a.history/day)
        try:
            def progress(value):
                if stop.is_set() or (root/'STOP').exists():raise InterruptedError('Preparation stopped after durable tile')
                with lock:active[day]=value;status()
            record=prepare(data,root/day,maximum_gib=a.maximum_gib/30,progress=progress)
            return dict(stored_bytes=record['stored_bytes'],dense_bytes=record['dense_bytes'],preparation_seconds=record['wall_seconds'],
                receipt_sha256=file_hash(root/day/'complete.json'))
        finally:data.close()
    with owned_run(root,version=VERSION),ThreadPoolExecutor(max_workers=a.workers) as pool:
        pending={pool.submit(worker,day):day for day in days}
        for future in as_completed(pending):
            day=pending[future]
            try:records[day]=future.result();print(json.dumps(dict(day=day,**records[day])),flush=True)
            except Exception as exc:failures[day]=str(exc);stop.set()
            with lock:active.pop(day,None);status()
        result=dict(status='failed' if failures else 'complete',sessions=records,failures=failures,workers=a.workers,
            wall_seconds=perf_counter()-started,stored_bytes=sum(v['stored_bytes'] for v in records.values()),validation_opened=False)
        write_json(root/('failed.json' if failures else 'complete.json'),result)
        if failures:raise RuntimeError('Feature preparation failed; drained worker pool; inspect failed.json')

if __name__=='__main__':main()
