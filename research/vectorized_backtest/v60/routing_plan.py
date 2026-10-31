"""GPU-batched branch evaluation with sticky, per-position strategy ownership."""
import torch
from .strategy_mix import leaves
from .network import NetworkPlan
from .genome import STAGES

POLICY_FIELDS=('stop_fraction','target_fraction','reduce_fraction','trail_fraction','add_minimum_profit','reduce_minimum_profit','maximum_adds','cooldown','swing_left','buy_limit_offset','sell_limit_offset')

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
    @property
    def cuda_block(self):return self.base.cuda_block
    @cuda_block.setter
    def cuda_block(self,value):self.base.cuda_block=value
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
        if 'pending_orders' in self.__dict__:
            self.base.pending_orders=self.pending_orders.index_select(1,self.parent_index)
        clocks=opened.index_select(1,self.parent_index)
        raw=self.base.signals(data,index,expanded,clocks,previous)[0]
        branch=raw[self.branch_index]
        # Choose among admissible entries, not merely true raw entry heads.
        # Otherwise an invalid A branch can hide a valid B strategy.
        ask=data.tensors['ask'].index_select(0,index.reshape(1)).reshape(-1)
        valid=data.tensors['quote_valid'].index_select(0,index.reshape(1)).reshape(-1)
        member=data.tensors['membership'].index_select(0,index.reshape(1)).reshape(-1)
        u=raw.shape[-1]
        if len(ask)<u:
            from torch.nn.functional import pad
            amount=u-len(ask)
            ask=pad(ask,(0,amount));valid=pad(valid,(0,amount),value=False);member=pad(member,(0,amount),value=False)
        bank=data.tensors['v60_entry_swing_values']
        ids=data.tensors['v60_entry_swing_ids'].index_select(0,index.reshape(1)).reshape(-1)
        if len(ids)<u:ids=torch.nn.functional.pad(ids,(0,u-len(ids)),value=0)
        swing=bank[ids[None,:],self.leaf_columns[:,None]]
        policy=self.policies
        stop=torch.where(policy[:,8,None]>0,swing,ask[None,:]*(1-policy[:,0,None]))
        limit=ask[None,:]*(1+policy[:,9,None])
        admissible=valid&member&torch.isfinite(stop)&(stop>0)&(stop<ask)&(stop<limit)
        admissible=admissible&((raw&2)==0)&(self.base.ratios[0,...,0]>0)
        eligible=((branch&1)!=0)&admissible[self.branch_index]&self.branch_valid[...,None]
        proposed=eligible.to(torch.int32).argmax(1)
        held=position[0,...,0]>1e-12
        if 'pending_orders' in self.__dict__:held=held|(self.pending_orders[0,...,0]!=0)
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
