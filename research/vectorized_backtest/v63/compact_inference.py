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
        self.entry=torch.arange(TOP_K,device=data.device,dtype=torch.int32)[None].expand(self.width,-1).contiguous()
        if self.base.uses_cross:
            from types import SimpleNamespace
            base=self.base;columns=base.cross_columns;lookup={s:i for i,s in enumerate(columns)}
            quotes=[base.columns[int(i)] for i in base.quote_columns.cpu().tolist()]
            selected=[i for i,s in enumerate(quotes) if s in lookup]
            self.lag_projection=SimpleNamespace(columns=columns,lookup=lookup,
                logarithmic=base.logarithmic.index_select(0,base.cross_indices),
                quote_columns=torch.tensor([lookup[quotes[i]] for i in selected],device=data.device,dtype=torch.long),
                quote_selection=base.quote_selection.index_select(0,torch.tensor(selected,device=data.device,dtype=torch.long)))

    def materialize(self,indices):
        slots=self.slots.index_select(0,indices)
        values,valid=project(self.base,self.data,indices,slots=slots)
        if self.base.uses_cross:
            # Reuse absolute values already computed in this block, matching
            # ticker identity across moving ranks. Fetch only newcomers/boundary.
            matches=(slots[1:,:,None]==slots[:-1,None,:])&(slots[1:,:,None]>=0)
            found=matches.any(-1);prior=matches.int().argmax(-1)
            current=values[...,:len(self.base.columns)].index_select(-1,self.base.cross_indices)
            known=valid.index_select(-1,self.base.cross_indices)
            lag=torch.empty_like(current);lm=torch.empty_like(known)
            lag[1:]=current[:-1].gather(1,prior[...,None].expand(-1,-1,current.shape[-1]))
            lm[1:]=known[:-1].gather(1,prior[...,None].expand(-1,-1,current.shape[-1]))
            missing=torch.cat((torch.ones_like(slots[:1],dtype=torch.bool),~found),0)
            time,slot=missing.nonzero(as_tuple=True)
            boundary,bm=project(self.lag_projection,self.data,(indices[time]-1).clamp_min(0),True,slots=slots[time,slot,None])
            lag[time,slot]=boundary[:,0];lm[time,slot]=bm[:,0]
            values=torch.cat((values,lag),-1)
            valid=torch.cat((valid,lm&(indices[:,None,None]>0)),-1)
        return values,valid,slots

    def __call__(self,indices):
        from .network_cuda import market_batch
        values,valid,slots=self.materialize(indices)
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
