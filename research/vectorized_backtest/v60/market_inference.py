"""Market-only, time-batched inference. No position or execution arguments."""
import torch
import math
from .features import CATALOG
from .feature_policy import transform_projected


def project(plan,data,indices,absolute_only=False):
    if data.device.type=='cpu' and hasattr(plan,'project'):
        # CPU cross-sectional reductions choose a different summation layout
        # for a multi-clock tensor. Keep the scalar-clock arithmetic as oracle.
        rows=[plan.project_absolute(data,i) if absolute_only else plan.project(data,i) for i in indices]
        return torch.stack([r[0] for r in rows]),torch.stack([r[1] for r in rows])
    a=data.resident_features.tensors
    rows=a['source'].index_select(0,indices).T
    ids=a['ids'].index_select(0,indices).T.long()
    clock=a['clocks'].index_select(0,indices)[None]
    v,m=data.resident_features._values(rows,ids,clock,plan.columns)
    valid=m&torch.isfinite(v)
    take=lambda name:data.tensors[name].index_select(0,indices).T
    bid,ask,mark=take('bid'),take('ask'),take('mark')
    quoted=take('quote_valid')&torch.isfinite(bid)&torch.isfinite(ask)&(bid>0)&(ask>=bid)
    reference=torch.isfinite(mark)&(mark>0);denominator=torch.where(reference,mark,1.)
    quotes=torch.stack((bid/denominator-1,ask/denominator-1,(ask-bid)/denominator,bid,ask,ask-bid),-1).to(v.dtype)
    qm=torch.stack((quoted&reference,)*3+(quoted,)*3,-1)
    if hasattr(plan,"quote_selection"):
        quotes=quotes.index_select(-1,plan.quote_selection);qm=qm.index_select(-1,plan.quote_selection)
    v=v.index_copy(2,plan.quote_columns,torch.where(qm,quotes,0.))
    valid=valid.index_copy(2,plan.quote_columns,qm)
    results=[]
    for tick in range(len(indices)):
        value=v[:,tick].contiguous();mask=valid[:,tick].contiguous()
        clean=torch.where(mask,value,0.)
        absolute=torch.where(plan.logarithmic,clean.sign()*clean.abs().log1p()/4,clean).tanh()
        if absolute_only:
            from .feature_policy import SHARES
            absolute=torch.where(mask,absolute,0.)
            for canonical in SHARES:
                if canonical not in plan.lookup:continue
                column=plan.lookup[canonical]
                logged=torch.expm1(4*torch.atanh(absolute[...,column].clamp(-.999999,.999999)))
                absolute[...,column]=torch.where(mask[...,column],torch.tanh(torch.floor(logged/math.log(10.))/10.),0.)
            results.append(absolute)
            continue
        count=mask.sum(0).clamp_min(1)
        first=mask.int().argmax(0,keepdim=True)
        anchor=clean.gather(0,first).squeeze(0)
        shifted=torch.where(mask,clean-anchor,0.)
        from .feature_layout import column_sum
        deviation=shifted-column_sum(shifted,plan.reduction_columns)/count
        scale=(column_sum(torch.where(mask,deviation.square(),0.),plan.reduction_columns)/count).sqrt().clamp_min(1e-6)
        relative=(deviation/(2*scale)).tanh()
        values=torch.cat((torch.where(mask,absolute,0.),torch.where(mask,relative,0.),mask.float()),-1)
        values=transform_projected(values,plan.columns)
        results.append(values[...,:len(plan.columns)] if absolute_only else values)
    return torch.stack(results),valid.transpose(0,1)



class Inference:
    """Bounded workspace for independent clocks; optional market-only routing."""
    def __init__(self,plan,data,u):
        self.plan=plan;self.data=data;self.u=u
        self.base=plan.base if hasattr(plan,'base') else plan
        base=self.base;self.b=len(base.members)
        # Compact only neural entry evaluation. Cross-sectional normalization
        # continues to use every listing, and execution sees the original grid.
        if 'v60_entry_indices' not in data.tensors:
            import numpy as np
            host=getattr(data,'host_tensors',{}).get('membership',data.tensors['membership'])
            mask=host.detach().cpu().numpy()
            counts=mask.sum(1);k=max(1,int(counts.max()))
            rows,columns=np.nonzero(mask)
            ranks=np.arange(len(rows))-np.repeat(np.cumsum(np.r_[0,counts[:-1]]),counts)
            table=np.full((len(mask),k),-1,np.int32);table[rows,ranks]=columns
            data.tensors['v60_entry_indices']=torch.as_tensor(table,device=data.device)
        self.entry_indices=data.tensors['v60_entry_indices']
        if 'v60_management_allowed' not in data.tensors:
            import numpy as np
            host=getattr(data,'host_tensors',{}).get('membership',data.tensors['membership'])
            possible=np.maximum.accumulate(host.detach().cpu().numpy(),axis=0)
            data.tensors['v60_management_allowed']=torch.as_tensor(possible,device=data.device)
        self.management_allowed=data.tensors['v60_management_allowed']
        if self.management_allowed.shape[1]<u:
            self.management_allowed=torch.nn.functional.pad(self.management_allowed,(0,u-self.management_allowed.shape[1]),value=False)


        # This workspace is additional to the existing one-clock plan. Never
        # reduce candidates, nodes or listings to fit a time batch.
        budget=256*2**20
        if data.device.type=='cuda':
            # Two resident session lanes share the GPU. Reserve at most one
            # eighth of currently free memory per producer, capped at 1 GiB.
            # The old fixed 256 MiB allowance reduced real 1024-member batches
            # to one clock even when the workstation had ample free memory.
            budget=min(2**30,torch.cuda.mem_get_info(data.device)[0]//8)
        self.width=max(1,min(8,budget//max(1,base.total_nodes*u*5)))
        self.work=torch.empty((self.width,base.total_nodes,u),device=data.device)
        self.known=torch.empty_like(self.work,dtype=torch.bool)
        if base.uses_cross:
            from types import SimpleNamespace
            columns=base.cross_columns;lookup={s:i for i,s in enumerate(columns)}
            quote_canonical=[base.columns[int(i)] for i in base.quote_columns.cpu().tolist()]
            selected=[i for i,s in enumerate(quote_canonical) if s in lookup]
            self.lag_projection=SimpleNamespace(columns=columns,lookup=lookup,
                logarithmic=base.logarithmic.index_select(0,base.cross_indices),
                quote_columns=torch.tensor([lookup[quote_canonical[i]] for i in selected],device=data.device,dtype=torch.long),
                quote_selection=torch.tensor(selected,device=data.device,dtype=torch.long))


    def __call__(self,indices):
        from .network_cuda import market_batch
        base=self.base;data=self.data
        values,valid=project(base,data,indices)
        if base.uses_cross:
            boundary,boundary_valid=project(self.lag_projection,data,(indices[:1]-1).clamp_min(0),True)
            lag=torch.cat((boundary,values[:-1,:,:len(base.columns)].index_select(-1,base.cross_indices)),0)
            lm=torch.cat((boundary_valid,valid[:-1].index_select(-1,base.cross_indices)),0)
            values=torch.cat((values,lag),-1)
            valid=torch.cat((valid,lm&(indices[:,None,None]>0)),-1)
        membership=data.tensors['membership'].index_select(0,indices)
        extra=self.u-values.shape[1]
        if extra:
            values=torch.nn.functional.pad(values,(0,0,0,extra))
            valid=torch.nn.functional.pad(valid,(0,0,0,extra),value=False)
            membership=torch.nn.functional.pad(membership,(0,extra),value=False)
        signals,ratios=market_batch(base,values,valid,membership,self.work,self.known,self.entry_indices.index_select(0,indices),self.management_allowed.index_select(0,indices))
        if not hasattr(self.plan,'base'):return signals,ratios,None,None,None
        p=self.plan
        ask=data.tensors['ask'].index_select(0,indices)
        quoted=data.tensors['quote_valid'].index_select(0,indices)
        ids=data.tensors['v60_entry_swing_ids'].index_select(0,indices).long()
        if extra:
            ask=torch.nn.functional.pad(ask,(0,extra));quoted=torch.nn.functional.pad(quoted,(0,extra),value=False)
            ids=torch.nn.functional.pad(ids,(0,extra))
        swing=data.tensors['v60_entry_swing_values'][ids[:,None,:],p.leaf_columns[None,:,None]]
        stop=torch.where(p.policies[None,:,8,None]>0,swing,ask[:,None,:]*(1-p.policies[None,:,0,None]))
        limit=ask[:,None,:]*(1+p.policies[None,:,9,None])
        admissible=quoted[:,None,:]&membership[:,None,:]&torch.isfinite(stop)&(stop>0)&(stop<ask[:,None,:])&(stop<limit)&(ratios[...,0]>0)
        branches=signals[:,p.branch_index]
        eligible=((branches&1)!=0)&admissible[:,p.branch_index]&p.branch_valid[None,:,:,None]
        owner=eligible.int().argmax(2)
        selected=p.branch_index[None].expand(len(indices),-1,-1).gather(2,owner)
        time=torch.arange(len(indices),device=data.device)[:,None,None]
        ticker=torch.arange(self.u,device=data.device)[None,None,:]
        return signals[time,selected,ticker],ratios[time,selected,ticker],p.policies[selected],p.leaf_columns[selected],owner
