"""Bounded two-session, complete-founder-cohort throughput and parity check."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from .global_runner import Global
from .global_contract import check
from .full_seed import global_seed
from .calibration import load_probe
from .genome import Individual
from .population_snapshot import Snapshot
from .publication_lane import Lane
from .identity import digest
from research.vectorized_backtest.v42.publication import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--maximum-batch',type=int,default=1024,choices=(1024,2048,4096,6144))
    args=parser.parse_args();job=json.loads(args.job.read_bytes());check(job)
    args.output.mkdir(parents=True,exist_ok=True)
    seedpath=args.output/'founders.json'
    if not seedpath.exists():
        print('Initializing 6144 fresh founders from training-only calibration',flush=True)
        state=global_seed({},job,load_probe(job['initialization_probe'],job['training']))
        write_json(seedpath,state)
    state=json.loads(seedpath.read_bytes());members=[Individual.restore(p) for p in state['population']]
    days=['2026-08-13','2026-08-18']
    if not set(days).issubset(job['training_days']):raise ValueError('Profile days outside training')
    label='batch-'+str(args.maximum_batch);root=args.output/label
    if root.exists():raise ValueError('Preserve the previous benchmark; use a fresh destination')
    local=dict(job,campaign=str(root),training_days=days,replay_maximum_batch=args.maximum_batch)
    c=Global(local);c.publication_lane=Lane();c.cohort_snapshot=Snapshot(members);c.paired_probe=dict(mutants=[],generation=1)
    thread=__import__('threading').Thread(target=c.pump,daemon=True);thread.start()
    started=time.perf_counter()
    try:
        panels,refs=c.evaluate('training',days,members,1)
        returned=time.perf_counter();c.publication_lane.close()
        rows=[]
        for day in days:
            timing=json.loads((c.root/'replays/training/generation-0001'/day/'timings.json').read_bytes())
            rows.append(dict(day=day,batches=len(timing),gpu_replay_seconds=sum(t['replay_seconds'] for t in timing),timings=timing))
        report=dict(population=len(members),population_sha256=c.cohort_snapshot.token,maximum_batch=args.maximum_batch,days=days,
                    two_session_return_seconds=returned-started,two_session_published_seconds=time.perf_counter()-started,sessions=rows,
                    metrics_sha256=digest(panels),initial_replay_target_seconds=45)
        write_json(root/'profile.json',report);print(json.dumps(report),flush=True)
    finally:
        c.halt.set();thread.join(5);c.close_lanes()


if __name__=='__main__':main()
