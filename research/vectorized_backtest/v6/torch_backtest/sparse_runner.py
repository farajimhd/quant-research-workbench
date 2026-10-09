"""Sparse market/rule residency with stable daily-union broker identities.

This is a profiling baseline, not the final top-N-plus-held state allocator.
No [clock, candidate, listing] gate or dense full-session market is allocated.
Structural-target policies require a separately certified raw-level sidecar;
absence fails closed rather than substituting indicator approximations.
"""
from types import SimpleNamespace
import json
from pathlib import Path
import numpy as np
import torch
from .program_runner import ProgramRunner
from .evolution import STAGES
from .timing import TIMING_CONTRACT, timing_fingerprint
from .runtime import file_hash


class SparseProgramRunner(ProgramRunner):
    def __init__(self, inputs, space, individuals, gates, *, structure=None, broker_capacity=None, **kwargs):
        if structure is None and (any(int(v.policy[6]) != 0 for v in individuals) or not kwargs.get('specialize',True)):
            raise ValueError('Sparse profiling baseline requires percentage targets; structural sidecar is not qualified')
        if gates.shape != (len(individuals), len(inputs.arrays['feature_keys'])) or gates.dtype != torch.uint8 or gates.device != inputs.device:
            raise ValueError('Sparse rule identity/shape/device mismatch')
        self.inputs=inputs;self.sparse_gates=gates
        union=np.unique(inputs.arrays['top_indices']);union=union[union>=0]
        if not len(union):raise ValueError('Training session has no ranked eligible identities')
        self.listing_ids=torch.as_tensor(union,device=inputs.device,dtype=torch.int64)
        self.union_ids=union.tolist()
        self.structural=None
        if structure is not None:
            folder=Path(structure);record=json.loads((folder/'complete.json').read_text())
            if (record.get('status')!='complete' or record.get('version')!='v6-sparse-structural-v1' or record.get('validation_opened',True)
                or record.get('input_receipt_sha256')!=file_hash(inputs.root/'complete.json')
                or record.get('market_keys_sha256')!=inputs.receipt['files']['market_keys.npy'] or record.get('listing_ids')!=self.union_ids):
                raise ValueError('Structural sidecar input/identity seal mismatch')
            arrays={}
            for name in ('targets','valid'):
                path=folder/(name+'.npy')
                if file_hash(path)!=record['files'][path.name]:raise ValueError('Structural sidecar bytes changed')
                arrays[name]=np.load(path,mmap_mode='r',allow_pickle=False)
            rows=len(inputs.arrays['market_keys'])
            if arrays['targets'].shape!=(rows,15) or arrays['targets'].dtype!=np.float64 or arrays['valid'].shape!=(rows,) or arrays['valid'].dtype!=np.bool_:
                raise ValueError('Structural sidecar raw-price/clock shape mismatch')
            if np.isnan(arrays['targets']).any():raise ValueError('Structural raw targets contain NaN')
            self.structural={name:inputs._transfer(value) for name,value in arrays.items()}
        clocks=inputs.tensors['clocks'];n=len(union) if broker_capacity is None else broker_capacity
        tape=SimpleNamespace(device=inputs.device,clocks=clocks,
            tickers=tuple(str(i) for i in self.union_ids) if broker_capacity is None else tuple(f'compact-{i}' for i in range(n)),
            admission=torch.full((n,),int(clocks[0]),device=inputs.device,dtype=torch.int64),
            structural_targets=True if self.structural is not None else None,
            provenance=dict(version='v6-sparse-union-profiling-v1',input_identity=inputs.receipt['identity'],
                input_files=inputs.receipt['files'],listing_ids=self.union_ids,
                timing_contract=TIMING_CONTRACT,timing_fingerprint=timing_fingerprint(),
                structural_receipt_sha256=file_hash(Path(structure)/'complete.json') if structure is not None else None))
        tape.validate=lambda:tape
        shape=(len(clocks),len(individuals),n)
        descriptors={s:SimpleNamespace(shape=shape,dtype=torch.bool,device=inputs.device) for s in STAGES}
        super().__init__(tape,space,individuals,descriptors,**kwargs)

    def tick(self):
        clock=self.tape.clocks.index_select(0,self.index.reshape(1)).squeeze(0)
        self.current_market=self.inputs.lookup(self.listing_ids,clock)
        top=self.inputs.tensors['top_indices'].index_select(0,self.index.reshape(1)).squeeze(0)
        self.current_membership=(self.listing_ids[:,None]==top[None]).any(-1)
        super().tick()

    def _row(self,name):
        aliases={'close':'mark','trades':'trade_count'}
        if name in ('structural_clock','structural_targets'):
            if self.structural is None:return torch.zeros_like(self.current_market['observed'])
            row=self.current_market['source_row'];known=row>=0
            if name=='structural_clock':return self.structural['valid'][row.clamp_min(0)]&known&self.current_market['observed']
            return torch.where(known[:,None],self.structural['targets'][row.clamp_min(0)],float('inf'))
        if name in ('macd_line','macd_signal'):
            return torch.full((self.n,4),float('nan'),device=self.tape.device,dtype=torch.float64)
        result=self.current_market[aliases.get(name,name)]
        return result if result.dtype==torch.bool else result.to(torch.float64)

    def _program_gate(self,stage):
        rows=self.current_market['feature_row'];known=rows>=0
        signals=self.sparse_gates.index_select(1,rows.clamp_min(0))
        gate=(signals.bitwise_and(1<<STAGES.index(stage))!=0)&known[None]
        if stage=='entry':gate&=self.current_membership[None]
        return gate
