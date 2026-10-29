"""Two persistent replay lanes per split with ordered asynchronous publication."""
import json
from pathlib import Path
from .campaign_runner import Evolution,stop_check
from .full_common import check_job,config
from .identity import digest
from .session_lanes import SessionLanes
from .publication_lane import publish_replays
from research.vectorized_backtest.v42.distributed_assets import ensure_day


class Global(Evolution):
    pipeline_enabled=True

    def __init__(self,job):
        super().__init__(job,'workstation',Path(job['campaign'])/'phase-2',job['global_session_workers'],[Path(job['campaign'])])
        self.split_lanes={};self.worker.deferred_writes=[];self.published_receipts=[]

    def close_lanes(self):
        errors=[]
        for lanes in self.split_lanes.values():
            try:lanes.__exit__(None,None,None)
            except BaseException as error:errors.append(error)
        self.split_lanes.clear()
        if errors:raise errors[0]

    def session_result(self,day,result,role,generation):
        writes=result.pop('writes')
        path=str(self.root/'replays'/role/f'generation-{generation:04d}'/day/'receipt.json')
        receipt=next(value for name,value in writes if name==path)
        n,token,combined_token=self.active_request
        if receipt['population_sha256']!=combined_token or receipt['split']!=role or receipt['validation_opened']!=(role=='validation') or not receipt['full_session'] or receipt['execution']!=self.job['execution'] or any(x is not None for x in receipt['case_owners']):raise ValueError('Global replay receipt authority changed')
        if role=='training' and self.paired_probe['mutants']:
            rawpath=str(self.root/'paired-replay'/f'generation-{generation:04d}'/day/'receipt.json')
            # The immutable combined receipt remains available for diagnostic auditing.
            writes=[(rawpath if name==path else name,value) for name,value in writes]
            metrics=receipt['metrics'];main={k:v[:n] for k,v in metrics.items()};extra={k:v[n:] for k,v in metrics.items()}
            receipt=dict(receipt,metrics=main,population_sha256=token,case_owners=receipt['case_owners'][:n],
                paired_replay=dict(receipt=rawpath,sha256=None,offset=0,count=n),
                reused_candidates=sum(main['result_reused']),evaluated_candidates=n-sum(main['result_reused']))
            extra_path=str(self.root/'mutation-probe-replay'/f'generation-{generation:04d}'/day/'receipt.json')
            extra_receipt=dict(receipt,metrics=extra,population_sha256=self.diagnostic_token,case_owners=[None]*len(self.paired_probe['mutants']),
                paired_replay=dict(receipt=rawpath,sha256=None,offset=n,count=len(self.paired_probe['mutants'])),
                reused_candidates=sum(extra['result_reused']),evaluated_candidates=len(self.paired_probe['mutants'])-sum(extra['result_reused']))
            writes.extend([(path,receipt),(extra_path,extra_receipt)])
            result['diagnostic']=extra;result['diagnostic_ref']=dict(receipt=extra_path,receipt_sha256=None)
            self.published_receipts.extend([rawpath,extra_path])
        result['metrics']=receipt['metrics'];result['ref']=dict(receipt=path,receipt_sha256=None)
        self.published_receipts.append(path)
        self.publication_lane.submit(publish_replays,writes)

    def evaluate(self,role,days,members,generation):
        stop_check(self.stop_roots)
        if days!=self.job[role+'_days'] or not days:raise ValueError('Global split changed')
        snapshot=self.cohort_snapshot;snapshot.verify(members)
        mutants=self.paired_probe['mutants'] if role=='training' else []
        population=members+mutants
        token=digest([m.payload() for m in population]) if mutants else snapshot.token
        self.active_request=(len(members),snapshot.token,token)
        self.diagnostic_token=digest([m.payload() for m in mutants])
        if role not in self.split_lanes:
            cfg=config(self.job,'workstation',role)
            cfg.update(output=str(self.root/'replays'/role),stop_roots=list(map(str,self.stop_roots)),boundary_stop=str(self.stop_roots[0]),
                compile_cache_root=str(Path(self.job['campaign'])/'compiler-cache'/role),defer_publication=True,total_gpu_workers=4)
            cfg['maximum_replay_batch_size']=self.job.get('replay_maximum_batch',1024)
            self.split_lanes[role]=SessionLanes(cfg,self.workers,self).__enter__()
        self.stage='Global training' if role=='training' else 'Validation replay + training-only reproduction'
        self.publish(phase=2,generation=generation,total_sessions=len(days),replay_role=role,evaluated_population=len(members))
        results=self.split_lanes[role].generation(days,population,generation,token)
        if mutants:
            self.paired_probe['metrics']=[r['diagnostic'] for r in results]
            self.paired_probe['receipts']={r['day']:r['diagnostic_ref'] for r in results}
        # Release graph allocations while the other split replays, retaining
        # processes, compiler caches and bounded CPU market residency.
        self.split_lanes[role].release_plans()
        return [r['metrics'] for r in results],{r['day']:r['ref'] for r in results}


def run(job):
    check_job(job)
    # Verify/copy prepared market assets once before GPU work, never mid-generation.
    for role in ('training','validation'):
        for day in job[role+'_days']:ensure_day(job,'workstation',role,day)
    Global(job).run({})
