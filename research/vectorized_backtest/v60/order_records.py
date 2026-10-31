"""Bounded actual order receipts; overflow invalidates the replay, never truncates."""
import torch


class OrderRecords:
    def __init__(self,b,u,device,capacity=8192,sink=None):
        self.capacity=capacity;self.u=u
        self.population=b;self.chunks=[];self.mapping=None
        self.sink=sink
        self.counts=torch.zeros(b,dtype=torch.int64,device=device)
        self.data=torch.zeros((b,capacity+u,9),dtype=torch.float64,device=device)

    def reset(self):
        self.counts.zero_()
        self.chunks.clear()

    def append(self,complete,record):
        if record.shape[-1]==17:
            if self.data.device.type=='cuda':
                from .order_scatter import append_pair
                append_pair(self,complete,record);return
            self.append(record[...,16]>0,record[...,8:16])
            self.append(complete,record[...,:8])
            return
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

    def drain(self):
        """Transfer only completed rows; caller retries an overflowing GPU chunk."""
        import numpy as np
        counts=self.counts.cpu().numpy().copy()
        if counts.max(initial=0)>self.capacity:return False
        width=int(counts.max(initial=0))
        if width:
            if self.data.device.type=='cuda':
                from .ledger_pack import pack
                data=pack([self.data[...,i] for i in range(9)],self.counts,counts).cpu().numpy()
            else:
                mask=torch.arange(width,device=self.counts.device)[None]<self.counts[:,None]
                data=self.data[:,:width][mask].cpu().numpy()
            ids=np.arange(len(counts)) if self.mapping is None else self.mapping
            if self.sink is not None:self.sink.submit(ids,counts,data)
            else:self.chunks.append((ids,counts,data))
        self.counts.zero_();return True

    def absorb(self,other):
        if not other.drain():raise RuntimeError('Undrained overflowing order journal')
        self.chunks.extend(other.chunks);other.chunks=[]

    def export(self):
        import numpy as np
        if not self.drain():raise RuntimeError('Undrained overflowing order journal')
        if self.sink is not None:return self.sink.finish()
        result=[[] for _ in range(self.population)]
        for ids,counts,data in self.chunks:
            offset=0
            for index,count in zip(ids,counts):
                end=offset+int(count)
                if count:result[int(index)].append(data[offset:end])
                offset=end
        result=[np.concatenate(parts).tolist() if parts else [] for parts in result]
        self.chunks.clear();return result
