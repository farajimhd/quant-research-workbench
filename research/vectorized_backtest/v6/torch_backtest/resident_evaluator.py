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
import torch
from .sparse_evaluator import SparseSessionEvaluator,seal_batch,seal_session
from .sparse_replay import SparseInputs
from .compact_runner import CompactProgramRunner
from .genome import StrategySpace
from .training_pass import population_hash
from .runtime import require_runtime,write_json,file_hash
from .financial_audit import audit_fills
from .captured_rules import SharedRuleBatch


class ResidentSessionEvaluator(SparseSessionEvaluator):
    def __init__(self,*args,**kwargs):
        backend=kwargs.pop('backend','compiled_graph')
        if backend not in ('cudagraph','compiled_graph'):raise ValueError('Resident evaluator requires captured execution')
        super().__init__(*args,backend='compile',**kwargs)
        if self.device.type!='cuda':raise ValueError('Resident capture requires CUDA')
        self.backend=backend
        self._resident_inputs={};self._resident_runners={};self._cohort_identity=None

    def close(self):
        """Release this evaluator's retained generation buffers explicitly."""
        torch.cuda.synchronize(self.device)
        self._resident_runners.clear();self._resident_inputs.clear();self._cohort_identity=None
        gc.collect()

    def contract(self,training,workers):
        if type(workers) is not int or not 1<=workers<=30:raise ValueError('Resident concurrency must be within the training-session count')
        result=super().contract(training,workers)
        result['capture_barrier']='all resident graphs prepared before concurrent replay'
        return result

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

    def prepare_pass(self,training,population,output,*,workers=2):
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
        for session in remaining:
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
        working=(len(remaining)*self.maximum_state_gib+workers*2.5+4)*1024**3
        if self._cohort_identity is None and residency+working>free*.75:
            raise MemoryError('Resident cohort inputs plus replay/rule envelopes exceed GPU headroom')
        resident_inputs=self._resident_inputs;runners=self._resident_runners;load_started=perf_counter();loads=0
        for session in remaining:
            day=session['day']
            if day in resident_inputs:continue
            write_json(output/'resident-status.json',dict(stage='Loading immutable cohort inputs once',day=day,loaded=len(resident_inputs),total=len(remaining),validation_opened=False))
            item=SparseInputs(self.inputs/day,device=self.device,maximum_gib=self.maximum_input_gib)
            begin,end=[datetime.fromisoformat(session[k]).timestamp() for k in ('start','end')]
            if item.receipt['identity']['session']!=session or len(item.arrays['clocks'])!=int(end-begin):
                raise ValueError('Resident full-session contract changed')
            resident_inputs[day]=item
            loads+=1
        self._cohort_identity=identity
        write_json(output/'input-residency.json',dict(load_seconds=perf_counter()-load_started,session_loads=loads,resident_sessions=len(resident_inputs),
            candidate_batches=(len(population)+self.batch_size-1)//self.batch_size,validation_opened=False))
        for offset in range(0,len(population),self.batch_size):
            members=population[offset:offset+self.batch_size]
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
                        union=sorted(set(int(v) for v in inputs[day].arrays['top_indices'].ravel() if v>=0))
                        write_json(output/'resident-status.json',dict(stage='Evaluate causal lifecycle rules',day=day,candidate_offset=offset,population=len(population),validation_opened=False))
                        gates,rule_seconds=inputs[day].compile(members,listing_ids=union,backend='cudagraph',prepared=shared)
                        broker_started=perf_counter()
                        runner=runners.get(day)
                        reused=runner is not None and runner.b==len(members) and runner.execution_key==CompactProgramRunner.specialization_key(members,space)
                        if reused:
                            runner.set_sparse_population(members,gates)
                        else:
                            write_json(output/'resident-status.json',dict(stage='Build and capture financial broker',day=day,candidate_offset=offset,population=len(population),validation_opened=False))
                            # Drop incompatible captures before allocating replacement state.
                            runners.pop(day,None);runner=None;gc.collect()
                            runner=CompactProgramRunner(inputs[day],space,members,gates,structure=self.structures/day,
                                holding_capacity=self.holding_capacity,backend=self.backend,maximum_fills=self.maximum_fills,
                                maximum_state_gib=self.maximum_state_gib)
                            torch.cuda.synchronize(self.device);runner.compile()
                            runners[day]=runner
                        # No worker runs until this entire serial preparation loop finishes.
                        runner.preparation_reused=reused
                        runner.broker_preparation_seconds=perf_counter()-broker_started
                        jobs.append((session,folder,runner,rule_seconds,torch.cuda.Stream(device=self.device)))
                    setup=perf_counter()-started_preparation
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
                    # Release graphs, rule gates and account state before the next batch.
                    del jobs,futures
                    if 'runner' in locals():del runner,gates
                    gc.collect()
                    del inputs
            finally:
                shared.close()
        for session in remaining:
            day=session['day']
            seal_session(session,token,len(population),output/day,parts[day],batches[day],self.inputs,self.structures,self.holding_capacity)
        write_json(output/'resident-measurements.json',measurements)
        return measurements
