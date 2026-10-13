"""Bounded GPU completed-position ledger, exact profits and listing attribution."""
import torch


class Episodes:
    def __init__(self, sessions, candidates, capacity, device, max_listings=1):
        if not 1 <= capacity <= 1048576:
            raise ValueError('Explicit bounded episode capacity required')
        self.capacity = capacity;self.max_listings=max_listings
        shape = (sessions, candidates, capacity+max_listings)
        self.profits = torch.zeros(shape, dtype=torch.float64, device=device)
        self.listings = torch.zeros(shape, dtype=torch.int64, device=device)
        self.counts = torch.zeros(shape[:2], dtype=torch.int64, device=device)

    def reset(self):
        self.profits.zero_(); self.listings.zero_(); self.counts.zero_()

    def append(self, before, after, active):
        closed = (before[...,0]>1e-12)&(after[...,0]<=1e-12)&active[:,None,None]
        if before.shape[-2]>self.max_listings:raise ValueError('Episode listing bound exceeded')
        self.append_events(closed,after[...,7]-before[...,8])

    def append_events(self,closed,profits):
        prefix=closed.to(torch.int64).cumsum(-1)
        offsets = prefix-1+self.counts[...,None]
        listings = torch.arange(closed.shape[-1],device=closed.device).expand_as(offsets)
        retained=closed&(offsets<self.capacity)
        # One scratch slot per listing avoids a heavily contended shared sentinel.
        # All indices are unique within a candidate, allowing non-atomic scatter.
        indices = torch.where(retained, offsets.clamp_min(0), self.capacity+listings)
        pnl = torch.where(retained, profits, 0.)
        self.profits.scatter_(-1, indices, pnl)
        self.listings.scatter_(-1, indices, torch.where(retained,listings,0))
        self.counts.add_(prefix[...,-1])

    def export(self):
        counts = self.counts.cpu().numpy()
        if (counts>self.capacity).any():
            raise MemoryError('Completed-position buffer overflow; no truncated score published')
        largest = int(counts.max())
        # Transfer completed positions only; unused bounded capacity stays on GPU.
        mask = torch.arange(largest, device=self.counts.device) < self.counts[..., None]
        profits = self.profits[..., :largest].masked_select(mask).cpu().numpy()
        listings = self.listings[..., :largest].masked_select(mask).cpu().numpy()
        result = []; offset = 0
        for row in counts:
            session_profits = []; session_listings = []
            for count in row:
                end = offset + int(count)
                session_profits.append(profits[offset:end].tolist())
                session_listings.append(listings[offset:end].tolist())
                offset = end
            result.append(dict(episode_profits=session_profits, episode_listings=session_listings))
        return result
