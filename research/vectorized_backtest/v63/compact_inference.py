"""Five ranked slots, market-only graph inference and concrete price packets."""
import torch
import numpy as np
from .attention import selected_columns,TOP_K
from .market_inference import project
from .price_references import PricePlan


class Inference:
    def __init__(self,plan,data):
        self.plan=plan;self.base=plan.base if hasattr(plan,'base') else plan;self.data=data
        self.slots_cpu=selected_columns(data.arrays['top_indices'],data.listing_ids)
        self.slots=torch.as_tensor(self.slots_cpu,device=data.device,dtype=torch.long)
        self.base.prepare(len(self.base.members),TOP_K)
        self.prices=PricePlan(self.base.members,data,self.slots_cpu)
        free=torch.cuda.mem_get_info(data.device)[0]
        self.width=max(1,min(256,int(free*.05)//max(1,self.base.total_nodes*TOP_K*5)))
        self.work=torch.empty((self.width,self.base.total_nodes,TOP_K),device=data.device)
        self.known=torch.empty_like(self.work,dtype=torch.bool)
        # Identity slots make every action graph evaluate only the same five tickers.
        self.entry=torch.arange(TOP_K,device=data.device,dtype=torch.int32)[None].expand(self.width,-1)

    def __call__(self,indices):
        from .network_cuda import market_batch
        slots=self.slots.index_select(0,indices)
        values,valid=project(self.base,self.data,indices,slots=slots)
        if self.base.uses_cross:
            # Resolve the PREVIOUS values for the SAME ticker; rank slots move.
            lag,lm=project(self.base,self.data,(indices-1).clamp_min(0),True,slots=slots)
            values=torch.cat((values,lag.index_select(-1,self.base.cross_indices)),-1)
            valid=torch.cat((valid,lm.index_select(-1,self.base.cross_indices)&(indices[:,None,None]>0)),-1)
        signals,ratios=market_batch(self.base,values,valid,slots>=0,self.work,self.known,self.entry[:len(indices)])
        prices,guard=self.prices(indices)
        abstain=((signals&1)!=0)&~guard
        signals=torch.where(guard,signals,signals&~1)
        signals=torch.where((slots>=0)[:,None],signals,0)
        if hasattr(self.plan,'base'):
            p=self.plan;branches=signals[:,p.branch_index]
            eligible=((branches&1)!=0)&p.branch_valid[None,:,:,None]
            owner=eligible.int().argmax(2)
            selected=p.branch_index[None].expand(len(indices),-1,-1).gather(2,owner)
            t=torch.arange(len(indices),device=self.data.device)[:,None,None]
            k=torch.arange(TOP_K,device=self.data.device)[None,None,:]
            signals=signals[t,selected,k];ratios=ratios[t,selected,k];prices=prices[t,selected,k]
            abstain=abstain[t,selected,k]
        else:owner=torch.zeros_like(signals,dtype=torch.int64)
        return signals,ratios,prices,owner,abstain
