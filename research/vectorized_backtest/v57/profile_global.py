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
    parser.add_argument('--offset',type=int,default=0);parser.add_argument('--count',type=int,default=6144)
    parser.add_argument('--neural-tile',type=int,default=64,choices=(32,64,128,256))
    parser.add_argument('--compare',type=Path);parser.add_argument('--reproduce',action='store_true')
    args=parser.parse_args();job=json.loads(args.job.read_bytes());check(job)
    args.output.mkdir(parents=True,exist_ok=True)
    seedpath=args.output/'founders.json'
    if not seedpath.exists():
        print('Initializing 6144 fresh founders from training-only calibration',flush=True)
        state=global_seed({},job,load_probe(job['initialization_probe'],job['training']))
        write_json(seedpath,state)
    state=json.loads(seedpath.read_bytes());members=[Individual.restore(p) for p in state['population']]
    if args.offset<0 or args.count<1 or args.offset+args.count>len(members):raise ValueError('Invalid profile subset')
    members=members[args.offset:args.offset+args.count]
    days=['2026-08-13','2026-08-18']
    if not set(days).issubset(job['training_days']):raise ValueError('Profile days outside training')
    label='batch-'+str(args.maximum_batch)+(f'-{args.offset}-{args.count}' if args.count!=6144 else '');root=args.output/label
    if root.exists():raise ValueError('Preserve the previous benchmark; use a fresh destination')
    local=dict(job,campaign=str(root),training_days=days,validation_days=[],replay_maximum_batch=args.maximum_batch,neural_tile=args.neural_tile)
    c=Global(local);c.publication_lane=Lane();c.cohort_snapshot=Snapshot(members);c.paired_probe=dict(mutants=[],generation=1)
    thread=__import__('threading').Thread(target=c.pump,daemon=True);thread.start()
    started=time.perf_counter()
    try:
        panels,refs=c.evaluate('training',days,members,1)
        returned=time.perf_counter();reproduction_seconds=None
        if args.reproduce:
            import numpy as np
            from .local_runner import CompactPedigree
            from .species import Species
            from .objectives import Objective
            from .publication_lane import Buffer
            from .reproduction_prefetch import FreshPrefetch
            from .allocation import budgets
            rng=np.random.default_rng();rng.bit_generator.state=state['rng'];probe=load_probe(job['initialization_probe'],job['training'])
            pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);ids=np.asarray(state['ids'][args.offset:args.offset+args.count])
            records=[pedigree.records[int(i)] for i in ids]
            c.fresh_prefetch=FreshPrefetch(rng,2,budgets(1024)['fresh'],probe)
            c.artifact_write=Buffer().write
            try:
                result=c.reproduce(c.root/'global',c.root/'global/generation-0001',members,ids,records,c.cohort_snapshot.token,panels,refs,days,Objective(**job['objective']),1,rng,pedigree,Species(),probe)
                reproduction_seconds=result[-1]
            finally:c.fresh_prefetch.close();c.fresh_prefetch=None
        c.publication_lane.close()
        rows=[]
        for day in days:
            timing=json.loads((c.root/'replays/training/generation-0001'/day/'timings.json').read_bytes())
            rows.append(dict(day=day,batches=len(timing),gpu_replay_seconds=sum(t['replay_seconds'] for t in timing),timings=timing))
        report=dict(population=len(members),population_sha256=c.cohort_snapshot.token,maximum_batch=args.maximum_batch,days=days,
                    two_session_return_seconds=returned-started,two_session_published_seconds=time.perf_counter()-started,sessions=rows,
                    metrics_sha256=digest(panels),initial_replay_target_seconds=45)
        report['reproduction_seconds']=reproduction_seconds
        if args.compare:
            expected=[]
            for day in days:
                original=json.loads((args.compare/'phase-2/replays/training/generation-0001'/day/'receipt.json').read_bytes())['metrics']
                expected.append({k:v[args.offset:args.offset+args.count] for k,v in original.items()})
            report['exact_financial_parity']=digest(expected)==digest(panels)
            if not report['exact_financial_parity']:
                report['differences']={day:[k for k in actual if actual[k]!=prior[k]] for day,actual,prior in zip(days,panels,expected)}
        write_json(root/'profile.json',report);print(json.dumps(report),flush=True)
        if report.get('exact_financial_parity') is False:raise ValueError('Financial parity failed')
    finally:
        c.halt.set();thread.join(5)
        try:c.close_lanes()
        finally:c.publication_lane.close()


if __name__=='__main__':main()
