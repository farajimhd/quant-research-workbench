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
        for offset in range(0,len(population),self.batch_size):
            members=population[offset:offset+self.batch_size]
            shared=SharedRuleBatch(members,self.device)
            try:
                for cursor in range(0,len(remaining),workers):
                    sessions=remaining[cursor:cursor+workers];inputs={}
                    for session in sessions:
                        day=session['day'];inputs[day]=SparseInputs(self.inputs/day,device=self.device,maximum_gib=self.maximum_input_gib)
                        begin,end=[datetime.fromisoformat(session[k]).timestamp() for k in ('start','end')]
                        if inputs[day].receipt['identity']['session']!=session or len(inputs[day].arrays['clocks'])!=int(end-begin):
                            raise ValueError('Resident full-session contract changed')
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
                        gates,rule_seconds=inputs[day].compile(members,listing_ids=union,backend='cudagraph',prepared=shared)
                        runner=CompactProgramRunner(inputs[day],space,members,gates,structure=self.structures/day,
                            holding_capacity=self.holding_capacity,backend=self.backend,maximum_fills=self.maximum_fills,
                            maximum_state_gib=self.maximum_state_gib)
                        # No worker runs until this entire serial preparation loop finishes.
                        torch.cuda.synchronize(self.device);runner.compile()
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
                            captured_preparation_seconds=runner.setup_seconds,clocks=len(runner.tape.clocks))
                    timings=[]
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures=[pool.submit(execute,job) for job in jobs]
                        for future in futures:
                            day,folder,record,timing=future.result();timings.append(timing)
                            parts[day].append(record['metrics']);batches[day].append(dict(directory=folder.name,sha256=file_hash(folder/'receipt.json')))
                    torch.cuda.synchronize(self.device)
                    measurements.append(dict(sessions=[s['day'] for s in sessions],candidate_offset=offset,candidates=len(members),
                        setup_seconds=setup,replay_and_audit_seconds=perf_counter()-started,active=len(jobs),session_timings=timings))
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
