"""Persistent, process-isolated rule/replay paths for independent sessions.

Each lane owns its CUDA context, graph buffers, pageable session cache and
bounded upload arena. Progress is coalesced in memory; only the controller's
publication thread writes the terminal snapshot. No validation dates enter here.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import gc,json
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
from .io_pipeline import Publisher, TransferStaging, prepare_host
from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches, require_runtime

_worker=None


class Worker:
    def __init__(self,config,events,lane):
        self.config=config;self.events=events;self.lane=lane;self.host={}
        self.staging=TransferStaging();self.loader=ThreadPoolExecutor(max_workers=1)
        self.pending=None
        torch.set_num_threads(1)
        if config['backend']=='compile':
            configure_caches(require_runtime(Path(config['output'])/'cache'/f'lane-{lane}'),recompile_limit=128)

    def emit(self,day,**values):
        event=dict(day=day,lane=self.lane,pid=os.getpid(),updated_epoch=time(),**values)
        # Queue is bounded. Progress snapshots may be coalesced, never receipts.
        try:self.events.put_nowait(event)
        except queue.Full:pass

    def load(self,day):
        c=self.config
        if day not in self.host:
            self.host[day]=prepare_host(SessionData(Path(c['inputs'])/day,Path(c['history'])/day,
                device='cpu',maximum_gib=c['maximum_data_gib'],
                feature_cache=Path(c['feature_cache'])/day if c['feature_cache'] else None),
                resident=torch.device(c['device']).type=='cuda')
        return self.host[day]

    def run(self,day,next_day,payload,generation,token,replay_width):
        c=self.config;started=perf_counter();self.emit(day,stage='Load session',candidate_offset=0)
        item=self.pending.result() if self.pending is not None else self.loader.submit(self.load,day).result()
        self.pending=self.loader.submit(self.load,next_day) if next_day else None
        device=torch.device(c['device']);population=[Individual.restore(v) for v in payload]
        root=require_runtime(Path(c['output'])/f'generation-{generation:04d}'/day)
        parts=[];timings=[];execution=Execution(**c['execution'])
        try:
            if device.type=='cuda':
                required=item.bytes+item.resident_features.bytes+(2*c['maximum_gate_gib']+2+c['capture_gib'])*1024**3
                if required>c['lane_memory_bytes']:raise MemoryError('Independent session exceeds admitted GPU memory budget')
            self.staging.activate([(day,item)],device)
            load_seconds=perf_counter()-started
            with Publisher() as publisher:
                for offset in range(0,len(population),c['batch_size']):
                    members=population[offset:offset+c['batch_size']]
                    plan=PopulationPrograms(members,device)
                    batch_start=perf_counter();rule_start=time()
                    self.emit(day,stage='Evaluate rules',candidate_offset=offset,rule_tiles=0,total_rule_tiles=0,
                              completed_seconds=0,total_seconds=item.clocks-1,replay_started=None)
                    try:
                        if device.type=='cuda':
                            from .rule_plan import RulePlan
                            plan.plan=RulePlan(members,device,maximum_capture_gib=c['capture_gib'])
                        gates=plan.evaluate(item,maximum_gate_gib=c['maximum_gate_gib'],
                            progress=lambda e:self.emit(day,stage='Evaluate rules',candidate_offset=offset,
                                rule_tiles=e['tiles'],total_rule_tiles=e['total_tiles']))
                        if device.type=='cuda':torch.cuda.synchronize(device)
                        rule_seconds=perf_counter()-batch_start;replay_start=time();replay_clock=perf_counter()
                        self.emit(day,stage='Replay positions',candidate_offset=offset,replay_started=replay_start)
                        result=replay_cohort([item],members,[gates],execution=execution,backend=c['backend'],listing_width=replay_width,
                            progress=lambda done,total:self.emit(day,stage='Replay positions',candidate_offset=offset,
                                completed_seconds=done,total_seconds=total,replay_started=replay_start))[0]
                        metrics={k:v.detach().cpu().tolist() for k,v in result.items()}
                        parts.append(metrics)
                        timing=dict(day=day,offset=offset,candidates=len(members),load_seconds=load_seconds if offset==0 else 0.,
                            rule_seconds=rule_seconds,replay_seconds=perf_counter()-replay_clock,
                            rule_started_epoch=rule_start,replay_started_epoch=replay_start,finished_epoch=time(),pid=os.getpid(),replay_listing_width=replay_width)
                        if plan.plan is not None:
                            timing.update(capture_builds=plan.plan.builds,capture_hits=plan.plan.hits,capture_bytes=plan.plan.capture_bytes,
                                capture_evictions=plan.plan.evictions,capture_seconds=plan.plan.capture_seconds)
                        timings.append(timing)
                        publisher.write(root/f'batch-{offset:06d}.json',dict(day=day,offset=offset,candidates=len(members),
                            population_sha256=token,metrics=metrics,full_session=True,validation_opened=False))
                        del gates,result
                    finally:plan.close()
                    publisher.flush()
                    self.emit(day,stage='Batch published',candidate_offset=offset,
                              completed_local_batches=len(parts),completed_seconds=item.clocks-1)
                merged={k:sum((part[k] for part in parts),[]) for k in parts[0]}
                publisher.write(root/'receipt.json',dict(day=day,metrics=merged,identity=item.identity,
                    population_sha256=token,full_session=True,validation_opened=False,execution=asdict(execution)))
                publisher.write(root/'timings.json',timings)
            self.emit(day,stage='Session complete',completed_local_batches=len(parts),completed_seconds=item.clocks-1)
            return dict(day=day,timings=timings,wall_seconds=perf_counter()-started,pid=os.getpid(),clocks=item.clocks-1)
        except BaseException as error:
            self.emit(day,stage='Failed',error=str(error));raise
        finally:
            if hasattr(item,'resident_features'):item.resident_features.deactivate()
            item.deactivate();gc.collect()
            if device.type=='cuda':torch.cuda.empty_cache()

    def close(self):
        self.loader.shutdown(wait=True,cancel_futures=True);self.staging.close()
        for item in self.host.values():item.close()
        self.host.clear()


def initialize(config,events,lane):
    global _worker
    _worker=Worker(config,events,lane)


def evaluate(*args):return _worker.run(*args)
def cleanup():_worker.close()


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
                self.panel.session_progress(event)
        except BaseException as error:self.error=error

    def __enter__(self):
        c=dict(self.config)
        # Preserve the measured RulePlan budget per execution path. Splitting
        # one plan's cache between workers forces repeated graph recaptures.
        # Admission below still bounds the combined resident allocation.
        c['capture_gib']=4.
        c['lane_memory_bytes']=torch.cuda.mem_get_info(c['device'])[0]*.8/self.workers if torch.device(c['device']).type=='cuda' else 0
        self.pools=[ProcessPoolExecutor(max_workers=1,mp_context=self.context,
            initializer=initialize,initargs=(c,self.events,lane)) for lane in range(self.workers)]
        self.thread=threading.Thread(target=self.pump,name='v7-session-progress',daemon=True);self.thread.start()
        return self

    def generation(self,days,population,generation,token):
        payload=[v.payload() for v in population];timings=[]
        if not self.widths:
            def width(day):return day,len(json.loads((Path(self.config['history'])/day/'complete.json').read_text())['listing_ids'])
            with ThreadPoolExecutor(max_workers=self.workers) as reader:self.widths=dict(reader.map(width,days))
        for first in range(0,len(days),self.workers):
            cohort=days[first:first+self.workers];started=perf_counter()
            self.panel.begin_sessions(cohort)
            futures=[self.pools[lane].submit(evaluate,day,
                days[first+self.workers+lane] if first+self.workers+lane<len(days) else None,
                payload,generation,token,max(self.widths[d] for d in cohort)) for lane,day in enumerate(cohort)]
            results=[future.result() for future in futures]
            if self.error is not None:raise RuntimeError('Session progress consumer failed') from self.error
            for result in results:
                timings.extend(result['timings'])
                self.panel.session_progress(dict(day=result['day'],stage='Session complete',
                    completed_local_batches=len(result['timings']),completed_seconds=result['clocks'],total_seconds=result['clocks']))
            self.panel.complete_sessions(len(cohort),perf_counter()-started)
        return timings

    def __exit__(self,*args):
        errors=[]
        try:
            for pool in self.pools:
                try:pool.submit(cleanup).result()
                except BaseException as error:errors.append(error)
                finally:pool.shutdown(wait=True,cancel_futures=True)
        finally:
            self.stop.set();self.thread.join();self.events.close();self.events.join_thread()
        if errors and args[0] is None:raise RuntimeError('Session worker cleanup failed') from errors[0]
