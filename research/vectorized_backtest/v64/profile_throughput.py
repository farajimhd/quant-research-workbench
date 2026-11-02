"""Bounded four-session throughput matrix. Never launches or modifies a campaign."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from .publication import write_json
from .distributed_assets import sha
from .identity import digest,source_hash


class Observer:
    def __init__(self,root):
        from .publication_lane import Lane
        self.root=root;self.writer=Lane()
    def publish(self,**values):write_json(self.root/'progress.json',values)
    def session_progress(self,event):pass
    def session_result(self,day,result,role,generation):
        from .publication_lane import publish_replays
        self.writer.submit(publish_replays,result.pop('writes'),True)


def comparable(metrics):
    """Paths differ; ordered journal content and counts must be identical."""
    result=dict(metrics)
    result['execution_orders']=[{k:v for k,v in row.items() if k in ('sha256','orders')}
                                for row in result['execution_orders']]
    if any(not row for row in result['execution_orders']):raise ValueError('Missing order identity')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job',type=Path,required=True);p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,choices=(2,4,6,8),required=True)
    p.add_argument('--days',nargs='+',default=['2026-07-30','2026-07-31','2026-08-13','2026-08-18'])
    p.add_argument('--block',type=int,choices=(128,256,512,1024),required=True)
    p.add_argument('--mode',choices=('synchronous','events'),required=True)
    p.add_argument('--reference',type=Path)
    a=p.parse_args()
    from .runtime import require_runtime
    root=require_runtime(a.output)
    if any(root.iterdir()):raise ValueError('Use an empty qualification directory')
    job=json.loads(a.job.read_bytes());saved=json.loads(a.checkpoint.read_bytes())
    state=saved['state']
    if digest(state)!=saved['state_sha256']:raise ValueError('Checkpoint state hash mismatch')
    from .genome import Individual
    from .full_common import config
    from .session_lanes import SessionLanes
    members=[Individual.restore(v) for v in state['population'][:1024]]
    if len(members)!=1024:raise ValueError('Expected 1024 saved genomes')
    days=a.days
    if len(days)<a.workers or len(days)!=len(set(days)):raise ValueError('Need distinct sessions for every worker')
    if not set(days)<=set(job['training_days']):raise ValueError('Training scope changed')
    cfg=config(job,'workstation','training')
    cfg.update(output=str(root/'replays'),source_sha256=source_hash(),stop_roots=[],boundary_stop=str(root),
        compile_cache_root='D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v64/compiler/training',
        defer_publication=True,total_gpu_workers=a.workers,replay_width_workers=2,indexed_results=True,
        replay_time_block=a.block,replay_transfer_mode=a.mode,reuse_results=False,
        immutable_population_cache=True,maximum_replay_batch_size=1024,plan_limit=6,
        gpu_execution_gate=None)
    token=digest([v.payload() for v in members]);observer=Observer(root)
    started=time.perf_counter()
    try:
        with SessionLanes(cfg,a.workers,observer) as lanes:
            results=lanes.generation(days,members,1,token)
            observer.writer.close()
        elapsed=time.perf_counter()-started
    finally:
        observer.writer.close()
    rows=[]
    for day in days:
        receipt=root/'replays/generation-0001'/day/'receipt.json'
        value=json.loads(receipt.read_bytes());metrics=comparable(value['metrics'])
        if a.reference:
            old=json.loads((a.reference/'replays/generation-0001'/day/'receipt.json').read_bytes())
            reference=comparable(old['metrics'])
            different=[k for k in set(metrics)|set(reference) if metrics.get(k)!=reference.get(k)]
            if different:raise ValueError(f'Exact parity failed {day}: {different}')
        result=next(r for r in results if r['day']==day)
        rows.append(dict(day=day,worker_seconds=result['wall_seconds'],timings=result['timings'],
                         financial_and_order_digest=digest(metrics),receipt_sha256=sha(receipt)))
    report=dict(workers=a.workers,time_block=a.block,transfer_mode=a.mode,total_seconds=elapsed,
        sessions=rows,exact_parity=bool(a.reference),reference=str(a.reference),population_sha256=token,
        checkpoint_sha256=sha(a.checkpoint),source_sha256=source_hash(),campaign_modified=False)
    write_json(root/'report.json',report);print(json.dumps(report),flush=True)


if __name__=='__main__':main()
