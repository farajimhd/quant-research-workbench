"""Persistent, process-isolated rule/replay paths for independent sessions.

Each lane owns its CUDA context, graph buffers, pageable session cache and
bounded upload arena. Progress is coalesced in memory; only the controller's
publication thread writes the terminal snapshot. Each instance enforces one split.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from research.vectorized_backtest.v65.windows_process import quiet_compilers
quiet_compilers()
import gc,json
import numpy as np
import multiprocessing as mp
import queue
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from time import perf_counter, time
import torch
from .data import SessionData
from .evaluator import Execution, PopulationPrograms, replay_cohort
from .genome import Individual
from research.vectorized_backtest.v65.io_pipeline import Publisher, prepare_host
from .prefetch_pipeline import TransferStaging,prepare_next,read_results,fingerprint
from .runtime_path import require_runtime
from research.vectorized_backtest.v65.compiler_runtime import configure_caches

_worker=None


def replay_widths(days,widths,group_size):
    """Keep financial reduction shapes independent of execution concurrency."""
    if not 1<=group_size<=8:raise ValueError('Invalid listing-width group size')
    result={}
    for first in range(0,len(days),group_size):
        cohort=days[first:first+group_size];width=max(widths[d] for d in cohort)
        result.update({day:width for day in cohort})
    return result


from contextlib import contextmanager

@contextmanager
def output_scope(worker,output,*,isolate_results=False):
    """Change receipt destination without evicting read-only market residency."""
    previous=worker.config['output'];resident=worker.resident_results;day=getattr(worker,'last_result_day',None)
    worker.config['output']=str(output)
    if isolate_results:worker.resident_results=None
    try:yield worker
    finally:
        worker.config['output']=previous
        if isolate_results:worker.resident_results=resident;worker.last_result_day=day

class Worker:
    def __init__(self,config,events,lane,*,preparation=None):
        self.config=config;self.events=events;self.lane=lane
        from collections import OrderedDict
        self.host=OrderedDict();self.maximum_host_sessions=config.get('maximum_host_sessions',2)
        if not 2<=self.maximum_host_sessions<=32:raise ValueError('Bounded CPU session residency required')
        self.staging=TransferStaging();self.loader=ThreadPoolExecutor(max_workers=1)
        from collections import OrderedDict
        self.plans=OrderedDict();self.plan_generation=None;self.plan_limit=config.get("plan_limit",2)
        from .session_prefetch import SessionPrefetch
        self.prefetch=SessionPrefetch(self.read_host,workers=config.get('host_prepare_workers',3),capacity=config.get('host_prefetch_depth',6),reserve_gib=config.get('host_reserve_gib',128.),
            admission_gib=config.get('host_admission_gib',32.),report=self.prefetch_report)
        self.load_count_lock=threading.Lock()
        self.loads=0;self.uploads=0
        self.resident_results=None
        self.execution_gate=None
        if config.get('gpu_execution_gate'):
            from .gpu_gate import ReplayGate
            self.execution_gate=ReplayGate(config['gpu_execution_gate'],self.check_stop)
        from .distributed_preparation import PopulationPreparation
        self.owns_population_preparer=preparation is None
        self.population_preparer=preparation or (PopulationPreparation() if config.get('immutable_population_cache') else None)
        self.batch_progress={}
        torch.set_num_threads(1)
        if config['backend']=='compile':
            from research.vectorized_backtest.v65.compiler_runtime import shared_compiler_cache
            cache=(require_runtime(Path(config['compile_cache_root'])/'cache'/f'lane-{lane}')
                   if config.get('compile_cache_root') else shared_compiler_cache(config['source_sha256']))
            configure_caches(cache,recompile_limit=128)
        from .compact_execution import warmup
        self.native_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='v65-native-prepare')
        self.native_ready=self.native_pool.submit(warmup)

    def emit(self,day,**values):
        event=dict(day=day,lane=self.lane,pid=os.getpid(),updated_epoch=time(),**(self.batch_progress|values))
        # Queue is bounded. Progress snapshots may be coalesced, never receipts.
        try:self.events.put_nowait(event)
        except queue.Full:pass

    def prefetch_report(self,info):
        from research.vectorized_backtest.v65.runtime import write_json
        counts=self.config.get('host_load_counts')
        if counts is not None:
            with counts.get_lock():info.update(global_load_active=counts[0],global_load_peak=counts[1],global_load_started=counts[2],global_load_completed=counts[3])
        write_json(Path(self.config['output'])/'prefetch-events'/f'{os.getpid()}-{__import__("time").time_ns()}.json',info)

    def read_host(self,day):
        from .host_load_slots import slot
        with slot(self.config,self.check_stop):return self._read_host(day)

    def _read_host(self,day):
        """Construct a separate CPU session; only the demand thread owns the LRU."""
        c=self.config
        with self.load_count_lock:self.loads+=1
        from .runtime import seed_verified_hashes
        seed_verified_hashes(c.get('verified_file_hashes',{}).get(day,{}))
        item=prepare_host(SessionData(Path(c['inputs'])/day,Path(c['history'])/day,manifest=c['shard_manifest'],
            device='cpu',maximum_gib=c['maximum_data_gib'],maximum_quote_age_seconds=c['execution']['maximum_quote_age_seconds'],
            execution_tail=Path(c['execution_tail'])/c.get('split','training')/day if c.get('execution_tail') else None,
            feature_cache=Path(c['feature_cache'])/day if c['feature_cache'] else None,
            indicator_root=Path(c['indicator_root'])/day if c.get('indicator_root') else None,
            enriched_root=Path(c['enriched_root'])/day if c.get('enriched_root') else None),
            resident=torch.device(c['device']).type=='cuda')
        actual='validation' if item.receipt.get('validation_opened') else 'training'
        if actual!=c.get('split','training'):
            item.close();raise ValueError('Session input crossed training/validation boundary')
        from research.vectorized_backtest.v65.publication import write_json
        write_json(Path(c['output'])/'quote-coverage'/f'{day}.json',dict(item.quote_coverage,input_identity=item.identity))
        return item

    def read_ahead(self,day):
        # Transfer ownership to prefetch; cancellation may safely close it.
        item=self.host.pop(day,None)
        if item is None:item=self.read_host(day)
        return prepare_next(item,Path(self.config['output'])/'result-cache'/(day+'.json'),self.config)

    def release_inactive_sessions(self):
        """Only between completed replays; evict recomputable data, not results."""
        before=__import__('psutil').virtual_memory().available
        self.prefetch.clear()
        for plan in self.plans.values():plan.close()
        plan=None
        self.plans.clear();self.staging.close();self.staging=TransferStaging()
        for item in list(self.host.values()):item.close()
        item=None
        self.host.clear();self.resident_results=None
        gc.collect()
        if torch.device(self.config['device']).type=='cuda':torch.cuda.empty_cache()
        from research.vectorized_backtest.v65.publication import write_json
        write_json(Path(self.config['output'])/'host-cache-events'/f'{os.getpid()}-{__import__("time").time_ns()}.json',dict(reason='inactive session blocks unchanged admission',available_before_bytes=before,available_after_bytes=__import__('psutil').virtual_memory().available,reserve_gib=self.config.get('host_reserve_gib',128.),admission_gib=self.config.get('host_admission_gib',32.),financial_results_preserved=True))

    def load(self,day):
        c=self.config
        if day not in self.host:
            required=(c.get('host_reserve_gib',128.)+c.get('host_admission_gib',32.))*2**30
            if self.host and __import__('psutil').virtual_memory().available<required:self.release_inactive_sessions()
            # Evict before allocation, rather than temporarily retaining a third
            # complete session. Plans own any still-live device references.
            while len(self.host)>=self.maximum_host_sessions:self.host.popitem(last=False)
            # Dropping the LRU reference releases acyclic session arrays.
            # A full-process collection here scans all live result/genome graphs
            # and stalls the loader and preparation threads under the GIL.
            from research.vectorized_backtest.v65.host_memory import wait_for_headroom
            wait_for_headroom(c['output'],lambda **v:self.emit(day,**v),
                reserve_gib=c.get('host_reserve_gib',128. if torch.device(c['device']).type=='cuda' else 0.),
                admission_gib=c.get('host_admission_gib',32.),boundary_stop=c.get('boundary_stop'))
            self.host[day]=self.read_host(day)
        self.host.move_to_end(day)
        item=self.host[day]
        while len(self.host)>self.maximum_host_sessions:self.host.popitem(last=False)
        # Active evaluation and bounded plans retain their own references.
        return item

    def retire_device_sessions(self,day,device):
        """One GPU session per lane; pageable prefetch and genome plans survive."""
        if device.type!='cuda':return
        for old_day,old in list(self.host.items()):
            if old_day!=day:
                if hasattr(old,'resident_features'):old.resident_features.deactivate()
                old.deactivate()
        # Other processes cannot reuse this lane's allocator cache. Return it
        # before admitting the next session, not after its upload peaks.
        torch.cuda.empty_cache()

    def run(self,day,next_day,payload,generation,token,replay_width):
        if getattr(self,'last_result_day',None)!=day:self.resident_results=None
        self.last_result_day=day
        self.check_stop()
        market_cached=day in self.host
        from .lineage import program_key
        from research.vectorized_backtest.v65.runtime import file_hash
        from hashlib import sha256
        self.batch_progress={}
        c=self.config;started=perf_counter();self.emit(day,stage='Load session',candidate_offset=0)
        self.retire_device_sessions(day,torch.device(c['device']))
        if payload is None:
            if self.population_preparer is None or self.population_preparer.token!=token:raise ValueError('Missing immutable worker population')
            payload=self.population_preparer.payload
        prepared=self.population_preparer.submit(payload,token) if self.population_preparer else None
        lookahead=[next_day] if isinstance(next_day,str) else list(next_day or ())
        self.prefetch.schedule([day]+(lookahead if c.get('host_prefetch',False) else []),self.host)
        item=self.prefetch.take(day)
        prefetched=item is not None
        if prefetched:self.host[day]=item;self.host.move_to_end(day)
        else:item=self.loader.submit(self.load,day).result()
        market_load_seconds=perf_counter()-started
        self.emit(day,stage='Prepare population',market_load_seconds=market_load_seconds)
        device=torch.device(c['device'])
        if prepared is None:population=[Individual.restore(v) for v in payload];packed_population=None;prepared_keys=None
        else:population,prepared_keys,packed_population,_=prepared.result()
        root=require_runtime(Path(c['output'])/f'generation-{generation:04d}'/day)
        cache_path=require_runtime(Path(c['output'])/'result-cache')/(day+'.json')
        from .entry_sharing import VERSION as entry_sharing_version
        identity=dict(source=c['source_sha256'],entry_sharing=entry_sharing_version,data=item.identity,execution=c['execution'],
                      split=c.get('split','training'),episode_capacity=c.get('episode_capacity',65536))
        keys=prepared_keys if prepared_keys is not None else [v.get('canonical_sha256') or program_key(m) for v,m in zip(payload,population)];cached={};cache_prefetched=False
        transfer_prefetched=bool(getattr(item,'prefetched_transfer',None))
        resident=c.get('retain_session_residency',False) and c.get('reuse_results',True)
        if resident and self.resident_results is not None:
            prior_identity,cached=self.resident_results
            if prior_identity!=identity:raise ValueError('Resident result cache contract changed')
            cached=dict(cached)
        elif cache_path.exists() and c.get('reuse_results',True):
            self.emit(day,stage='Read prior results',market_load_seconds=market_load_seconds)
            ahead=getattr(item,'prefetched_results',None)
            cache_prefetched=bool(ahead and ahead[0]==str(cache_path) and ahead[1]==fingerprint(cache_path))
            record=ahead[2] if cache_prefetched else read_results(cache_path,keys)[1]
            item.prefetched_results=None
            if record['identity']!=identity:
                # Only an exact, archived cache receipt may cross the explicitly
                # authorized source transition. Every numerical/input field stays equal.
                from .resume_authority import accept_cache
                accept_cache(c,cache_path,record,identity)
            cached=record['results']
        missing=[i for i,k in enumerate(keys) if k not in cached]
        from .replay_batches import plan_batches
        ceiling=c.get('maximum_replay_batch_size',c['batch_size'])
        batches=plan_batches(population,missing,replay_width,c['batch_size'],ceiling)
        self.batch_progress=dict(completed_local_batches=0,total_local_batches=len(batches),total_candidates=len(population),
            reused_candidates=len(population)-len(missing),evaluated_candidates=len(missing),completed_candidates=len(population)-len(missing),
            session_started_epoch=time()-(perf_counter()-started),load_seconds=perf_counter()-started)
        timings=[];execution=Execution(**c['execution'])
        try:
            if missing:
                if device.type=='cuda':
                    from .performance import memory_budget
                    budget=memory_budget(item,population,batches,c.get('replay_time_block',256))
                    from .publication import write_json
                    write_json(Path(c['output'])/'memory-admission'/f'{generation}-{day}.json',
                        dict(budget,lane_memory_bytes=c['lane_memory_bytes']))
                    if budget['required_bytes']>c['lane_memory_bytes']:
                        raise MemoryError(f"Compact replay needs {budget['required_bytes']} bytes; lane admits {c['lane_memory_bytes']}")
                if not c.get('retain_session_residency') or item.device!=device:
                    self.emit(day,stage='Upload session',market_load_seconds=market_load_seconds)
                    self.staging.activate([(day,item)],device)
                    self.uploads+=1
            load_seconds=perf_counter()-started
            cleanup_started=perf_counter()
            # Keep previously prepared sessions for subsequent generations.
            # CPU futures own only their bounded lookahead; only this owner
            # mutates the LRU or activates CUDA tensors.
            while len(self.host)>self.maximum_host_sessions:
                oldest=next(k for k in self.host if k!=day)
                self.host.pop(oldest).close()
            if c.get('host_prefetch',False):self.prefetch.schedule(lookahead,self.host)
            cleanup_seconds=perf_counter()-cleanup_started
            from .publication_lane import Buffer
            deferred=getattr(self,'defer_publication',False)
            with (Buffer() if deferred else Publisher()) as publisher,ThreadPoolExecutor(max_workers=1,thread_name_prefix='v57-results') as results_pool:
                offset=0;pending_result=None;pending_timing=None
                for batch_index,selection in enumerate(batches):
                    self.batch_progress['completed_local_batches']=batch_index
                    self.batch_progress.update(completed_candidates=len(population)-len(missing)+offset,batch_candidates=len(selection),completed_seconds=0)
                    members=[population[i] for i in selection]
                    # Native candidate tails avoid replaying artificial duplicates.
                    actual=len(members)
                    batch_start=perf_counter();rule_start=time()
                    batch_keys=[keys[i] for i in selection]
                    batch_key=tuple(batch_keys)
                    if self.plan_generation!=generation:
                        for value in self.plans.values():value.close()
                        self.plans.clear();self.plan_generation=generation
                    if batch_key not in self.plans:
                        self.emit(day,stage='Pack networks',candidate_offset=offset,market_load_seconds=market_load_seconds)
                        if len(self.plans)>=self.plan_limit:
                            _,old=self.plans.popitem(last=False);old.close()
                        if packed_population is None:plan=PopulationPrograms(members,device)
                        else:
                            from .network_pack import select_packed
                            plan=PopulationPrograms(members,device,packed=select_packed(packed_population,selection))
                        plan.plan.cuda_block=c.get('neural_tile',plan.plan.cuda_block)
                        self.plans[batch_key]=plan
                    plan=self.plans[batch_key];self.plans.move_to_end(batch_key)
                    plan.plan.replay_time_block=c.get('replay_time_block',256)
                    plan.plan.replay_transfer_mode=c.get('replay_transfer_mode','synchronous')
                    prior={k:getattr(plan.plan,k,0) for k in ('builds','hits','evictions','capture_seconds')}
                    self.emit(day,stage='Evaluate rules',candidate_offset=offset,rule_tiles=0,total_rule_tiles=0,
                              completed_seconds=0,total_seconds=item.clocks-1,replay_started=None)
                    gates=plan.evaluate(item,maximum_gate_gib=c['maximum_gate_gib'],
                        progress=lambda e:self.emit(day,stage='Evaluate rules',candidate_offset=offset,
                            rule_tiles=e['tiles'],total_rule_tiles=e['total_tiles']))
                    if device.type=='cuda':torch.cuda.synchronize(device)
                    rule_seconds=perf_counter()-batch_start;replay_start=time();replay_clock=perf_counter()
                    worker_pre_replay_seconds=perf_counter()-started
                    previous_finished=getattr(self,'previous_replay_finished',None)
                    self.emit(day,stage='Replay positions',candidate_offset=offset,replay_started=replay_start)
                    def replay_progress(done,total):
                        self.check_stop()
                        if c.get('host_prefetch',False):self.prefetch.schedule(lookahead,self.host)
                        self.emit(day,stage=getattr(item,'execution_phase','Replay positions'),candidate_offset=offset,completed_seconds=done,total_seconds=total,**self.prefetch.stats())
                    gate_options={'execution_gate':self.execution_gate} if self.execution_gate is not None else {}
                    if execution.order_mode!='immediate':item.execution_order_root=root/'execution-orders'/str(offset)
                    if execution.order_mode!='immediate' and c.get('execution_diagnostics'):item.execution_diagnostic_root=root/'execution-diagnostic'/str(offset)
                    gate_wait=self.execution_gate.wait_seconds if self.execution_gate is not None else 0.
                    prime_start=perf_counter();prime_seconds=self.native_ready.result();prime_wait=perf_counter()-prime_start
                    result=self.replay([item],members,[gates],execution=execution,backend=c['backend'],listing_width=replay_width,
                        episode_capacity=c.get('episode_capacity',65536),trace=c.get('trace_validation',False) or c.get('split','training')=='training',
                        progress=replay_progress,**gate_options)[0]
                    metrics={k:v.detach().cpu().tolist() if isinstance(v,torch.Tensor) else v for k,v in result.items()}

                    # One bounded CPU result transaction overlaps the next GPU
                    # batch. Publication still waits for every result and error.
                    if pending_result is not None:
                        values,seconds=pending_result.result();cached.update(values);pending_timing['attribution_seconds']=seconds
                    from .session_pipeline import finish_metrics
                    pending_result=results_pool.submit(finish_metrics,metrics,item,selection,keys)
                    timing=dict(day=day,offset=offset,candidates=actual,padded_candidates=len(members),
                        market_cached=market_cached,worker_load_count=self.loads,worker_upload_count=self.uploads,
                        configured_batch_size=c['batch_size'],maximum_replay_batch_size=ceiling,
                        load_seconds=load_seconds if offset==0 else 0.,cleanup_seconds=cleanup_seconds if offset==0 else 0.,worker_pre_replay_seconds=worker_pre_replay_seconds if offset==0 else 0.,host_prefetched=prefetched,result_cache_prefetched=cache_prefetched,transfer_prefetched=transfer_prefetched,rule_seconds=rule_seconds,
                        replay_seconds=perf_counter()-replay_clock,gpu_gate_wait_seconds=self.execution_gate.wait_seconds-gate_wait if self.execution_gate is not None else 0.,pid=os.getpid(),finished_epoch=time())
                    timing['replay_started_epoch']=replay_start
                    timing.update(native_preparation_seconds=prime_seconds,native_preparation_wait_seconds=prime_wait,
                                  market_load_seconds=market_load_seconds if offset==0 else 0.)
                    timing.update(getattr(plan.plan,'last_replay_profile',{}))
                    timing['since_previous_replay_seconds']=None if previous_finished is None else replay_start-previous_finished
                    self.previous_replay_finished=timing['finished_epoch']
                    if plan.plan is not None:
                        timing.update({k:getattr(plan.plan,k)-v for k,v in prior.items()})
                    timings.append(timing);pending_timing=timing;del gates,result
                    if device.type=='cuda':
                        timing['cuda_allocated_bytes']=torch.cuda.memory_allocated(device)
                        timing['cuda_reserved_bytes_before_release']=torch.cuda.memory_reserved(device)
                        release_started=perf_counter();torch.cuda.empty_cache()
                        timing['cuda_cache_release_seconds']=perf_counter()-release_started
                        timing['cuda_reserved_bytes_after_release']=torch.cuda.memory_reserved(device)
                    self.emit(day,stage='Batch complete',candidate_offset=offset,completed_local_batches=len(timings),
                        completed_candidates=len(population)-len(missing)+offset+len(selection),gpu_gate_wait_seconds=self.execution_gate.wait_seconds if self.execution_gate else 0.)
                    offset+=len(selection)
                if pending_result is not None:
                    values,seconds=pending_result.result();cached.update(values);pending_timing['attribution_seconds']=seconds
                self.emit(day,stage='Publish session results',completed_candidates=len(population))
                selected={key:cached[key] for key in keys}
                raw=None if deferred else json.dumps(selected,sort_keys=True,separators=(',',':'),allow_nan=False)
                publisher.write(cache_path,dict(identity=identity,results=selected,results_sha256=None if deferred else sha256(raw.encode()).hexdigest()))
                merged={k:[cached[key][k] for key in keys] for k in cached[keys[0]]}
                missing_set=set(missing)
                merged['result_reused']=[i not in missing_set for i in range(len(population))]
                publisher.write(root/'receipt.json',dict(day=day,metrics=merged,identity=item.identity,
                    **({'program_keys':keys,'result_identity':identity} if c.get('indexed_results') else {}),
                    population_sha256=token,case_owners=[m.case_owner for m in population],full_session=True,split=c.get('split','training'),
                    validation_opened=c.get('split')=='validation',execution=dict(c['execution']),
                    listing_ids=item.listing_ids.tolist(),reused_candidates=len(population)-len(missing),
                    evaluated_candidates=len(missing)))
                publisher.write(root/'timings.json',timings)
            # Only retain a successfully published generation. The disk cache
            # remains hashed and is revalidated on process/session restart.
            if resident:self.resident_results=(identity,selected)
            if deferred:self.deferred_writes=publisher.writes
            self.emit(day,stage='Session complete',completed_local_batches=len(timings),completed_seconds=item.clocks-1)
            return dict(day=day,timings=timings,wall_seconds=perf_counter()-started,pid=os.getpid(),clocks=item.clocks-1)
        except BaseException as error:
            self.emit(day,stage='Failed',status='failed',finished_epoch=time(),error=str(error));raise
        finally:
            if not self.config.get('retain_session_residency'):
                if hasattr(item,'resident_features'):item.resident_features.deactivate()
                item.deactivate()

    def check_stop(self):
        if any((Path(p)/'STOP').exists() for p in self.config.get('stop_roots',[])):
            raise InterruptedError('V60 campaign stop requested')

    def replay(self,*args,**kwargs):
        """Retry only a preflight rejection after evicting recomputable graphs."""
        try:
            return replay_cohort(*args,**kwargs)
        except MemoryError as error:
            if str(error) not in ('Packed V7 cohort exceeds GPU headroom','Neural replay exceeds physical GPU headroom') or args[0][0].device.type!='cuda':raise
            device=args[0][0].device
            before=dict(free=torch.cuda.mem_get_info(device)[0],reserved=torch.cuda.memory_reserved(device))
        # Release the exception traceback before clearing graph-owned pools.
        for plan in self.plans.values():plan.close()
        gc.collect();torch.cuda.empty_cache()
        from research.vectorized_backtest.v65.runtime import write_json
        write_json(Path(self.config['output'])/f'capture-recovery-{os.getpid()}-{__import__("time").time_ns()}.json',
                   dict(before=before,after=dict(free=torch.cuda.mem_get_info(device)[0],reserved=torch.cuda.memory_reserved(device)),
                        plans=len(self.plans),identical_replay_retry=True))
        return replay_cohort(*args,**kwargs)

    def close(self):
        if hasattr(self,'native_pool'):self.native_pool.shutdown(wait=True,cancel_futures=True)
        if getattr(self,'execution_gate',None):self.execution_gate.close();self.execution_gate=None
        if self.population_preparer and self.owns_population_preparer:self.population_preparer.close()
        self.resident_results=None
        try:self.prefetch.close()
        finally:
            self.loader.shutdown(wait=True,cancel_futures=True);self.staging.close()
            for plan in self.plans.values():plan.close()
            self.plans.clear()
            for item in list(self.host.values()):item.close()
            self.host.clear()


def initialize(config,events,lane):
    global _worker
    _worker=Worker(config,events,lane)
    _worker.defer_publication=bool(config.get('defer_publication',False))


def evaluate(*args):
    try:
        profiler=None
        if _worker.config.get('execution_diagnostics'):
            import cProfile
            profiler=cProfile.Profile();profiler.enable()
        try:result=_worker.run(*args)
        finally:
            if profiler is not None:
                profiler.disable()
                path=Path(_worker.config['output'])/'cpu-profile'/f'{args[3]}-{args[0]}.prof'
                path.parent.mkdir(parents=True,exist_ok=True);profiler.dump_stats(str(path))
        if getattr(_worker,'defer_publication',False):result['writes']=_worker.deferred_writes
        return result
    except Exception as error:
        # Compiler exceptions may capture non-pickleable closure cells. Preserve
        # the real worker traceback as text across the process boundary.
        import traceback
        raise RuntimeError(traceback.format_exc()) from None
def cleanup():_worker.close()

def preload(days,payload,token):
    """CPU-only demand preparation while the other split owns GPU replay."""
    _worker.population_preparer.submit(payload,token)
    _worker.prefetch.schedule(days,_worker.host)


def queue_sessions(days):
    """Start bounded CPU loads, without a cross-lane readiness barrier."""
    _worker.prefetch.schedule(days,_worker.host)


def choose_ready(days):
    return _worker.prefetch.ready_day(days,_worker.host,_worker.check_stop)


def prime_sessions(days):
    """Bounded CPU preparation; keep complete sessions across generations.

    Admission may defer under external RAM pressure. Demand loading remains
    available, and no financial result or authoritative input is discarded.
    """
    worker=_worker;began=perf_counter();ready=0;deferred=[]
    if len(days)>worker.maximum_host_sessions:
        raise ValueError('Resident preparation exceeds assigned cache capacity')
    for index,day in enumerate(days):
        worker.check_stop()
        worker.prefetch.schedule(days[index:],worker.host)
        if day not in worker.host:
            worker.emit(day,stage='Prepare resident RAM',prepared_sessions=ready,assigned_sessions=len(days))
            item=worker.prefetch.take(day)
            if item is None:
                deferred.append(day);continue
            worker.host[day]=item
        ready+=1
        worker.emit(day,stage='Resident RAM ready',prepared_sessions=ready,assigned_sessions=len(days))
    result=dict(pid=os.getpid(),lane=worker.lane,ready=ready,deferred=deferred,
                seconds=perf_counter()-began,available_bytes=__import__('psutil').virtual_memory().available)
    from .publication import write_json
    write_json(Path(worker.config['output'])/'resident-preparation'/f'{worker.lane}.json',result)
    return result

def evaluate_preloaded(day,next_day,payload,generation,token,width,parent_token,extra):
    if _worker.population_preparer.token==parent_token:
        _worker.population_preparer.append_members(parent_token,payload,[Individual.restore(p) for p in extra],token)
    return evaluate(day,next_day,payload,generation,token,width)
def evaluate_scoped(day,next_day,payload,generation,token,replay_width,output,isolate_results):
    with output_scope(_worker,output,isolate_results=isolate_results):
        return _worker.run(day,next_day,payload,generation,token,replay_width)


def release_plans():
    for plan in _worker.plans.values():plan.close()
    _worker.plans.clear()
    # The inactive split must not retain its market banks on the GPU while
    # the other split admits two replay lanes. Keep pageable host residency.
    for item in list(_worker.host.values()):
        if hasattr(item,'resident_features'):item.resident_features.deactivate()
        item.deactivate()
    _worker.staging.close();_worker.staging=TransferStaging()
    gc.collect()
    if torch.device(_worker.config['device']).type=='cuda':torch.cuda.empty_cache()


class SessionLanes:
    def __init__(self,config,workers,panel):
        self.config=config;self.workers=workers;self.panel=panel
        self.context=mp.get_context('spawn');self.events=self.context.Queue(maxsize=256)
        self.pools=[];self.stop=threading.Event();self.error=None;self.widths={}

    def pump(self):
        try:
            while not self.stop.is_set():
                try:event=self.events.get(timeout=.1)
                except queue.Empty:continue
                progress=getattr(self,'independent_progress',None)
                if progress is None:self.panel.session_progress(event)
                else:progress.event(event)
        except BaseException as error:self.error=error

    def __enter__(self):
        c=dict(self.config)
        if c.get('shared_replay'):
            raise ValueError('V60 uses two independent persistent replay lanes')
        # Preserve the measured RulePlan budget per execution path. Splitting
        # one plan's cache between workers forces repeated graph recaptures.
        # Admission below still bounds the combined resident allocation.
        c['capture_gib']=float(c.get('capture_gib',4.))
        if not .25<=c['capture_gib']<=16.:raise ValueError('Capture cache must be between 0.25 and 16 GiB per worker')
        c['lane_memory_bytes']=torch.cuda.mem_get_info(c['device'])[0]*.8/c.get('total_gpu_workers',self.workers) if torch.device(c['device']).type=='cuda' else 0
        self.pools=[ProcessPoolExecutor(max_workers=1,mp_context=self.context,
            initializer=initialize,initargs=(c,self.events,lane)) for lane in range(self.workers)]
        self.thread=threading.Thread(target=self.pump,name='v18-session-progress',daemon=True);self.thread.start()
        return self

    def generation(self,days,population,generation,token,*,prepared_population=None,output_override=None,isolate_results=False):
        for future in getattr(self,'preload_futures',[]):future.result()
        self.preload_futures=[]
        if output_override is not None and self.config.get('shared_replay'):raise ValueError('Scoped output requires independent process lanes')
        if isolate_results and output_override is None:raise ValueError('Result isolation requires an explicit output destination')
        from .lineage import program_key
        if self.config.get('shared_replay'):
            raise ValueError('V60 uses two independent persistent replay lanes')
        else:payload=[v.payload() for v in population]
        timings=[]
        if not self.widths:
            manifest=json.loads(Path(self.config['shard_manifest']).read_bytes())
            widths={s['day']:len(s['selected']) for s in manifest['sessions']}
            def width(day):return day,widths[day]
            with ThreadPoolExecutor(max_workers=self.workers) as reader:self.widths=dict(reader.map(width,days))
        fixed_widths=replay_widths(days,self.widths,self.config.get('replay_width_workers',self.workers))
        if self.config.get('shared_replay'):
            raise ValueError('V60 uses two independent persistent replay lanes')
        from .session_pipeline import SessionProgress,independent_sessions
        self.independent_progress=SessionProgress(self.panel,days,len(population),generation,self.config.get('split','training'))
        dispatched=[False]*self.workers
        bound=getattr(self.panel,'job',{}).get('resume_transition',{}).get('completed_replays',{})
        role=self.config.get('split','training')
        can_restore=lambda day:output_override is None and not prepared_population and f'{role}/{generation}/{day}' in bound
        queued={};dispatched_days=[set() for _ in range(self.workers)]
        if self.config.get('host_resident_prepare'):
            # FIFO within each process ensures scheduling precedes its replay.
            # Never wait for another lane's cold session before dispatching a
            # ready lane. The existing bounded queue refills as that lane runs.
            for lane,pool in enumerate(self.pools):
                assigned=[d for d in self.config.get('prefetch_days',days)[lane::self.workers] if not can_restore(d)]
                if assigned:queued[lane]=pool.submit(queue_sessions,assigned[:self.config.get('host_prefetch_depth',6)])
        def choose(lane,remaining):
            if lane in queued:queued.pop(lane).result()
            restored=next((d for d in remaining if can_restore(d)),None)
            return restored or self.pools[lane].submit(choose_ready,remaining).result()
        def submit(lane,index,day,next_day):
            if self.error is not None:raise RuntimeError('Session progress consumer failed') from self.error
            if can_restore(day):
                from concurrent.futures import Future
                from .completed_replays import restore
                from .controller_metrics import compact
                began=perf_counter()
                panels,refs=restore(self.panel.job,self.config['output'],role,[day],generation,token,len(population))
                result=dict(day=day,metrics=compact(panels[0]),ref=refs[day],restored=True,
                            timings=[],wall_seconds=perf_counter()-began,clocks=19799)
                future=Future();future.set_result(result);return future
            assigned=days[lane::self.workers]
            dispatched_days[lane].add(day)
            next_day=[d for d in assigned if d not in dispatched_days[lane] and not can_restore(d)][:self.config.get('host_prefetch_depth',6)]
            # The generation's immutable population crosses IPC once per lane.
            first=not dispatched[lane];dispatched[lane]=True
            request=payload if (first and getattr(self,'preloaded_token',None)!=token) or not self.config.get('immutable_population_cache') else None
            if index==0 and prepared_population and getattr(self,'preloaded_token',None)==prepared_population['main_token']:
                return self.pools[lane].submit(evaluate_preloaded,day,next_day,payload,generation,token,fixed_widths[day],prepared_population['main_token'],prepared_population['extra'])
            if output_override is not None:return self.pools[lane].submit(evaluate_scoped,day,next_day,request,generation,token,fixed_widths[day],str(output_override),isolate_results)
            return self.pools[lane].submit(evaluate,day,next_day,request,generation,token,fixed_widths[day])
        try:
            results=independent_sessions(days,self.workers,submit,self.independent_progress,
                choose=choose if self.config.get('host_resident_prepare') else None)
            if self.error is not None:raise RuntimeError('Session progress consumer failed') from self.error
            if self.config.get('defer_publication'):return results
            return [timing for result in results for timing in result['timings']]
        finally:self.independent_progress=None;self.preloaded_token=None

    def preload(self,days,snapshot):
        self.preloaded_token=snapshot.token
        days=self.config.get('prefetch_days',days)
        self.preload_futures=[pool.submit(preload,days[lane::self.workers][:self.config.get('host_prefetch_depth',6)],snapshot.payload,snapshot.token) for lane,pool in enumerate(self.pools) if lane<len(days)]

    def release_plans(self):
        if self.config.get('shared_replay'):
            raise ValueError('V60 uses two independent persistent replay lanes')
        # Independent worker cleanup must overlap rather than serialize two
        # garbage-collection/cache-release pauses at every split boundary.
        futures=[pool.submit(release_plans) for pool in self.pools]
        for future in futures:future.result()

    def __exit__(self,*args):
        if self.config.get('shared_replay'):
            raise ValueError('V60 uses two independent persistent replay lanes')
        errors=[]
        try:
            for pool in self.pools:
                try:pool.submit(cleanup).result()
                except BaseException as error:errors.append(error)
                finally:pool.shutdown(wait=True,cancel_futures=True)
        finally:
            self.stop.set();self.thread.join();self.events.close();self.events.join_thread()
        if errors and args[0] is None:raise RuntimeError('Session worker cleanup failed') from errors[0]
