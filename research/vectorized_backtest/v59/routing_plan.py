"""GPU-batched branch evaluation with sticky, per-position strategy ownership."""
import torch
from .strategy_mix import leaves
from .network import NetworkPlan
from .genome import STAGES

POLICY_FIELDS=('stop_fraction','target_fraction','reduce_fraction','trail_fraction','add_minimum_profit','reduce_minimum_profit','maximum_adds','cooldown','swing_left')

class RoutingPlan:
    def __init__(self,members,device):
        self.members=members;self.device=torch.device(device);flat=[];parents=[];indices=[]
        for i,m in enumerate(members):
            ids=[]
            for leaf in leaves(m):ids.append(len(flat));flat.append(leaf);parents.append(i)
            indices.append(ids)
        self.leaves=flat;self.base=NetworkPlan(flat,device)
        self.parent_index=torch.tensor(parents,device=device)
        width=max(map(len,indices))
        self.branch_index=torch.tensor([v+[v[0]]*(width-len(v)) for v in indices],device=device)
        self.branch_valid=torch.tensor([[True]*len(v)+[False]*(width-len(v)) for v in indices],device=device)
        self.policies=torch.tensor([[getattr(m.policy,k) for k in POLICY_FIELDS] for m in flat],device=device,dtype=torch.float64)
        self.ages=torch.tensor([[m.minimum_age.get(k,0) for k in STAGES] for m in flat],device=device,dtype=torch.float64)
        self.workspace_shape=None
    def __getattr__(self,name):return getattr(self.base,name)
    def prepare(self,b,u):
        if self.workspace_shape==(b,u):return
        self.base.prepare(len(self.leaves),u)
        self.owner=torch.zeros((b,u),device=self.device,dtype=torch.int64)
        self.ratios=torch.ones((1,b,u,3),device=self.device,dtype=torch.float64)
        self.feedback=torch.zeros((b,u,1),device=self.device,dtype=torch.float32)
        self.workspace_shape=(b,u)
    def bind(self,policy,limits,columns):
        self.selected_policy=policy;self.selected_limits=limits;self.selected_columns=columns
    def reset(self):
        self.base.reset()
        if self.workspace_shape:self.owner.zero_();self.ratios.fill_(1);self.feedback.zero_()
    def signals(self,data,index,position,opened,previous,out=None):
        if hasattr(self,'market_block'):self.base.market_block=self.market_block
        expanded=position.index_select(1,self.parent_index)
        clocks=opened.index_select(1,self.parent_index)
        raw=self.base.signals(data,index,expanded,clocks,previous)[0]
        if self.device.type=='cuda':
            from .routing_kernel import route
            if out is None:out=torch.empty((1,*self.workspace_shape),device=self.device,dtype=raw.dtype)
            return route(self,raw,position,out)
        branch=raw[self.branch_index]
        eligible=((branch&1)!=0)&self.branch_valid[...,None]
        proposed=eligible.to(torch.int32).argmax(1)
        held=position[0,...,0]>1e-12
        self.owner.copy_(torch.where(held,self.owner,proposed))
        selected=self.branch_index.gather(1,self.owner)
        ticker=torch.arange(raw.shape[-1],device=self.device)[None,:]
        result=raw[selected,ticker][None]
        self.ratios.copy_(self.base.ratios[0,selected,ticker][None])
        self.selected_policy.copy_(self.policies[selected][None])
        self.selected_limits.copy_(self.ages[selected][None])
        self.selected_columns.copy_(self.leaf_columns[selected])
        if out is not None:out.copy_(result);return out
        return result
    def close(self):self.base.close()
