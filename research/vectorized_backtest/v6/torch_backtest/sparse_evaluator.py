"""Independent full-session sparse accounts and durable audited batch receipts.

Eager/compiled replay may use one stream per concurrent session. CUDA graph
capture is deliberately excluded here until a global capture barrier is
qualified; the separate single-owner profiler measures captured variants.
"""
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
import json
import torch
from .sparse_replay import SparseInputs
from .compact_runner import CompactProgramRunner
from .runtime import require_runtime,write_json,file_hash
from .genome import StrategySpace
from .training_pass import population_hash
from .financial_audit import audit_fills
from .inactivity import area
from .run_search import seal_ledger,clean


def seal_batch(runner,result,session,population_token,offset,folder,rule_seconds):
    """One financial receipt contract for streamed and resident execution."""
    metrics={k:clean(v) for k,v in result.items() if isinstance(v,torch.Tensor) or k in ('closed_position_duration_samples','closed_position_pnl_samples')}
    counts=runner.fill_count.detach().cpu()
    ledger=runner.ledger[:,:int(counts.max())].detach().cpu()
    ledger_hash=seal_ledger(folder/'fills.pt',ledger,counts)
    audit_fills(folder/'fills.pt',metrics)
    begin,end=[datetime.fromisoformat(session[k]).timestamp() for k in ('start','end')]
    inactivity=[]
    for lane,count in enumerate(counts.tolist()):
        fills=ledger[lane,:count];entries=sorted(set(fills[fills[:,3]==1,0].tolist()))
        if any(t<begin or t>end for t in entries):raise ValueError('Entry outside full session boundary')
        boundaries=[begin,*entries,end]
        inactivity.append(sum(area(b-a) for a,b in zip(boundaries,boundaries[1:]))/(end-begin))
    metrics['inactivity_fraction']=inactivity
    record=dict(day=session['day'],population_sha256=population_token,candidate_indices=list(range(offset,offset+runner.b)),metrics=metrics,
        ledger_sha256=ledger_hash,full_session=True,validation_opened=False,financial_audit_passed=True,rule_seconds=rule_seconds,
        input_receipt_sha256=file_hash(runner.inputs.root/'complete.json'),
        structural_receipt_sha256=runner.tape.provenance['structural_receipt_sha256'],
        broker='compact',holding_capacity=runner.n)
    write_json(folder/'receipt.json',record)
    return record


def seal_session(session,population_token,population_size,destination,parts,batches,inputs,structures,holding_capacity):
    keys=set(parts[0])
    if any(set(part)!=keys for part in parts):raise ValueError('Candidate batch metric schema changed')
    merged={k:sum((part[k] for part in parts),[]) for k in keys}
    record=dict(day=session['day'],population_sha256=population_token,candidate_indices=list(range(population_size)),full_session=True,
        validation_opened=False,broker='compact',holding_capacity=holding_capacity,metrics=merged,batch_receipts=batches,
        input_receipt_sha256=file_hash(inputs/session['day']/'complete.json'),
        structural_receipt_sha256=file_hash(structures/session['day']/'complete.json'),financial_audit_passed=True)
    write_json(destination/'receipt.json',record)
    return record


class SparseSessionEvaluator:
    def __init__(self,inputs,structures,*,batch_size=128,device='cuda',backend='compile',maximum_input_gib=4.,maximum_state_gib=4.,maximum_fills=16384,holding_capacity=40):
        if backend not in ('eager','compile'):raise ValueError('Concurrent sparse evaluator supports eager/compile; capture concurrency is not qualified')
        if type(batch_size) is not int or not 1<=batch_size<=1024:raise ValueError('Invalid bounded candidate batch')
        self.inputs=Path(inputs);self.structures=Path(structures);self.batch_size=batch_size
        self.device=torch.device(device);self.backend=backend;self.maximum_input_gib=maximum_input_gib
        self.maximum_state_gib=maximum_state_gib;self.maximum_fills=maximum_fills
        if type(holding_capacity) is not int or holding_capacity<1:raise ValueError('Invalid holding capacity')
        self.holding_capacity=holding_capacity

    def contract(self,training,workers,*,memory_required_gib=None):
        if self.device.type=='cuda':
            free,_=torch.cuda.mem_get_info(self.device)
            # Include rule workspace and sidecar headroom, beyond broker/input
            # declarations. Reject the proposed budget rather than overcommit.
            required=(workers*(self.maximum_input_gib+self.maximum_state_gib+2.5) if memory_required_gib is None else memory_required_gib)*1024**3
            if required>free*.75:raise MemoryError('Concurrent session envelopes exceed free GPU headroom; choose measured smaller envelopes/concurrency')
        return dict(backend=self.backend,batch_size=self.batch_size,device=str(self.device),maximum_input_gib=self.maximum_input_gib,
            maximum_state_gib=self.maximum_state_gib,maximum_fills=self.maximum_fills,broker='compact',holding_capacity=self.holding_capacity,
            inputs={s['day']:file_hash(self.inputs/s['day']/'complete.json') for s in training},
            structures={s['day']:file_hash(self.structures/s['day']/'complete.json') for s in training})

    def __call__(self,session,population,destination):
        day=session['day'];destination=require_runtime(destination)
        if (destination/'receipt.json').exists():
            record=json.loads((destination/'receipt.json').read_text())
            if (record.get('population_sha256')!=population_hash(population) or record.get('day')!=day
                or record.get('input_receipt_sha256')!=file_hash(self.inputs/day/'complete.json')
                or record.get('structural_receipt_sha256')!=file_hash(self.structures/day/'complete.json')
                or record.get('broker')!='compact' or record.get('holding_capacity')!=self.holding_capacity
                or record.get('validation_opened',True) or not record.get('full_session')):raise ValueError('Completed session resume contract changed')
            for batch in record['batch_receipts']:
                folder=destination/batch['directory']
                if folder.resolve().parent!=destination.resolve() or file_hash(folder/'receipt.json')!=batch['sha256']:raise ValueError('Resumed batch receipt changed')
                saved=json.loads((folder/'receipt.json').read_text())
                if file_hash(folder/'fills.pt')!=saved['ledger_sha256']:raise ValueError('Resumed fill ledger changed')
                audit_fills(folder/'fills.pt',saved['metrics'])
            return record
        stream=torch.cuda.Stream(device=self.device) if self.device.type=='cuda' else None
        context=torch.cuda.stream(stream) if stream is not None else nullcontext()
        with context:
            inputs=SparseInputs(self.inputs/day,device=self.device,maximum_gib=self.maximum_input_gib)
            if inputs.receipt['identity']['session']!=session:raise ValueError('Full session contract changed')
            union=sorted(set(int(v) for v in inputs.arrays['top_indices'].ravel() if v>=0))
            token=population_hash(population);parts=[];batches=[]
            begin,end=[datetime.fromisoformat(session[k]).timestamp() for k in ('start','end')]
            if end<=begin or len(inputs.arrays['clocks'])!=int(end-begin):raise ValueError('Sparse input does not cover the full session')
            space=StrategySpace()
            for offset in range(0,len(population),self.batch_size):
                members=population[offset:offset+self.batch_size]
                folder=require_runtime(destination/f'batch-{offset:06d}')
                gates,rule_seconds=inputs.compile(members,listing_ids=union)
                runner=CompactProgramRunner(inputs,space,members,gates,structure=self.structures/day,holding_capacity=self.holding_capacity,
                    backend=self.backend,maximum_state_gib=self.maximum_state_gib,maximum_fills=self.maximum_fills)
                runner.compile();result=runner.run()
                if stream is not None:stream.synchronize()
                record=seal_batch(runner,result,session,token,offset,folder,rule_seconds)
                batches.append(dict(directory=folder.name,sha256=file_hash(folder/'receipt.json')));parts.append(record['metrics'])
                del runner,gates
            return seal_session(session,token,len(population),destination,parts,batches,self.inputs,self.structures,self.holding_capacity)
