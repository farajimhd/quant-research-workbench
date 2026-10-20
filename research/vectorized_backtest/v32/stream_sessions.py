"""One CUDA owner, at most two independent sessions and four replay streams.

CPU evolution submits requests; only the service thread prepares or runs CUDA.
Each session retains its own candidates, recurrent state, ledger and receipts.
"""
import gc
import json
import os
import queue
import threading
from concurrent.futures import Future, TimeoutError
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter, time, sleep

import psutil
import torch

from .session_lanes import Worker
from .evaluator import Execution, PopulationPrograms, replay_cohort
from .genome import Individual
from .lineage import program_key
from .result_processing import process_metrics, publish_results, timed_call
from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime


class StreamWorker(Worker):
    def close(self):
        self.market_block=None
        super().close()

    def release_phase(self):
        """Completed global sessions cannot be reused before LRU eviction."""
        if self.config.get('retain_session_residency'):
            raise ValueError('Persistent local session residency must remain owned')
        before=psutil.virtual_memory().available;count=len(self.host)
        self.prefetch.close();self.market_block=None
        for item in self.host.values():item.close()
        self.host.clear();pinned=self.staging.release_completed()
        gc.collect();torch.cuda.empty_cache()
        from .publication import write_json
        report=dict(source_sha256=self.config['source_sha256'],lane=self.lane,
            host_sessions_released=count,pinned_staging_bytes_released=pinned,
            available_before_gib=before/2**30,available_after_gib=psutil.virtual_memory().available/2**30)
        write_json(Path(self.config['output'])/'phase-releases'/f'{__import__("time").time_ns()}-lane-{self.lane}.json',report)
        return report

    def __init__(self,config,events,lane):
        # Query/admit on the CUDA owner too: a local evolution thread must not
        # invoke CUDA APIs while another session is capturing a graph.
        config=dict(config,lane_memory_bytes=torch.cuda.mem_get_info(config['device'])[0]*.8/2)
        super().__init__(config,events,lane)

    def steps(self, day, next_day, payload, generation, token, replay_width):
        self.current_day=day
        c=self.config; started=perf_counter(); self.batch_progress={}
        self.emit(day,stage='Load session',candidate_offset=0)
        self.prefetch.release_under_pressure()
        item=self.prefetch.take(day); prefetched=item is not None
        if prefetched:self.host[day]=item; self.host.move_to_end(day)
        else:item=self.load(day)
        device=torch.device(c['device'])
        from .preparation import submit_when_ready, _restore_population
        from functools import partial
        def admission_report(**values):
            self.prefetch.release_under_pressure()
            self.emit(day,**values)
        submit=partial(submit_when_ready,output=c['output'],
            emit=admission_report,boundary_stop=c.get('boundary_stop'))
        from .session_owner import PackedPopulation
        from .global_pipeline import GenerationPopulation
        restore_started=perf_counter()
        prepared_population=payload if isinstance(payload,PackedPopulation) else None
        generation_population=payload if isinstance(payload,GenerationPopulation) else None
        if generation_population is not None:
            population,keys=payload.members,payload.keys;restore_seconds=0.
        elif prepared_population is None:
            (population,keys),restore_seconds=yield submit(timed_call,_restore_population,payload)
        else:
            population,keys=payload.members,payload.keys;restore_seconds=0.
        restore_wait_seconds=perf_counter()-restore_started
        root=require_runtime(Path(c['output'])/f'generation-{generation:04d}'/day)
        cache_path=require_runtime(Path(c['output'])/'result-cache')/(day+'.json')
        identity=dict(source=c['source_sha256'],data=item.identity,execution=c['execution'],
                      split=c.get('split','training'),episode_capacity=c.get('episode_capacity',65536))
        cached={}; resident=c.get('retain_session_residency',False) and c.get('reuse_results',True)
        if resident and self.resident_results is not None:
            prior,cached=self.resident_results
            if prior!=identity:raise ValueError('Resident result cache contract changed')
            cached=dict(cached)
        elif cache_path.exists() and c.get('reuse_results',True):
            record=json.loads(cache_path.read_text(encoding='utf-8-sig'))
            if record['identity']!=identity:raise ValueError('Result cache contract changed')
            raw=json.dumps(record['results'],sort_keys=True,separators=(',',':'),allow_nan=False)
            if sha256(raw.encode()).hexdigest()!=record['results_sha256']:raise ValueError('Result cache integrity mismatch')
            cached=record['results']
        missing=[i for i,k in enumerate(keys) if k not in cached]
        if prepared_population is not None and missing!=prepared_population.missing:
            raise ValueError('Persistent owner and replay cache selection disagree')
        batch=c['batch_size']; timings=[]; execution=Execution(**c['execution'])
        self.batch_progress=dict(completed_local_batches=0,total_local_batches=(len(missing)+batch-1)//batch)
        packing={};packing_reused={};cpu_pending=None;staging_released_bytes=0
        try:
            if missing:
                required=item.bytes+item.resident_features.bytes+(2+c['capture_gib'])*1024**3
                required+=2*batch*(c.get('episode_capacity',65536)+replay_width)*32
                if required>c['lane_memory_bytes']:raise MemoryError('Independent session exceeds admitted GPU memory budget')
                if not c.get('retain_session_residency') or item.device!=device:
                    self.staging.activate([(day,item)],device); self.uploads+=1
                    if c.get('retain_session_residency'):
                        staging_released_bytes=self.staging.release_completed()
                        self.emit(day,stage='Upload complete',released_staging_bytes=staging_released_bytes)
            load_seconds=perf_counter()-started
            if c.get('host_prefetch',False) and next_day is not None and next_day not in self.host:
                for old_day in list(self.host):
                    if old_day!=day:self.host.pop(old_day).close()
                gc.collect(); self.prefetch.start(next_day,self.read_host)
            from .preparation import _pack
            def prepack(first):
                # Current pair plus one pair ahead per session, never a population-wide queue.
                for offset in range(first,min(first+2*batch,len(missing)),batch):
                    if offset not in packing:
                        if generation_population is not None:
                            selection=missing[offset:offset+batch]
                            hits=generation_population.hits
                            packing[offset]=generation_population.pack(selection,submit)
                            packing_reused[offset]=generation_population.hits>hits
                        elif prepared_population is None:
                            packing[offset]=submit(_pack,[population[i] for i in missing[offset:offset+batch]])
                        else:
                            future=Future();future.set_result(prepared_population.batches.pop(offset))
                            packing[offset]=future
            prepack(0)
            for first in range(0,len(missing),2*batch):
                prepack(first+2*batch)
                pending=[]
                for offset in range(first,min(first+2*batch,len(missing)),batch):
                    selection=missing[offset:offset+batch]; members=[population[i] for i in selection]
                    batch_start=perf_counter()
                    # Plans are independent per captured cohort. Never evict a
                    # plan still referenced by another queued CUDA graph.
                    wait_started=perf_counter()
                    packed,packing_seconds=yield packing.pop(offset)
                    packing_wait_seconds=perf_counter()-wait_started
                    reused_pack=packing_reused.pop(offset,False)
                    if reused_pack:packing_seconds=0.
                    upload_started=perf_counter()
                    plan=PopulationPrograms(members,device,packed=packed)
                    upload_seconds=perf_counter()-upload_started
                    self.emit(day,stage='Capture replay graph',candidate_offset=offset,completed_seconds=0,
                              total_seconds=item.clocks-1,replay_started=None)
                    gates=plan.evaluate(item,maximum_gate_gib=c['maximum_gate_gib'])
                    from .market_block import MarketBlock
                    market=getattr(self,'market_block',None)
                    if market is None or market.data is not item:
                        self.market_block=market=MarketBlock(gates,item)
                    gates.market_block=market
                    torch.cuda.synchronize(device)
                    if torch.cuda.mem_get_info(device)[0]<8*2**30:raise MemoryError('Shared replay GPU headroom below 8 GiB')
                    capture_started=perf_counter()
                    prepared=replay_cohort([item],members,[gates],execution=execution,backend=c['backend'],
                        listing_width=replay_width,episode_capacity=c.get('episode_capacity',65536),
                        trace=c.get('split','training')=='training',capture_only=True,replay_block=32)
                    prepared.replay.market_block=market
                    pending.append((offset,selection,plan,prepared,perf_counter()-batch_start,
                        dict(cpu_packing_seconds=packing_seconds,packing_wait_seconds=packing_wait_seconds,
                             generation_packing_reused=reused_pack,
                             plan_upload_seconds=upload_seconds,capture_seconds=perf_counter()-capture_started)))
                self.emit(day,stage='Replay positions',candidate_offset=first,replay_started=time())
                # The CUDA owner advances all admitted session groups together.
                replay_seconds=yield [p[3].replay for p in pending]
                for offset,selection,plan,prepared,rule_seconds,preparation_times in pending:
                    export_started=perf_counter()
                    result=prepared.export()[0]
                    # Complete D2H on the CUDA owner; only independent CPU arrays
                    # cross the process boundary. No graph/device lifetime escapes.
                    snapshot={k:v.detach().cpu().numpy().copy() if isinstance(v,torch.Tensor) else v for k,v in result.items()}
                    export_seconds=perf_counter()-export_started
                    processing_started=perf_counter()
                    self.prefetch.release_under_pressure()
                    cpu_pending=submit(timed_call,process_metrics,snapshot,item.listing_ids,item.receipt['listings'])
                    plan.close()
                    metrics,processing_seconds=yield cpu_pending
                    processing_wait_seconds=perf_counter()-processing_started
                    cpu_pending=None
                    for j,i in enumerate(selection):cached[keys[i]]={k:v[j] for k,v in metrics.items()}
                    timings.append(dict(day=day,offset=offset,candidates=len(selection),padded_candidates=len(selection),
                        load_seconds=load_seconds if offset==0 else 0.,host_prefetched=prefetched,rule_seconds=rule_seconds,
                        staging_released_bytes=staging_released_bytes if offset==0 else 0,
                        export_seconds=export_seconds,result_processing_seconds=processing_seconds,
                        result_processing_wait_seconds=processing_wait_seconds,
                        replay_seconds=replay_seconds/len(pending),replay_group_seconds=replay_seconds,
                        scheduler_profile=getattr(prepared.replay,'scheduler_profile',{}),
                        scheduler='shared-streams-v2-independent',pid=os.getpid(),finished_epoch=time(),**preparation_times))
                # Drop graph-owned state before capturing the next pair.
                del result,prepared,plan,metrics,pending,gates
                gc.collect()
                self.emit(day,stage='Batch complete',completed_local_batches=len(timings),completed_seconds=item.clocks-1)
            profile=dict(schema_version=1,source_sha256=c['source_sha256'],pid=os.getpid(),
                day=day,generation=generation,population_sha256=token,
                population_restore_service_seconds=restore_seconds,
                population_restore_wait_seconds=restore_wait_seconds,
                owner_preparation_seconds=prepared_population.seconds if prepared_population is not None else 0.,
                generation_preparation_seconds=generation_population.seconds if generation_population is not None else 0.,
                generation_pack_hits=generation_population.hits if generation_population is not None else 0,
                generation_pack_misses=generation_population.misses if generation_population is not None else 0,
                shared_market_bytes=getattr(getattr(self,'market_block',None),'bytes',0),
                shared_market_capture_seconds=getattr(getattr(self,'market_block',None),'capture_seconds',0.),
                prepublication_wall_seconds=perf_counter()-started,
                timing_semantics='Wall seconds; waits include queue and scheduler delay; replay includes host pauses. Overlapping stages must not be summed. Publication excludes profile write. No added CUDA synchronization.')
            cpu_pending=submit(publish_results,cache_path,root,identity,cached,keys,missing,
                day,item.identity,token,c.get('split','training'),asdict(execution),
                item.listing_ids.tolist(),timings,profile)
            selected=yield cpu_pending
            cpu_pending=None
            if resident:self.resident_results=(identity,selected)
            self.emit(day,stage='Session complete',completed_local_batches=len(timings),completed_seconds=item.clocks-1)
            return dict(day=day,timings=timings,wall_seconds=perf_counter()-started,pid=os.getpid(),clocks=item.clocks-1)
        except BaseException as error:
            self.emit(day,stage='Failed',error=str(error)); raise
        finally:
            if cpu_pending is not None:
                cpu_pending.cancel()
                if not cpu_pending.cancelled():
                    try:cpu_pending.result()
                    except BaseException:pass
            for future in packing.values():future.cancel()
            if not c.get('retain_session_residency'):
                self.market_block=None
                item.resident_features.deactivate(); item.deactivate()


def replay_groups(groups,progress=None):
    """No arithmetic fusion, padding, clock barrier or cross-session state."""
    graphs=[g for group in groups for g in group]
    if not 1<=len(graphs)<=4:raise ValueError('At most four independent replay graphs')
    streams=[torch.cuda.Stream() for _ in graphs]
    for g in graphs:g.reset()
    torch.cuda.synchronize()
    started=perf_counter()
    main=torch.cuda.current_stream()
    for stream in streams:stream.wait_stream(main)
    counts=[(g.total+g.block-1)//g.block for g in graphs]
    for i in range(max(counts)):
        for stream,g,n in zip(streams,graphs,counts):
            if i<n:
                with torch.cuda.stream(stream):g.graph.replay()
        if progress is not None and ((i+1)%64==0 or i+1==max(counts)):
            for stream in streams:main.wait_stream(stream)
            main.synchronize(); progress(i+1)
    for stream in streams:main.wait_stream(stream)
    main.synchronize()
    return perf_counter()-started


class ReplayTask:
    """One session's graphs, advanced only at event-confirmed safe boundaries.

    No arithmetic or chronological ordering changes. The service may prepare,
    export or resume CPU continuations only after *all* queued quanta finish.
    """
    def __init__(self, graphs):
        if not 1<=len(graphs)<=2:raise ValueError('At most two graphs per session')
        self.graphs=graphs
        self.streams=[torch.cuda.Stream() for _ in graphs]
        self.events=[torch.cuda.Event() for _ in graphs]
        self.counts=[(g.total+g.block-1)//g.block for g in graphs]
        self.completed=0;self.queued=0;self.inflight=False;self.started=perf_counter()
        self.enqueue_seconds=0.;self.quanta=0
        self.market=getattr(graphs[0],'market_block',None)
        if self.market is not None:
            if any(getattr(g,'market_block',None) is not self.market or g.block!=self.market.block for g in graphs):
                raise ValueError('Replay cohorts must share the same market block')
            self.market.reset()
        for g in graphs:g.reset()
        main=torch.cuda.current_stream()
        for stream in self.streams:stream.wait_stream(main)

    @property
    def done(self):return self.completed>=max(self.counts)

    def poll(self):
        if self.inflight and all(event.query() for event in self.events):
            self.completed=self.queued;self.inflight=False
        return not self.inflight

    def enqueue(self, quantum=16):
        if self.inflight or self.done:raise RuntimeError('Replay task is not ready')
        began=perf_counter()
        if self.market is not None:
            quantum=1;self.market.enqueue(self.streams)
        end=min(self.completed+quantum,max(self.counts))
        for i in range(self.completed,end):
            for stream,g,n in zip(self.streams,self.graphs,self.counts):
                if i<n:
                    with torch.cuda.stream(stream):g.graph.replay()
        for stream,event in zip(self.streams,self.events):event.record(stream)
        self.queued=end;self.inflight=True
        self.enqueue_seconds+=perf_counter()-began;self.quanta+=1

    def drain(self):
        if self.inflight:
            for event in self.events:event.synchronize()
            self.completed=self.queued;self.inflight=False


class SharedReplayService:
    """Bounded command queue; all CUDA ownership stays on one service thread."""
    def __init__(self):
        self.commands=queue.Queue(maxsize=16); self.workers={}; self.error=None
        self.thread=threading.Thread(target=self._serve,name='v32-shared-gpu',daemon=False)
        self.thread.start()

    def call(self,key,method,*args):
        if self.error:raise RuntimeError('Shared GPU service failed') from self.error
        future=Future(); self.commands.put((key,method,args,future))
        while True:
            try:return future.result(timeout=.2)
            except TimeoutError:
                if future.done():raise
                if self.error:raise RuntimeError('Shared GPU service failed') from self.error
                if not self.thread.is_alive():raise RuntimeError('Shared GPU service exited')

    def factory(self,config,events,lane):
        return Proxy(self,config,events,lane)

    def _serve(self):
        active=[];closing=False;running=set()
        def accept(command):
            nonlocal closing
            key,method,args,future=command
            try:
                if method=='shutdown':
                    if running:raise ValueError('Cannot shut down active sessions')
                    closing=True;future.set_result(None);return
                if method=='create':
                    if len(self.workers)>=2 or key in self.workers:raise ValueError('Two distinct shared sessions maximum')
                    config,events,lane=args
                    if config['device']!='cuda' or config['backend']!='compile' or config['batch_size']!=512:
                        raise ValueError('Qualified shared execution requires CUDA compile and batch 512')
                    self.workers[key]=StreamWorker(config,events,lane);result=None
                elif method=='run':
                    if len(running)>=2 or key in running:raise ValueError('Duplicate or excessive active session')
                    running.add(key);active.append((key,self.workers[key].steps(*args),future,None));return
                elif method=='close':
                    if key in running:raise ValueError('Cannot close an active replay')
                    result=self.workers.pop(key).close()
                elif method=='release_phase':
                    if key in running:raise ValueError('Cannot release phase buffers during an active replay')
                    result=self.workers[key].release_phase()
                else:result=getattr(self.workers[key],method)(*args)
                future.set_result(result)
            except BaseException as error:future.set_exception(error)
        try:
            while not closing:
                replays=[stage for _,_,_,stage in active if isinstance(stage,ReplayTask)]
                # All potentially CUDA-using continuations and commands wait for
                # event-confirmed quiescence, never for the entire session.
                settled=[task.poll() for task in replays]
                # Host-only telemetry/admission must not wait for simultaneous
                # stream quiescence. Events already confirm these clock counts.
                for key,_,_,stage in active:
                    if isinstance(stage,ReplayTask) and stage.completed-getattr(stage,'reported',-64)>=64:
                        worker=self.workers[key]
                        if psutil.virtual_memory().available<worker.config.get('host_reserve_gib',128)*2**30:
                            raise MemoryError('Shared replay host reserve reached')
                        worker.emit(worker.current_day,stage='Replay positions',
                            completed_seconds=min(stage.completed*stage.graphs[0].block,stage.graphs[0].total),
                            total_seconds=stage.graphs[0].total)
                        stage.reported=stage.completed
                quiescent=all(settled)
                if not quiescent:
                    # Independently refill ready streams; do not impose a clock
                    # barrier between sessions. Drain only when host-side work
                    # needs a safe capture/export boundary.
                    host_ready=(not self.commands.empty() or any(task.done for task in replays)
                        or any(isinstance(stage,Future) and stage.done() for _,_,_,stage in active))
                    if not host_ready:
                        for task,ready in zip(replays,settled):
                            if ready:task.enqueue()
                    sleep(.001)
                    continue
                commands=[]
                # A ready replay must not pay an artificial queue timeout at
                # every block boundary. Blocking is only useful when idle.
                try:commands.append(self.commands.get_nowait() if active else self.commands.get(timeout=.2))
                except queue.Empty:pass
                while not self.commands.empty():commands.append(self.commands.get_nowait())
                for command in commands:accept(command)
                ready=[]
                for key,steps,future,stage in active:
                    try:
                        if isinstance(stage,ReplayTask):
                            if not stage.done:
                                ready.append((key,steps,future,stage));continue
                            for graph in stage.graphs:
                                graph.scheduler_profile=dict(host_enqueue_seconds=stage.enqueue_seconds,quanta=stage.quanta)
                            stage=perf_counter()-stage.started
                        if isinstance(stage,Future):
                            if not stage.done():
                                ready.append((key,steps,future,stage));continue
                            try:value=stage.result()
                            except BaseException as error:stage=steps.throw(error)
                            else:stage=steps.send(value)
                        else:stage=steps.send(stage)
                        if not isinstance(stage,Future):stage=ReplayTask(stage)
                        ready.append((key,steps,future,stage))
                    except StopIteration as done:running.remove(key);future.set_result(done.value)
                    except BaseException as error:running.remove(key);future.set_exception(error)
                    while not self.commands.empty():accept(self.commands.get_nowait())
                active=ready
                replays=[stage for _,_,_,stage in active if isinstance(stage,ReplayTask)]
                if replays:
                    if sum(len(task.graphs) for task in replays)>4:raise ValueError('At most four independent replay graphs')
                    reserve=max(self.workers[k].config.get('host_reserve_gib',128) for k,_,_,_ in active)
                    if psutil.virtual_memory().available<reserve*2**30:raise MemoryError('Shared replay host reserve reached')
                    for key,_,_,stage in active:
                        if isinstance(stage,ReplayTask):
                            worker=self.workers[key]
                            worker.prefetch.release_under_pressure()
                            stage.enqueue()
                elif active:
                    # CPU/admission futures alone must not spin a whole core.
                    sleep(.001)
                # Do not keep the preceding completed replay alive through
                # function-local aliases while the service is idle at a barrier.
                task=None;graph=None;stage=None;value=None
        except BaseException as error:
            self.error=error
            for _,_,_,stage in active:
                if isinstance(stage,ReplayTask):stage.drain()
            for _,steps,future,_ in active:
                steps.close()
                if not future.done():future.set_exception(error)
            while not self.commands.empty():
                _,_,_,future=self.commands.get_nowait();future.set_exception(error)
        finally:
            for worker in self.workers.values():worker.close()
            self.workers.clear()

    def close(self):
        if self.thread.is_alive() and not self.error:self.call(None,'shutdown')
        self.thread.join()
        if self.error:raise RuntimeError('Shared GPU service failed') from self.error


class Proxy:
    def __init__(self,service,config,events,lane):
        self.service=service;self.key=(str(Path(config['output']).resolve()),lane)
        self.resident=config.get('retain_session_residency',False)
        self.loaded_metadata=None
        service.call(self.key,'create',config,events,lane)
    def load(self,day):
        # Locals repeatedly request the same immutable identity/listing metadata.
        # Do not put that read behind another session's entire GPU replay. Only
        # the CUDA owner retains/operates the actual session tensors.
        if self.resident and self.loaded_metadata is not None and self.loaded_metadata[0]==day:
            return self.loaded_metadata[1]
        item=self.service.call(self.key,'load',day)
        if self.resident:
            from types import SimpleNamespace
            metadata=SimpleNamespace(identity=dict(item.identity),listing_ids=item.listing_ids.copy())
            self.loaded_metadata=(day,metadata)
            return metadata
        return item
    def run(self,*args):return self.service.call(self.key,'run',*args)
    def close(self):
        try:return self.service.call(self.key,'close')
        finally:self.loaded_metadata=None
