import torch
from ..v5.shards.phase1_contract import FRAMES


class FrameSampler:
    """Equal frame probabilities, uniform within each existing training frame."""
    def __init__(self, frames):
        indices=[torch.where(frames==f)[0] for f in FRAMES]
        counts=[len(i) for i in indices]
        if min(counts)==0:raise ValueError('Every timeframe must have training examples')
        self.counts=torch.tensor(counts,device=frames.device)
        self.table=torch.zeros(6,max(counts),device=frames.device,dtype=torch.long)
        for j,i in enumerate(indices):self.table[j,:len(i)]=i

    def sample(self, batch, generator):
        frame=torch.randint(6,(batch,),device=self.table.device,generator=generator)
        offset=(torch.rand(batch,device=self.table.device,generator=generator)*self.counts[frame]).long()
        return self.table[frame,offset]
