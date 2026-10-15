"""Bounded GPU completed-position ledger, exact profits and listing attribution."""
import torch


class Episodes:
    def __init__(self, sessions, candidates, capacity, device, max_listings=1, trace=False):
        if not 1 <= capacity <= 1048576:
            raise ValueError('Explicit bounded episode capacity required')
        self.capacity = capacity;self.max_listings=max_listings
        shape = (sessions, candidates, capacity+max_listings)
        self.profits = torch.zeros(shape, dtype=torch.float64, device=device)
        self.listings = torch.zeros(shape, dtype=torch.int64, device=device)
        self.counts = torch.zeros(shape[:2], dtype=torch.int64, device=device)
        self.trace=trace
        if trace:
            self.entry_clocks=torch.zeros(shape,dtype=torch.int64,device=device)
            self.exit_clocks=torch.zeros(shape,dtype=torch.int64,device=device)

    def reset(self):
        self.profits.zero_(); self.listings.zero_(); self.counts.zero_()
        if self.trace:self.entry_clocks.zero_();self.exit_clocks.zero_()

    def append(self, before, after, active, entry_clocks=None, exit_clock=None):
        closed = (before[...,0]>1e-12)&(after[...,0]<=1e-12)&active[:,None,None]
        if before.shape[-2]>self.max_listings:raise ValueError('Episode listing bound exceeded')
        self.append_events(closed,after[...,7]-before[...,8],entry_clocks,exit_clock)

    def append_events(self,closed,profits,entry_clocks=None,exit_clock=None):
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
        if self.trace:
            if entry_clocks is None or exit_clock is None:raise ValueError('Complete trace clocks required')
            self.entry_clocks.scatter_(-1,indices,torch.where(retained,entry_clocks.to(torch.int64),0))
            self.exit_clocks.scatter_(-1,indices,torch.where(retained,exit_clock.to(torch.int64).expand_as(offsets),0))
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
        if self.trace:
            entries=self.entry_clocks[...,:largest].masked_select(mask).cpu().numpy()
            exits=self.exit_clocks[...,:largest].masked_select(mask).cpu().numpy()
        result = []; offset = 0
        for row in counts:
            session_profits = []; session_listings = [];session_entries=[];session_exits=[]
            for count in row:
                end = offset + int(count)
                session_profits.append(profits[offset:end].tolist())
                session_listings.append(listings[offset:end].tolist())
                if self.trace:
                    session_entries.append(entries[offset:end].tolist());session_exits.append(exits[offset:end].tolist())
                offset = end
            result.append(dict(episode_profits=session_profits, episode_listings=session_listings))
            if self.trace:result[-1].update(episode_entry_clocks=session_entries,episode_exit_clocks=session_exits)
        return result
