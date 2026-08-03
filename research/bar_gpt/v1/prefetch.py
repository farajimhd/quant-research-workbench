from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import torch

from research.bar_gpt.v1.data import BarGPTBatch


class DeviceBatchPrefetcher:
    """Bounded one-batch device prefetch with an optional dedicated CUDA stream."""

    def __init__(self, loader: Any, device: torch.device, *, enabled: bool) -> None:
        self.iterator: Iterator[BarGPTBatch] = iter(loader)
        self.device = device
        self.enabled = bool(enabled and device.type == "cuda")
        self.stream = torch.cuda.Stream(device=device) if self.enabled else None
        self._next_batch: BarGPTBatch | None = None
        self._next_raw: BarGPTBatch | None = None
        self._exhausted = False
        self._preload()

    def _preload(self) -> None:
        if self._exhausted:
            self._next_batch = None
            self._next_raw = None
            return
        try:
            raw = next(self.iterator)
        except StopIteration:
            self._exhausted = True
            self._next_batch = None
            self._next_raw = None
            return
        self._next_raw = raw
        if self.stream is None:
            self._next_batch = raw.to(self.device)
            return
        with torch.cuda.stream(self.stream):
            self._next_batch = raw.to(self.device, non_blocking=True)

    def next(self) -> tuple[BarGPTBatch, float]:
        if self._next_batch is None:
            raise StopIteration
        started = time.perf_counter()
        if self.stream is not None:
            torch.cuda.current_stream(self.device).wait_stream(self.stream)
        batch = self._next_batch
        self._next_raw = None
        self._preload()
        return batch, time.perf_counter() - started

    def __iter__(self) -> "DeviceBatchPrefetcher":
        return self

    def __next__(self) -> BarGPTBatch:
        return self.next()[0]
