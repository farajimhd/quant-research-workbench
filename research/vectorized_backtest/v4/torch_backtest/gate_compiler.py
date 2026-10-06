"""Compile immutable sparse candle features to shared lifecycle decision gates.

No database access, future labels, or per-candidate history copies. Packed bank
rows stay shared on GPU; program intermediates are released per bounded chunk.
"""
from time import perf_counter
import torch
from .feature_bank import CATALOG
from .program import TorchPrograms
from .evolution import STAGES

class FeatureResident:
    def __init__(self,bank,identities,previous=None,*,device='cuda',maximum_gib=24.,start_us=None,end_us=None):
        self.bank=bank;self.device=torch.device(device);self.rows=[];self.bytes=0
        budget=int(maximum_gib*1024**3)
        for identity in identities:
            options=dict(previous=previous)
            if start_us is not None:options['start_us']=start_us
            if end_us is not None:options['end_us']=end_us
            clocks,features,valid=bank.listing(identity,**options)
            size=sum(t.numel()*t.element_size() for t in (clocks,features,valid))
            if self.bytes+size>budget:raise MemoryError('V4 feature residency budget exceeded before device allocation')
            self.bytes+=size
            if self.device.type=='cuda':
                clocks=clocks.pin_memory();features=features.pin_memory();valid=valid.pin_memory()
            self.rows.append((clocks.to(self.device,non_blocking=True),features.to(self.device,non_blocking=True),valid.to(self.device,non_blocking=True)))
        if self.device.type=='cuda':torch.cuda.current_stream().synchronize()

    def compile(self,individuals,tape,*,chunk_candles=4096,emit=None,packed=True,
                listing_batch=32,workspace_gib=4.):
        started=perf_counter();b=len(individuals);t=len(tape.clocks);n=len(tape.tickers)
        if chunk_candles<1 or listing_batch<1 or workspace_gib<=0:
            raise ValueError('Positive bounded compilation resources required')
        # Boolean gates are population-dependent; features remain shared.
        required=t*b*n*(1 if packed else len(STAGES))
        if required>8*1024**3:raise MemoryError('Lifecycle gates exceed explicit 8GiB envelope')
        gates=torch.zeros((t,b,n),dtype=torch.uint8,device=self.device) if packed else {stage:torch.zeros((t,b,n),dtype=torch.bool,device=self.device) for stage in STAGES}
        programs={s:TorchPrograms([v.programs()[s] for v in individuals],CATALOG,self.device) for s in STAGES}
        boundaries=tape.clocks*1_000_000
        # Length buckets limit padding. Each core chunk owns its observations;
        # preceding rows supply temporal context only and cannot emit twice.
        buckets={};remaining={}
        for ticker,(clocks,features,valid) in enumerate(self.rows):
            remaining[ticker]=(len(clocks)+chunk_candles-1)//chunk_candles
            for left in range(0,len(clocks),chunk_candles):
                begin=max(0,left-119);right=min(len(clocks),left+chunk_candles)
                length=right-begin;bucket=1<<(length-1).bit_length()
                buckets.setdefault(bucket,[]).append((ticker,begin,right,left-begin))
        completed=sum(v==0 for v in remaining.values())
        width=max(p.width for p in programs.values())
        budget=int(workspace_gib*1024**3)
        for tasks in buckets.values():
            cursor=0
            while cursor<len(tasks):
                length=max(right-begin for _,begin,right,_ in tasks[cursor:cursor+listing_batch])
                fields=self.rows[tasks[cursor][0]][1].shape[-1]
                # Values/masks, repeated stacks/gathers and operation temporaries;
                # feature storage has no candidate axis. Fail before allocation.
                per_listing=length*(fields*5+b*(width*20+128)+32)
                count=min(listing_batch,budget//per_listing,len(tasks)-cursor)
                if count<1:raise MemoryError('Rule workspace cannot fit one bounded listing chunk')
                batch=tasks[cursor:cursor+count];cursor+=count
                length=max(right-begin for _,begin,right,_ in batch)
                features=self.rows[batch[0][0]][1].new_zeros((count,length,fields))
                valid=torch.zeros_like(features,dtype=torch.bool)
                clocks=torch.full((count,length),int(boundaries[-1])+1,dtype=torch.int64,device=self.device)
                core=torch.zeros((count,length),dtype=torch.bool,device=self.device)
                tickers=[]
                for row,(ticker,begin,right,warmup) in enumerate(batch):
                    source_clock,source_features,source_valid=self.rows[ticker];size=right-begin
                    features[row,:size]=source_features[begin:right]
                    valid[row,:size]=source_valid[begin:right]
                    clocks[row,:size]=source_clock[begin:right]
                    core[row,warmup:size]=True;tickers.append(ticker)
                positions=torch.searchsorted(boundaries,clocks)
                core&=(positions<t)&(boundaries[positions.clamp_max(t-1)]==clocks)
                ticker_axis=torch.tensor(tickers,device=self.device)[:,None]
                candidate_axis=torch.arange(b,device=self.device)[:,None,None]
                targets=((positions.clamp_max(t-1)[None]*b+candidate_axis)*n+ticker_axis[None]).expand(b,count,length)
                for stage,program in programs.items():
                    values,known=program(features,valid)
                    signal=(values!=0)&known&core[None]
                    if packed:
                        # Unique active (time,candidate,listing) within/across
                        # chunks. Zero padding can collide but adds no bits.
                        gates.view(-1).scatter_add_(0,targets.reshape(-1),
                            (signal.to(torch.uint8)*(1<<STAGES.index(stage))).reshape(-1))
                    else:
                        gates[stage].view(-1).scatter_reduce_(0,targets.reshape(-1),signal.reshape(-1),reduce='amax')
                for ticker,_,_,_ in batch:
                    remaining[ticker]-=1
                    if remaining[ticker]==0:completed+=1
                if emit:emit(dict(stage='Compile lifecycle rules',completed_tickers=completed,total_tickers=len(self.rows)))
        if self.device.type=='cuda':torch.cuda.current_stream().synchronize()
        return gates,perf_counter()-started
