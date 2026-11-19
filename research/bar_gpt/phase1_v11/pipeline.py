"""Ordered prefetch with bounded, reusable pinned host staging buffers."""
import time
import torch
from ..phase1_v7.full_data import loader as original_loader
from ..phase1_v5_raw.engine import DeviceBatches as OriginalDeviceBatches


def loader(*args, **kwargs):
    source = original_loader(*args, **kwargs)
    # Workers still prefetch into shared CPU memory. Pin only the two reusable
    # staging slots below, not every fresh batch in DataLoader's pinning thread.
    source.pin_memory = False
    return source


class DeviceBatches(OriginalDeviceBatches):
    def __init__(self, source, device):
        self.staging = [{}, {}]
        self.transfers = [None, None]
        self.slot = 0
        self.staging_seconds = 0.
        self.pin_allocations = 0
        super().__init__(source, device)

    @property
    def pinned_bytes(self):
        return sum(t.numel() * t.element_size() for slot in self.staging for t in slot.values())

    def advance(self):
        began = time.perf_counter()
        try:
            batch = next(self.source)
        except StopIteration:
            self.next = None
            return
        self.wait_seconds += time.perf_counter() - began
        if self.stream is None:
            self.next = batch.to(self.device)
            return
        began = time.perf_counter()
        slot = self.slot
        # CPU must not overwrite storage still being read by an asynchronous H2D.
        if self.transfers[slot] is not None:
            self.transfers[slot].synchronize()
        index = 0

        def stage(value):
            nonlocal index
            key = index
            index += 1
            if value.device.type != 'cpu':
                raise ValueError('Host staging requires CPU source tensors')
            storage = self.staging[slot].get(key)
            if storage is not None and (storage.dtype != value.dtype or storage.shape[1:] != value.shape[1:]):
                raise ValueError('Batch field schema changed during traversal')
            if storage is None or storage.shape[0] < value.shape[0]:
                storage = torch.empty(value.shape, dtype=value.dtype, pin_memory=True)
                self.staging[slot][key] = storage
                self.pin_allocations += 1
            view = storage[:value.shape[0]]
            view.copy_(value)
            return view

        staged = batch.map(stage)  # Batch.map preserves grid/target/raw aliases.
        self.staging_seconds += time.perf_counter() - began
        with torch.cuda.stream(self.stream):
            self.next = staged.to(self.device, non_blocking=True)
            self.ready = torch.cuda.Event()
            self.ready.record(self.stream)
        self.transfers[slot] = self.ready
        self.slot = 1 - slot
