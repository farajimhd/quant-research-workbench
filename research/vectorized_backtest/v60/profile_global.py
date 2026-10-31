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
from research.vectorized_backtest.v60.publication import write_json


def financial_evidence(metrics):
    """Ignore only destination paths; retain order contents hashes and counts."""
    return {k:([{field:value for field,value in row.items() if field!='receipt'}
                 for row in values] if k=='execution_orders' else values)
            for k,values in metrics.items()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--maximum-batch',type=int,default=1024,choices=(1024,2048,4096,6144))
    parser.add_argument('--offset',type=int,default=0);parser.add_argument('--count',type=int,default=6144)
    parser.add_argument('--neural-tile',type=int,default=64,choices=(32,64,128,256))
    parser.add_argument('--compare',type=Path);parser.add_argument('--reproduce',action='store_true')
    parser.add_argument('--founders',type=Path);parser.add_argument('--founders-sha256')
    parser.add_argument('--mixed',action='store_true',help='Equal template/random sample; no outcome-based selection')
    parser.add_argument('--diagnostics',action='store_true',help='Bounded GPU trace and CPU reproduction profile; not a timing acceptance run')
    parser.add_argument('--ungated',action='store_true',help='Diagnostic comparison of independent CUDA contexts without host mutex')
    args=parser.parse_args();job=json.loads(args.job.read_bytes());check(job)
    args.output.mkdir(parents=True,exist_ok=True)
    seedpath=args.output/'founders.json'
    if args.founders:
        from .distributed_assets import sha
        if not args.founders_sha256 or sha(args.founders)!=args.founders_sha256:
            raise ValueError('Pinned V60 diagnostic genomes required')
        seedpath=args.founders
    if not seedpath.exists():
        print('Initializing 6144 fresh founders from training-only calibration',flush=True)
        state=global_seed({},job,load_probe(job['initialization_probe'],job['training']))
        write_json(seedpath,state)
    state=json.loads(seedpath.read_bytes());members=[Individual.restore(p) for p in state['population']]
    if len(members)!=6144:raise ValueError('Complete V60 diagnostic founder cohort required')
    if args.offset<0 or args.count<1 or args.offset+args.count>len(members):raise ValueError('Invalid profile subset')
    if args.mixed:
        if args.offset or args.count%2 or args.count>6144:raise ValueError('Even mixed cohort and zero offset required')
        indices=list(range(args.count//2))+list(range(3072,3072+args.count//2))
        members=[members[i] for i in indices]
    else:indices=list(range(args.offset,args.offset+args.count));members=members[args.offset:args.offset+args.count]
    days=['2026-08-13','2026-08-18']
    if not set(days).issubset(job['training_days']):raise ValueError('Profile days outside training')
    label='batch-'+str(args.maximum_batch)+(f'-{args.offset}-{args.count}' if args.count!=6144 else '')+('-mixed' if args.mixed else '');root=args.output/label
    if root.exists():raise ValueError('Preserve the previous benchmark; use a fresh destination')
    local=dict(job,campaign=str(root),training_days=days,validation_days=[],replay_maximum_batch=args.maximum_batch,neural_tile=args.neural_tile,execution_diagnostics=args.diagnostics,profile_ungated=args.ungated)
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
            pedigree=CompactPedigree(3,state['pedigree'],state['next_serial']);ids=np.asarray(state['ids'])[indices]
            records=[pedigree.records[int(i)] for i in ids]
            c.fresh_prefetch=FreshPrefetch(rng,2,budgets(1024)['fresh'],probe)
            c.artifact_write=Buffer().write
            import cProfile
            profiler=cProfile.Profile() if args.diagnostics else None
            try:
                if profiler:profiler.enable()
                result=c.reproduce(c.root/'global',c.root/'global/generation-0001',members,ids,records,c.cohort_snapshot.token,panels,refs,days,Objective(**job['objective']),1,rng,pedigree,Species(),probe)
                reproduction_seconds=result[-1]
            finally:
                if profiler:
                    profiler.disable();profiler.dump_stats(str(root/'reproduction.prof'))
                c.fresh_prefetch.close();c.fresh_prefetch=None
        publication_wait_started=time.perf_counter()
        c.publication_lane.close()
        publication_wait_seconds=time.perf_counter()-publication_wait_started
        rows=[]
        for day in days:
            timing=json.loads((c.root/'replays/training/generation-0001'/day/'timings.json').read_bytes())
            rows.append(dict(day=day,batches=len(timing),gpu_replay_seconds=sum(t['replay_seconds'] for t in timing),timings=timing))
        # Compare complete published financial evidence, not the compact subset
        # retained by the controller solely for selection and behavior.
        published=[json.loads((c.root/'replays/training/generation-0001'/day/'receipt.json').read_bytes())['metrics'] for day in days]
        report=dict(population=len(members),population_sha256=c.cohort_snapshot.token,maximum_batch=args.maximum_batch,days=days,
                    two_session_return_seconds=returned-started,two_session_published_seconds=time.perf_counter()-started,sessions=rows,
                    metrics_sha256=digest([financial_evidence(p) for p in published]),initial_replay_target_seconds=45)
        report['reproduction_seconds']=reproduction_seconds
        report['publication_final_wait_seconds']=publication_wait_seconds
        report['instrumented']=args.diagnostics
        import numpy as np
        report['quality']=[]
        for day,metrics in zip(days,published):
            net=np.asarray(metrics['net_pnl']);trades=np.asarray(metrics['closed_positions']);violations=0
            for listings,entries,exits in zip(metrics['episode_listings'],metrics['episode_entry_clocks'],metrics['episode_exit_clocks']):
                listings=np.asarray(listings);entries=np.asarray(entries);exits=np.asarray(exits);order=np.lexsort((entries,listings))
                violations+=int(((listings[order][1:]==listings[order][:-1])&(entries[order][1:]-exits[order][:-1]<3)).sum())
            report['quality'].append(dict(day=day,positive=int((net>0).sum()),best=float(net.max()),median=float(np.median(net)),
                total_trades=int(trades.sum()),inactive=int((trades==0).sum()),terminal_invalid=int((~np.asarray(metrics['terminal_valid'],bool)).sum()),
                reentry_violations=violations,ledger_error=max(abs(sum(p)-n) for p,n in zip(metrics['episode_profits'],net))))
        if args.compare:
            expected=[]
            for day in days:
                original=json.loads((args.compare/'phase-2/replays/training/generation-0001'/day/'receipt.json').read_bytes())['metrics']
                expected.append({k:[v[i] for i in indices] for k,v in original.items()})
            expected=[financial_evidence(p) for p in expected]
            actual_evidence=[financial_evidence(p) for p in published]
            report['exact_financial_parity']=digest(expected)==digest(actual_evidence)
            if not report['exact_financial_parity']:
                report['differences']={day:[k for k in actual if actual[k]!=prior[k]] for day,actual,prior in zip(days,actual_evidence,expected)}
        write_json(root/'profile.json',report);print(json.dumps(report),flush=True)
        if report.get('exact_financial_parity') is False:raise ValueError('Financial parity failed')
    finally:
        c.halt.set();thread.join(5)
        try:c.close_lanes()
        finally:c.publication_lane.close()


if __name__=='__main__':main()
