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

    def compile(self,individuals,tape,*,chunk_candles=4096,emit=None,packed=True):
        started=perf_counter();b=len(individuals);t=len(tape.clocks);n=len(tape.tickers)
        # Boolean gates are population-dependent; features remain shared.
        required=t*b*n*(1 if packed else len(STAGES))
        if required>8*1024**3:raise MemoryError('Lifecycle gates exceed explicit 8GiB envelope')
        gates=torch.zeros((t,b,n),dtype=torch.uint8,device=self.device) if packed else {stage:torch.zeros((t,b,n),dtype=torch.bool,device=self.device) for stage in STAGES}
        programs={s:TorchPrograms([v.programs()[s] for v in individuals],CATALOG,self.device) for s in STAGES}
        boundaries=tape.clocks*1_000_000
        for ticker,(clocks,features,valid) in enumerate(self.rows):
            if not len(clocks):continue
            # Clock -> latest completed actual candle. Require new observation;
            # no forward-fill creates a new signal across an empty clock gap.
            position=torch.searchsorted(clocks,boundaries,right=True)-1
            fresh=(position>=0)&(clocks[position.clamp_min(0)]==boundaries)
            for left in range(0,len(clocks),chunk_candles):
                begin=max(0,left-119);right=min(len(clocks),left+chunk_candles)
                selected=fresh&(position>=left)&(position<right)
                indices=torch.nonzero(selected).flatten()
                if not indices.numel():continue
                for stage,program in programs.items():
                    values,known=program(features[begin:right],valid[begin:right])
                    chosen=position[indices]-begin
                    signal=((values[:,chosen]!=0)&known[:,chosen]).T
                    if packed:gates[indices,:,ticker]|=signal.to(torch.uint8)*(1<<STAGES.index(stage))
                    else:gates[stage][indices,:,ticker]=signal
            if emit:emit(dict(stage='Compile lifecycle rules',completed_tickers=ticker+1,total_tickers=n))
        if self.device.type=='cuda':torch.cuda.current_stream().synchronize()
        return gates,perf_counter()-started
