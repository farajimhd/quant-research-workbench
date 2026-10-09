"""Sparse market/rule residency with stable daily-union broker identities.

This is a profiling baseline, not the final top-N-plus-held state allocator.
No [clock, candidate, listing] gate or dense full-session market is allocated.
Structural-target policies require a separately certified raw-level sidecar;
absence fails closed rather than substituting indicator approximations.
"""
from types import SimpleNamespace
import numpy as np
import torch
from .program_runner import ProgramRunner
from .evolution import STAGES
from .timing import TIMING_CONTRACT, timing_fingerprint


class SparseProgramRunner(ProgramRunner):
    def __init__(self, inputs, space, individuals, gates, **kwargs):
        if any(int(v.policy[6]) != 0 for v in individuals):
            raise ValueError('Sparse profiling baseline requires percentage targets; structural sidecar is not qualified')
        if gates.shape != (len(individuals), len(inputs.arrays['feature_keys'])) or gates.dtype != torch.uint8 or gates.device != inputs.device:
            raise ValueError('Sparse rule identity/shape/device mismatch')
        self.inputs=inputs;self.sparse_gates=gates
        union=np.unique(inputs.arrays['top_indices']);union=union[union>=0]
        if not len(union):raise ValueError('Training session has no ranked eligible identities')
        self.listing_ids=torch.as_tensor(union,device=inputs.device,dtype=torch.int64)
        self.union_ids=union.tolist()
        clocks=inputs.tensors['clocks'];n=len(union)
        tape=SimpleNamespace(device=inputs.device,clocks=clocks,
            tickers=tuple(str(i) for i in self.union_ids),
            admission=torch.full((n,),int(clocks[0]),device=inputs.device,dtype=torch.int64),
            structural_targets=None,
            provenance=dict(version='v6-sparse-union-profiling-v1',input_identity=inputs.receipt['identity'],
                input_files=inputs.receipt['files'],listing_ids=self.union_ids,
                timing_contract=TIMING_CONTRACT,timing_fingerprint=timing_fingerprint()))
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
        if name=='structural_clock':return torch.zeros_like(self.current_market['observed'])
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
