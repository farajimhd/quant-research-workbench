"""Unchanged causal input/plan; direct labels gathered without future tokenization."""
import numpy as np
import torch
import math
from ..phase2_v2.tokenizer import SessionTokens
from ..phase2_v2.train import source_batches
from ..v5.pretraining_v3 import local_references
from .targets import from_raw


class BarSessionTokens(SessionTokens):
    requires_target_embeddings=False
    cache_source_references=True

    def __init__(self,reader,teacher,teacher_sha,*,device='cpu',precision='fp32',memory_mib=512,token_batch=256):
        n=reader.receipt['views'][0]['rows']
        reserve=math.ceil((n*57+512)/1024**2)
        if memory_mib<=reserve:raise MemoryError('Insufficient total budget for exact reference cache')
        super().__init__(reader,teacher,teacher_sha,device=device,precision=precision,
            memory_mib=memory_mib-reserve,token_batch=token_batch)
        self._source_references=torch.empty(n,7,device=self.device,dtype=torch.float64)
        self._reference_ready=np.zeros(n,dtype=bool)
        self._reference_binding=self.identity
        self._label_offsets=torch.arange(63,-1,-1,device=self.device)
        self.consumer_buffers=(self._source_references,self._label_offsets,self.reference_indices)
        self.reference_cache_misses=0

    def store_source_references(self,selected,ids,price,quantity):
        self._source_references[ids]=torch.cat((price.reshape(-1,1),quantity),1)
        self._reference_ready[selected]=True

    @torch.no_grad()
    def prepare(self,plan):
        super().prepare(plan)
        if self._reference_binding!=self.identity:raise ValueError('Foreign source reference cache')
        ends=np.unique(np.concatenate((plan.endpoints[0][:,-1],plan.targets)))
        if len(ends) and (ends[0]<63 or ends[-1]>=len(self._reference_ready)):
            raise ValueError('Reference endpoint outside immutable source')
        missing=ends[~self._reference_ready[ends]]
        values,valid,_=self.pools[0]
        for start in range(0,len(missing),self.token_batch):
            selected=missing[start:start+self.token_batch];ids=torch.as_tensor(selected.copy(),device=self.device)
            gather=ids[:,None]-self._label_offsets
            price,quantity=local_references(values[gather],valid[gather].bool(),
                _indices=(self.reference_indices[0],self.reference_indices[2]))
            self.store_source_references(selected,ids,price,quantity)
            self.reference_cache_misses+=len(selected)

    @torch.no_grad()
    def batch(self,plan,start,stop):
        if self._reference_binding!=self.identity:raise ValueError('Foreign source reference cache')
        if (not self._reference_ready[plan.targets[start:stop]].all()
            or not self._reference_ready[plan.endpoints[0][start:stop,-1]].all()):
            raise ValueError('Missing exact source reference cache entries')
        token,metadata,indices=self.context_batch(plan,start,stop)
        values,valid,_=self.pools[0]
        future=indices[:,16,None]-self._label_offsets
        # Latest1s window (row7) ends exactly at the origin, never at a future close.
        target=from_raw(values[future],valid[future].bool(),_indices=self.reference_indices,
            _future_reference=self._source_references[indices[:,16]],
            _origin_reference=self._source_references[indices[:,7]])
        return token,metadata,target


def target_size(target):return len(target['bars'])


def batches(units,split,teacher,teacher_sha,args,*,cursor=(0,0,0),report=None,reconstruction=False):
    if reconstruction:raise ValueError('Direct bar targets already include raw audit evidence')
    source=source_batches(units,split,teacher,teacher_sha,args,cursor=cursor,report=report,session_type=BarSessionTokens)
    chunks=[];count=0;last=None
    def packed():
        token,meta=(torch.cat([c[i] for c in chunks]) for i in range(2))
        target={k:torch.cat([c[2][k] for c in chunks]) for k in chunks[0][2]}
        return token,meta,target,last,np.concatenate([c[3] for c in chunks]),np.concatenate([c[4] for c in chunks])
    try:
        for token,meta,target,next_cursor,origins,liquid in source:
            if not getattr(args,'pack_batches',True):
                yield token,meta,target,next_cursor,origins,liquid
                continue
            begin=0;n=target_size(target)
            while begin<n:
                take=min(args.batch_size-count,n-begin);end=begin+take
                chunks.append((token[begin:end],meta[begin:end],{k:v[begin:end] for k,v in target.items()},
                    origins[begin:end],np.full(take,liquid,dtype=np.int8)))
                last=(*next_cursor[:2],next_cursor[2]-n+end);count+=take;begin=end
                if count==args.batch_size:
                    yield packed();chunks=[];count=0
        if count:yield packed()
    finally:source.close()
