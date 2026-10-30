"""Bounded actual order receipts; overflow invalidates the replay, never truncates."""
import torch


class OrderRecords:
    def __init__(self,b,u,device,capacity=8192):
        self.capacity=capacity;self.u=u
        self.counts=torch.zeros(b,dtype=torch.int64,device=device)
        self.data=torch.zeros((b,capacity+u,9),dtype=torch.float64,device=device)

    def reset(self):
        self.counts.zero_()

    def append(self,complete,record):
        if self.data.device.type=='cuda':
            from .order_scatter import append
            append(self,complete,record);return
        complete=complete[0];record=record[0]
        prefix=complete.to(torch.int64).cumsum(-1)
        offsets=self.counts[:,None]+prefix-1
        listings=torch.arange(self.u,device=complete.device).expand_as(offsets)
        retained=complete&(offsets<self.capacity)
        indices=torch.where(retained,offsets,self.capacity+listings)
        rows=torch.arange(len(self.counts),device=complete.device)[:,None]
        self.data[rows,indices]=torch.cat((listings[...,None],record),-1)
        self.counts.add_(prefix[:,-1])

    def export(self):
        counts=self.counts.cpu().tolist()
        if max(counts,default=0)>self.capacity:
            raise MemoryError('Order receipt buffer overflow; no incomplete execution receipt published')
        width=max(counts,default=0)
        mask=torch.arange(width,device=self.counts.device)[None]<self.counts[:,None]
        data=self.data[:,:width][mask].cpu().tolist()
        result=[];offset=0
        for count in counts:
            result.append(data[offset:offset+count]);offset+=count
        return result
