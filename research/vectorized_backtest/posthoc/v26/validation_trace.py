"""Isolated trace replay using the completed optimizer's immutable code."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json,sys,shutil,hashlib,time
from pathlib import Path


def exact_parity(actual,expected):
    for key in ('identity','execution','population_sha256','listing_ids','full_session','split','validation_opened'):
        if actual[key]!=expected[key]:raise ValueError('Validation replay identity changed: '+key)
    for key,value in expected['metrics'].items():
        if key!='result_reused' and actual['metrics'][key]!=value:raise ValueError('Exact validation parity failed: '+key)
    if not all(k in actual['metrics'] for k in ('episode_entry_clocks','episode_exit_clocks')):raise ValueError('Trace clocks missing')


def main():
    job=json.loads(Path(sys.argv[1]).read_bytes())
    for k,v in json.loads(Path(job['environment']).read_bytes()).items():os.environ[k]=v
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','POLARS_MAX_THREADS'):os.environ[k]='1'
    sys.path.insert(0,job['owner']['code']);os.chdir(job['owner']['code'])
    import torch,psutil
    from research.vectorized_backtest.v26 import session_lanes as lanes
    from research.vectorized_backtest.v26.run_search import source_hash,digest
    from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash,write_json
    assert source_hash()==job['owner']['source_sha256']
    pointer=Path(job['pointer']);out=Path(job['output'])
    def idle():
        p=json.loads(pointer.read_bytes())
        try:active=abs(psutil.Process(p['pid']).create_time()-p['creation_time'])<.01
        except psutil.NoSuchProcess:active=False
        root=Path(p['campaign'])
        if active or (root/'owner.lock').exists() or (root/'global/owner.lock').exists():raise InterruptedError('Optimizer became active; trace replay yields')
        if psutil.virtual_memory().available<128*1024**3:raise InterruptedError('Host RAM reserve reached; trace replay yields')
    class Events:
        checked=0.
        def put_nowait(self,event):
            if time.monotonic()-self.checked>5:
                idle();self.checked=time.monotonic();write_json(out/'progress.json',event)
    original=lanes.replay_cohort
    def trace(*args,**kwargs):
        kwargs['trace']=True
        return original(*args,**kwargs)
    lanes.replay_cohort=trace
    c=job['contract'];dataset=c['validation'];idle()
    if psutil.virtual_memory().available<160*1024**3:raise InterruptedError('Insufficient host RAM headroom')
    cfg=dict(dataset,output=str(out),device=c['device'],backend=c['backend'],batch_size=c['batch_size'],
        maximum_data_gib=c['maximum_data_gib'],maximum_gate_gib=c['maximum_gate_gib'],
        maximum_host_sessions=2,host_reserve_gib=128.,host_admission_gib=32.,capture_gib=c['capture_gib'],
        plan_limit=c['plan_limit'],episode_capacity=c['episode_capacity'],execution=c['execution'],
        source_sha256=c['source_sha256'],split='validation',reuse_results=True,
        lane_memory_bytes=int(torch.cuda.mem_get_info()[0]*.8))
    worker=lanes.Worker(cfg,Events(),0)
    seed=json.loads(Path(job['driver_source']).read_bytes());src=Path(job['driver_cache'])/seed['key'];dst=Path(os.environ['TRITON_CACHE_DIR'])/seed['key'];dst.mkdir(parents=True,exist_ok=True)
    for name,h in seed['files'].items():
        assert file_hash(src/name)==h
        if not (dst/name).exists():shutil.copy2(src/name,dst/name)
        assert file_hash(dst/name)==h
    payload=job['payload'];token=job['token'];assert digest(payload)==token
    try:
        for day in job['trace_days']:
            idle()
            target=out/'generation-0050'/day/'exact-parity.json'
            if target.exists():
                proof=json.loads(target.read_bytes())
                assert proof['receipt_sha256']==file_hash(target.parent/'receipt.json')
                assert proof['saved_sha256']==job['receipt_hashes'][day]
                continue
            worker.run(day,None,payload,50,token,job['widths'][day])
            receipt=target.parent/'receipt.json';actual=json.loads(receipt.read_bytes())
            saved=Path(job['saved_folder'])/day/'receipt.json';assert file_hash(saved)==job['receipt_hashes'][day]
            expected=json.loads(saved.read_bytes())
            exact_parity(actual,expected)
            write_json(target,dict(receipt_sha256=file_hash(receipt),saved_sha256=file_hash(saved),exact_saved_metrics=True))
    finally:worker.close()


if __name__=='__main__':
    try:main()
    except InterruptedError:raise SystemExit(75)
