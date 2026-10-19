"""Shared certified data/execution infrastructure; no V27 genetics."""
from research.vectorized_backtest.v27.io_pipeline import *
from research.vectorized_backtest.v27.io_pipeline import Publisher as _Publisher
from .publication import write_json


class Publisher(_Publisher):
    """Same bounded queue and error propagation, compact V32 artifacts."""
    def write(self, path, value):
        if len(self.pending) >= self.capacity:
            self.pending.popleft().result()
        self.pending.append(self.pool.submit(write_json, path, value))


from research.vectorized_backtest.v27.io_pipeline import TransferStaging as _TransferStaging


class TransferStaging(_TransferStaging):
    def release_completed(self):
        """Resident locals need no upload arena between generations."""
        import torch
        released=0
        for slot in self.slots:
            if slot['event'] is not None:slot['event'].synchronize()
            if slot['buffer'] is not None:released+=slot['buffer'].numel()
            slot['buffer']=None;slot['event']=None
        # This installed PyTorch API releases only unused pinned allocations;
        # active arenas and authoritative pageable/device tensors stay owned.
        torch._C._host_emptyCache()
        return released
