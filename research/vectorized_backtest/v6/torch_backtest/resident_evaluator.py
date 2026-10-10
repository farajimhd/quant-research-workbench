"""Bounded resident CUDA sessions: serial capture barrier, concurrent replay.

Every candidate batch is prepared while no owned replay is active. Only after
all resident session graphs are captured may their independent streams run.
The next batch waits for replay and durable financial receipts to finish.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import perf_counter
import json
import gc
from copy import deepcopy
import numpy as np
import torch
from .sparse_evaluator import SparseSessionEvaluator,seal_batch,seal_session
from .sparse_replay import SparseInputs
from .compact_runner import CompactProgramRunner
from .genome import StrategySpace
from .training_pass import population_hash
from .runtime import require_runtime,write_json,file_hash
from .financial_audit import audit_fills
from .captured_rules import SharedRuleBatch
from .capture_seed import seed_population,family_token
from .run_search import restore


class ResidentSessionEvaluator(SparseSessionEvaluator):
    def __init__(self,*args,**kwargs):
        self.compiler_specialization_budget=kwargs.pop('compiler_specialization_budget',128)
        if type(self.compiler_specialization_budget) is not int or not 1<=self.compiler_specialization_budget<=16384:
            raise ValueError('Bounded compiler specialization budget required')
        self.capture_seed_root=kwargs.pop('capture_seed_root',None)
        self._capture_seeds={}
        backend=kwargs.pop('backend','compiled_graph')
        graph_steps=kwargs.pop('graph_steps',16)
        capture_variants=kwargs.pop('capture_variants',1)
        if type(capture_variants) is not int or not 1<=capture_variants<=2:raise ValueError('Resident capture variants must be 1..2')
        if type(graph_steps) is not int or not 1<=graph_steps<=64:raise ValueError('Resident capture steps must be 1..64')
        if backend not in ('cudagraph','compiled_graph'):raise ValueError('Resident evaluator requires captured execution')
        super().__init__(*args,backend='compile',**kwargs)
        if self.device.type!='cuda':raise ValueError('Resident capture requires CUDA')
        self.backend=backend
        self.graph_steps=graph_steps
        self.capture_variants=capture_variants
        self._resident_inputs={};self._resident_runners={};self._resident_listing_ids={};self._cohort_identity=None

    def close(self):
        """Release this evaluator's retained generation buffers explicitly."""
        torch.cuda.synchronize(self.device)
        self._resident_runners.clear();self._resident_inputs.clear();self._resident_listing_ids.clear();self._cohort_identity=None
        self._capture_seeds.clear()
        gc.collect()

    @staticmethod
    def _listing_union(top_indices):
        listing_ids=np.unique(top_indices)
        return listing_ids[listing_ids>=0].tolist()

    def contract(self,training,workers):
        if type(workers) is not int or not 1<=workers<=30:raise ValueError('Resident concurrency must be within the training-session count')
        # Retained inputs/account buffers are already allocated. Reserve only
        # replacement state, one temporary gate buffer and rule workspaces.
        required=self.maximum_state_gib+10. if self._resident_inputs else None
        result=super().contract(training,workers,memory_required_gib=required)
        result['capture_barrier']='all resident graphs prepared before concurrent replay'
        result['graph_steps']=self.graph_steps
        result['capture_variants']=self.capture_variants
        result['capture_initializer']='exact-capture-initializer-v1'
        result['compiler_specialization_budget']=self.compiler_specialization_budget
        return result

    def _capture_cache(self,day):
        return self._resident_runners.setdefault(day,{})

    def _reserve_capture(self,cache,key):
        """Evict only an exact, least-recently-used variant before replacement."""
        if key not in cache and len(cache)>=self.capture_variants:
            cache.pop(next(iter(cache)))
            gc.collect()

    @staticmethod
    def _touch_capture(cache,key,runner):
        cache.pop(key,None)
        cache[key]=runner

    def _batch_order(self,population,space):
        """Group fixed member blocks by exact shape; never regroup candidates."""
        groups={}
        for offset in range(0,len(population),self.batch_size):
            members=population[offset:offset+self.batch_size]
            key=(len(members),self.graph_steps,CompactProgramRunner.specialization_key(members,space))
            groups.setdefault(key,[]).append(offset)
        def priority(item):
            key,offsets=item
            warm=sum(key in cache for cache in self._resident_runners.values())
            return (-warm,-len(offsets),offsets[0])
        return [offset for _,offsets in sorted(groups.items(),key=priority) for offset in offsets]

    def restore_capture_context(self,training,output,*,workers):
        """Restore finite compilation shape history without evaluating sessions."""
        if self.capture_seed_root is None:return
        order_path=self.capture_seed_root/'order.json'
        if not order_path.exists():return
        if self._resident_runners:raise ValueError('Compiler restoration requires a fresh serial capture barrier')
        order=json.loads(order_path.read_text())
        families=order.get('families',[])
        if (order.get('version')!='compiler-priming-order-v2' or order.get('validation_opened') is not False
                or not isinstance(families,list) or any(type(v) is not str for v in families) or len(set(families))!=len(families)
                or len(families)*len(training)>self.compiler_specialization_budget):
            raise ValueError('Compiler priming history exceeds its exact bounded contract')
        output=require_runtime(output);checkpoint=output/'checkpoint.json'
        completed=json.loads(checkpoint.read_text())['completed_generations'] if checkpoint.exists() else None
        progress=output/f'generation-{completed+1:04d}'/'resident-status.json' if completed is not None else output/'compiler-priming-status.json'
        for index,token in enumerate(families):
            if type(token) is not str or len(token)!=64 or any(c not in '0123456789abcdef' for c in token):
                raise ValueError('Invalid compiler priming family identity')
            record=order['initializers'][token]
            members=[restore(v) for v in record['population']]
            key=(len(members),self.graph_steps,CompactProgramRunner.specialization_key(members,StrategySpace()))
            if family_token(key)!=token or population_hash(members)!=record['population_sha256']:
                raise ValueError('Compiler priming initializer changed')
            seed_population(self.capture_seed_root,key,members)
            write_json(progress,dict(stage=f'Restoring compiler context {index+1}/{len(families)}; no full-session evaluation',
                sessions=[s['day'] for s in training],validation_opened=False))
            self.prepare_pass(training,members,output/'compiler-priming'/f'family-{index:04d}',workers=workers,prime_only=True)
        write_json(output/'compiler-priming'/'complete.json',dict(priming_only=True,selection_allowed=False,full_session=False,
            order_sha256=file_hash(order_path),families=families,validation_opened=False))

    def __call__(self,session,population,destination):
        if not (destination/'receipt.json').exists():raise ValueError('Resident full pass must be prepared before selection')
        return super().__call__(session,population,destination)

    def _resume_batch(self,session,token,indices,folder):
        path=folder/'receipt.json'
        if not path.exists():return None
        record=json.loads(path.read_text())
        expected=dict(day=session['day'],population_sha256=token,candidate_indices=indices,full_session=True,
            validation_opened=False,financial_audit_passed=True,broker='compact',holding_capacity=self.holding_capacity,
            input_receipt_sha256=file_hash(self.inputs/session['day']/'complete.json'),
            structural_receipt_sha256=file_hash(self.structures/session['day']/'complete.json'))
        if any(record.get(k)!=v for k,v in expected.items()):raise ValueError('Resident batch resume contract changed')
        if file_hash(folder/'fills.pt')!=record['ledger_sha256']:raise ValueError('Resident batch ledger changed')
        audit_fills(folder/'fills.pt',record['metrics'])
        return record

    def prepare_pass(self,training,population,output,*,workers=2,prime_only=False):
        if type(prime_only) is not bool:raise ValueError('Explicit Boolean capture priming required')
        self.contract(training,workers)
        token=population_hash(population);space=StrategySpace();measurements=[]
        remaining=[]
        for session in training:
            destination=require_runtime(output/session['day'])
            if (destination/'receipt.json').exists():self(session,population,destination)
            else:remaining.append(session)
        parts={s['day']:[] for s in remaining};batches={s['day']:[] for s in remaining}
        if not remaining:
            write_json(output/'resident-measurements.json',[]);return []
        identity=tuple((s['day'],file_hash(self.inputs/s['day']/'complete.json'),file_hash(self.structures/s['day']/'complete.json')) for s in training)
        if self._cohort_identity is not None and identity!=self._cohort_identity:
            raise ValueError('Retained cohort input/structural identity changed')
        # Inputs are immutable across candidate batches. Reserve a separate
        # bounded cohort envelope before retaining them on device.
        free,_=torch.cuda.mem_get_info(self.device)
        residency=0
        # Budget the complete training cohort even when durable receipts let
        # this pass skip sessions. The next generation may load every session.
        for session in training:
            folder=self.inputs/session['day'];certificate=json.loads((folder/'complete.json').read_text())
            if 'market_rows' not in certificate:
                residency+=self.maximum_input_gib*1024**3
                continue
            # NPY file sizes bound resident arrays (including harmless headers).
            # Broker columns: 13 eight-byte columns and one Boolean. Structural
            # sidecar: 15 float64 targets and one Boolean per source row.
            arrays=sum((folder/(name+'.npy')).stat().st_size for name in
                ('clocks','top_indices','market_keys','feature_keys','features','feature_valid'))
            residency+=arrays+certificate['market_rows']*(105+121)
        working=(len(training)*self.maximum_state_gib*getattr(self,'capture_variants',1)+workers*2.5+4)*1024**3
        if self._cohort_identity is None and residency+working>free*.75:
            raise MemoryError('Resident cohort inputs plus replay/rule envelopes exceed GPU headroom')
        resident_inputs=self._resident_inputs;load_started=perf_counter();loads=0
        for session in remaining:
            day=session['day']
            if day in resident_inputs:continue
            write_json(output/'resident-status.json',dict(stage='Loading immutable cohort inputs once',day=day,loaded=len(resident_inputs),total=len(remaining),validation_opened=False))
            item=SparseInputs(self.inputs/day,device=self.device,maximum_gib=self.maximum_input_gib)
            begin,end=[datetime.fromisoformat(session[k]).timestamp() for k in ('start','end')]
            if item.receipt['identity']['session']!=session or len(item.arrays['clocks'])!=int(end-begin):
                raise ValueError('Resident full-session contract changed')
            resident_inputs[day]=item
            # Membership is immutable across population batches/generations.
            # Native unique avoids repeated Python iteration over every clock.
            self._resident_listing_ids[day]=self._listing_union(item.arrays['top_indices'])
            loads+=1
        self._cohort_identity=identity
        write_json(output/'input-residency.json',dict(load_seconds=perf_counter()-load_started,session_loads=loads,resident_sessions=len(resident_inputs),
            candidate_batches=(len(population)+self.batch_size-1)//self.batch_size,validation_opened=False))
        for offset in self._batch_order(population,space):
            members=population[offset:offset+self.batch_size]
            execution_key=CompactProgramRunner.specialization_key(members,space)
            shared=SharedRuleBatch(members,self.device)
            try:
                for cursor in range(0,len(remaining),workers):
                    sessions=remaining[cursor:cursor+workers]
                    inputs={s['day']:resident_inputs[s['day']] for s in sessions}
                    jobs=[];started_preparation=perf_counter()
                    write_json(output/'resident-status.json',dict(stage='Preparing resident graphs',sessions=[s['day'] for s in sessions],
                        candidate_offset=offset,population=len(population),validation_opened=False))
                    for session in sessions:
                        day=session['day'];folder=require_runtime(output/day/f'batch-{offset:06d}')
                        record=self._resume_batch(session,token,list(range(offset,offset+len(members))),folder)
                        if record is not None:
                            parts[day].append(record['metrics']);batches[day].append(dict(directory=folder.name,sha256=file_hash(folder/'receipt.json')))
                            continue
                        union=self._resident_listing_ids[day]
                        write_json(output/'resident-status.json',dict(stage='Evaluate causal lifecycle rules',day=day,candidate_offset=offset,population=len(population),validation_opened=False))
                        gates,rule_seconds=inputs[day].compile(members,listing_ids=union,backend='cudagraph',prepared=shared)
                        broker_started=perf_counter()
                        cache=self._capture_cache(day)
                        capture_key=(len(members),min(self.graph_steps,len(inputs[day].arrays['clocks'])),execution_key)
                        runner=cache.get(capture_key)
                        reused=runner is not None
                        if reused:
                            runner.set_sparse_population(members,gates)
                        else:
                            write_json(output/'resident-status.json',dict(stage='Build and capture financial broker',day=day,candidate_offset=offset,population=len(population),validation_opened=False))
                            # Evict the least-recent variant only when the bound is full.
                            self._reserve_capture(cache,capture_key);runner=None;gc.collect()
                            if capture_key not in self._capture_seeds:
                                self._capture_seeds[capture_key]=(seed_population(self.capture_seed_root,capture_key,members)
                                    if self.capture_seed_root is not None else deepcopy(members))
                            initializer=self._capture_seeds[capture_key]
                            if CompactProgramRunner.specialization_key(initializer,space)!=execution_key:
                                raise ValueError('Capture initializer execution shape changed')
                            runner=CompactProgramRunner(inputs[day],space,initializer,gates,structure=self.structures/day,
                                holding_capacity=self.holding_capacity,backend=self.backend,maximum_fills=self.maximum_fills,
                                maximum_state_gib=self.maximum_state_gib,graph_steps=self.graph_steps)
                            torch.cuda.synchronize(self.device);runner.compile()
                            runner.set_sparse_population(members,gates)
                        self._touch_capture(cache,capture_key,runner)
                        # No worker runs until this entire serial preparation loop finishes.
                        runner.preparation_reused=reused
                        runner.broker_preparation_seconds=perf_counter()-broker_started
                        jobs.append((session,folder,runner,rule_seconds,torch.cuda.Stream(device=self.device)))
                    setup=perf_counter()-started_preparation
                    if prime_only:
                        torch.cuda.synchronize(self.device)
                        measurements.append(dict(sessions=[s['day'] for s in sessions],candidate_offset=offset,candidates=len(members),
                            setup_seconds=setup,priming_only=True,full_session=False,active=len(jobs)))
                        write_json(output/'resident-measurements.json',measurements)
                        del jobs
                        if 'runner' in locals():del runner,gates
                        del inputs
                        gc.collect()
                        continue
                    torch.cuda.synchronize(self.device);started=perf_counter()
                    write_json(output/'resident-status.json',dict(stage='Concurrent captured replay',sessions=[s['day'] for s in sessions],
                        candidate_offset=offset,population=len(population),active=len(jobs),validation_opened=False))
                    def execute(job):
                        session,folder,runner,rule_seconds,stream=job
                        replay_started=perf_counter()
                        with torch.cuda.stream(stream):
                            result=runner.run();stream.synchronize()
                            replay_seconds=perf_counter()-replay_started
                            audit_started=perf_counter()
                            record=seal_batch(runner,result,session,token,offset,folder,rule_seconds)
                        return session['day'],folder,record,dict(day=session['day'],replay_and_position_report_seconds=replay_seconds,
                            seal_and_financial_audit_seconds=perf_counter()-audit_started,rule_seconds=rule_seconds,
                            captured_preparation_seconds=0. if runner.preparation_reused else runner.setup_seconds,
                            captured_compile_phase_seconds={} if runner.preparation_reused else dict(runner.compile_phase_seconds),
                            captured_preparation_reused=runner.preparation_reused,
                            broker_preparation_seconds=runner.broker_preparation_seconds,clocks=len(runner.tape.clocks))
                    timings=[]
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures=[pool.submit(execute,job) for job in jobs]
                        for future in futures:
                            day,folder,record,timing=future.result();timings.append(timing)
                            parts[day].append(record['metrics']);batches[day].append(dict(directory=folder.name,sha256=file_hash(folder/'receipt.json')))
                    torch.cuda.synchronize(self.device)
                    measurements.append(dict(sessions=[s['day'] for s in sessions],candidate_offset=offset,candidates=len(members),
                        setup_seconds=setup,replay_and_audit_seconds=perf_counter()-started,active=len(jobs),session_timings=timings))
                    write_json(output/'resident-measurements.json',measurements)
                    # Release transient job references; bounded captures remain resident.
                    del jobs,futures
                    if 'runner' in locals():del runner,gates
                    gc.collect()
                    del inputs
            finally:
                shared.close()
        if prime_only:
            write_json(output/'priming.json',dict(priming_only=True,full_session=False,selection_allowed=False,
                population_sha256=token,sessions=[s['day'] for s in remaining],validation_opened=False))
            return measurements
        for session in remaining:
            day=session['day']
            # Scheduling is independent across accounts. Restore the original
            # candidate order before aggregation and parent selection.
            ordered=sorted(zip(batches[day],parts[day]),key=lambda pair:pair[0]['directory'])
            seal_session(session,token,len(population),output/day,[v for _,v in ordered],[b for b,_ in ordered],self.inputs,self.structures,self.holding_capacity)
        write_json(output/'resident-measurements.json',measurements)
        return measurements
