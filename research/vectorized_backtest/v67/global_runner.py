"""Two persistent replay lanes per split with ordered asynchronous publication."""
import json
from pathlib import Path
from .campaign_runner import Evolution,stop_check
from .full_common import check_job,config
from .identity import digest
from .session_lanes import SessionLanes
from .publication_lane import publish_replays
from research.vectorized_backtest.v67.distributed_assets import ensure_day


class Global(Evolution):
    pipeline_enabled=True

    def __init__(self,job):
        super().__init__(job,'workstation',Path(job['campaign'])/'phase-2',job['global_session_workers'],[Path(job['campaign'])])
        self.split_lanes={};self.worker.deferred_writes=[];self.published_receipts=[]
        import multiprocessing
        context=multiprocessing.get_context('spawn')
        self.host_load_slots=context.BoundedSemaphore(32)
        self.host_load_counts=context.Array('q',[0,0,0,0],lock=True)

    def close_lanes(self):
        errors=[]
        for lanes in self.split_lanes.values():
            try:lanes.__exit__(None,None,None)
            except BaseException as error:errors.append(error)
        self.split_lanes.clear()
        if errors:raise errors[0]

    def prepare_resident_sessions(self):
        """Admit every train/validation input before evolution; no labels in inference."""
        from .session_lanes import prime_sessions
        from .publication import write_json
        import psutil,time
        self.stage='Prepare all-session RAM residency';self.publish()
        started=time.perf_counter();pending=[]
        for role in ('training','validation'):
            lanes=self.get_lanes(role)
            for lane,pool in enumerate(lanes.pools):
                pending.append(pool.submit(prime_sessions,self.job[role+'_days'][lane::self.workers]))
        records=[future.result() for future in pending]
        report=dict(sessions=sum(r['ready'] for r in records),deferred=[d for r in records for d in r['deferred']],
                    workers=records,available_gib=psutil.virtual_memory().available/2**30,
                    seconds=time.perf_counter()-started,labels_cpu_only=True)
        write_json(self.root/'all-session-residency.json',report)
        expected=sum(len(self.job[r+'_days']) for r in ('training','validation'))
        if report['sessions']!=expected or report['deferred']:
            raise MemoryError('All-session RAM residency is required before evolution')
        if report['available_gib']<self.job['machines']['workstation']['reserve_gib']:
            raise MemoryError('All-session residency breached the RAM reserve')
        self.publish(resident_sessions=report['sessions'],resident_available_gib=report['available_gib'])

    def session_result(self,day,result,role,generation):
        if result.get('restored'):
            # Explicitly bound receipts remain immutable; publication only links
            # their hashes into the new generation seal, without rewriting them.
            self.published_receipts.append(result['ref']['receipt'])
            self.prepare_session_summary(result['metrics'],generation)
            return
        writes=result.pop('writes')
        path=str(self.root/'replays'/role/f'generation-{generation:04d}'/day/'receipt.json')
        receipt=next(value for name,value in writes if name==path)
        n,token,combined_token=self.active_request
        if receipt['population_sha256']!=combined_token or receipt['split']!=role or receipt['validation_opened']!=(role=='validation') or not receipt['full_session'] or receipt['execution']!=self.job['execution'] or any(x is not None for x in receipt['case_owners']):raise ValueError('Global replay receipt authority changed')
        if role=='training' and self.paired_probe['mutants']:
            rawpath=str(self.root/'paired-replay'/f'generation-{generation:04d}'/day/'receipt.json')
            # The immutable combined receipt remains available for diagnostic auditing.
            writes=[(rawpath if name==path else name,value) for name,value in writes]
            metrics=receipt['metrics'];main={k:v[:n] for k,v in metrics.items()};extra={k:v[n:] for k,v in metrics.items()};combined_keys=receipt.get('program_keys')
            receipt=dict(receipt,metrics=main,population_sha256=token,case_owners=receipt['case_owners'][:n],
                paired_replay=dict(receipt=rawpath,sha256=None,offset=0,count=n),
                reused_candidates=sum(main['result_reused']),evaluated_candidates=n-sum(main['result_reused']))
            if 'program_keys' in receipt:receipt['program_keys']=receipt['program_keys'][:n]
            extra_path=str(self.root/'mutation-probe-replay'/f'generation-{generation:04d}'/day/'receipt.json')
            extra_receipt=dict(receipt,metrics=extra,population_sha256=self.diagnostic_token,case_owners=[None]*len(self.paired_probe['mutants']),
                paired_replay=dict(receipt=rawpath,sha256=None,offset=n,count=len(self.paired_probe['mutants'])),
                reused_candidates=sum(extra['result_reused']),evaluated_candidates=len(self.paired_probe['mutants'])-sum(extra['result_reused']))
            if combined_keys is not None:extra_receipt['program_keys']=combined_keys[n:]
            writes.extend([(path,receipt),(extra_path,extra_receipt)])
            from .controller_metrics import compact
            result['diagnostic']=compact(extra);result['diagnostic_ref']=dict(receipt=extra_path,receipt_sha256=None)
            self.published_receipts.extend([rawpath,extra_path])
        from .controller_metrics import compact
        result['metrics']=compact(receipt['metrics']);result['ref']=dict(receipt=path,receipt_sha256=None)
        self.prepare_session_summary(result['metrics'],generation)
        self.published_receipts.append(path)
        self.publication_lane.submit(publish_replays,writes,True)

    def prepare_session_summary(self,metrics,generation):
        from .full_common import prepare_summary
        from .objectives import Objective
        prepare_summary(metrics,Objective(**self.job['objective']))
        if generation==0 and '_founder_descriptor' not in metrics:
            from .founder_selection import session_descriptor
            metrics['_founder_descriptor']=session_descriptor(metrics)

    def get_lanes(self,role):
        if role not in self.split_lanes:
            cfg=config(self.job,'workstation',role)
            # run() already verified every asset through ensure_day. Pass its
            # unchanged-file receipts to readers instead of hashing each large
            # bank again in all six processes.
            verified={}
            for day in self.job[role+'_days']:
                assets=self.job['assets'][role][day]
                marker=Path(self.job['campaign'])/'data-ready/workstation'/role/(day+'.json')
                receipt=json.loads(marker.read_bytes())
                if receipt['asset_sha256']!=self.job['asset_sha256']:raise ValueError('Asset verification authority changed')
                expected={str(Path(x['path'])):x['sha256'] for x in assets}
                verified[day]={x['path']:dict(x,sha256=expected[str(Path(x['path']))]) for x in receipt['files']}
            cfg['verified_file_hashes']=verified
            cfg.update(host_load_slots=self.host_load_slots,host_load_counts=self.host_load_counts)
            cfg.update(output=str(self.root/'replays'/role),stop_roots=list(map(str,self.stop_roots)),boundary_stop=str(self.stop_roots[0]),
                # Inductor/Triton keys bind graphs and kernels. UI/publication
                # source changes must not discard those content-addressed caches.
                compile_cache_root=str(Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v67/compiler')/role),defer_publication=True,total_gpu_workers=self.workers,indexed_results=True,
                replay_width_workers=2)
            from .performance import tuning
            options=tuning(self.job)
            cfg.update(replay_time_block=options['time_block'],replay_transfer_mode=options['transfer_mode'])
            cfg['maximum_replay_batch_size']=self.job.get('replay_maximum_batch',1024)
            cfg['neural_tile']=self.job.get('neural_tile',64)
            # Retain the six bounded founder plans across sessions instead of
            # evicting four and rebuilding every network pack on the next day.
            # Later generations normally need only one main plan plus mutants.
            cfg['plan_limit']=max(2,self.job['initial_multiplier'])
            # Short, capacity-bounded turns measured faster than competing
            # financial streams or an additional readiness synchronization.
            cfg['gpu_execution_gate']=self.job['campaign'] if self.job.get('profile_gated',True) else None
            cfg['execution_diagnostics']=bool(self.job.get('execution_diagnostics',False))
            self.split_lanes[role]=SessionLanes(cfg,self.workers,self).__enter__()
        return self.split_lanes[role]

    def prepare_successor(self,snapshot):
        self.get_lanes('training').preload(self.job['training_days'],snapshot)

    def evaluate(self,role,days,members,generation):
        stop_check(self.stop_roots)
        if generation==0 and self.job.get('selection_restart'):
            from .founder_reuse import restore
            self.stage='Reuse sealed founder '+role+' receipts; no market replay'
            self.publish(generation=0,replay_role=role,replay_population=len(members))
            return restore(self.job,role,days,self.cohort_snapshot.token)
        if days!=self.job[role+'_days'] or not days:raise ValueError('Global split changed')
        snapshot=self.cohort_snapshot;snapshot.verify(members)
        mutants=self.paired_probe['mutants'] if role=='training' else []
        population=members+mutants
        token=digest([m.payload() for m in population]) if mutants else snapshot.token
        self.active_request=(len(members),snapshot.token,token)
        self.diagnostic_token=digest([m.payload() for m in mutants])
        lanes=self.get_lanes(role)
        if role=='training' and self.job['validation_days']:
            if lanes.config.get('host_resident_prepare'):lanes.preload(days,snapshot)
            self.get_lanes('validation').preload(self.job['validation_days'],snapshot)
        self.stage=('Fresh founder replay' if generation==0 else 'Global training') if role=='training' else 'Validation replay + training-only reproduction'
        from .completed_replays import bindings
        if all(f'{role}/{generation}/{day}' in bindings(self.job) for day in days):
            self.stage='Restore saved founder results'
        self.publish(phase=2,generation=generation,total_sessions=len(days),replay_role=role,evaluated_population=len(members))
        pair=dict(main_token=snapshot.token,extra=[m.payload() for m in mutants]) if mutants else None
        results=lanes.generation(days,population,generation,token,prepared_population=pair)
        if mutants:
            self.paired_probe['metrics']=[r['diagnostic'] for r in results]
            self.paired_probe['receipts']={r['day']:r['diagnostic_ref'] for r in results}
        # Release graph allocations while the other split replays, retaining
        # processes, compiler caches and bounded CPU market residency.
        from time import perf_counter
        release_started=perf_counter()
        from .review_window import due
        if not due(self.job,generation):self.split_lanes[role].release_plans()
        self.publish(split_release_role=role,split_release_seconds=perf_counter()-release_started)
        return [r['metrics'] for r in results],{r['day']:r['ref'] for r in results}


def run(job):
    check_job(job)
    # Verify/copy prepared market assets once before GPU work, never mid-generation.
    for role in ('training','validation'):
        for day in job[role+'_days']:ensure_day(job,'workstation',role,day)
    Global(job).run({})
