"""Device-resident top-N plus retained identity allocation for compact accounts.

The broker must supply retention for every holding, pending order and rotation
reference. Capacity exhaustion invalidates the run; it never drops a retained
identity. A changed slot must be reset before the next broker interval.
"""
import torch


class HoldingRegistry:
    def __init__(self,candidates,capacity,top_n,*,device='cpu'):
        if any(type(v) is not int or v<1 for v in (candidates,capacity,top_n)) or capacity<top_n:
            raise ValueError('Compact identity capacity must cover the decision universe')
        self.ids=torch.full((candidates,capacity),-1,dtype=torch.int64,device=device)
        self.overflow=torch.zeros(candidates,dtype=torch.bool,device=device)
        self.axis=torch.arange(capacity,device=device).expand(candidates,-1)
        self.rank=torch.arange(top_n,device=device).expand(candidates,-1)
        self.top_n=top_n

    def reconcile(self,top,retained):
        if top.shape!=(self.top_n,) or retained.shape!=self.ids.shape or retained.dtype!=torch.bool:
            raise ValueError('Compact top/retention shape changed')
        old=self.ids.clone()
        membership=(self.ids[:,:,None]==top[None,None,:])&(self.ids[:,:,None]>=0)
        keep=retained|membership.any(-1)
        self.ids.copy_(torch.where(keep,self.ids,-1))
        present=((self.ids[:,:,None]==top[None,None,:])&(top[None,None,:]>=0)).any(1)
        missing=(top[None]>=0)&~present
        free=self.ids<0;needed=missing.sum(-1)
        self.overflow.logical_or_(needed>free.sum(-1))
        free_slots=torch.where(free,self.axis,self.ids.shape[1]).sort(-1).values[:,:self.top_n]
        source=torch.where(missing,self.rank,self.top_n).sort(-1).values
        safe_source=source.clamp_max(self.top_n-1)
        additions=top[safe_source]
        valid=(self.rank<needed[:,None])&(free_slots<self.ids.shape[1])
        # Invalid padding contributes -1 under amax, so repeated scratch indices
        # cannot overwrite the final legitimate allocation or a held identity.
        self.ids.scatter_reduce_(1,free_slots.clamp_max(self.ids.shape[1]-1),torch.where(valid,additions,-1),reduce='amax',include_self=True)
        changed=self.ids!=old
        return changed,(self.ids[:,:,None]==top[None,None,:]).any(-1)&(self.ids>=0)

    def reset(self):self.ids.fill_(-1);self.overflow.zero_()

    def require_valid(self):
        if bool(self.overflow.any()):raise RuntimeError('Compact holding/order capacity exhausted; no completed result')
