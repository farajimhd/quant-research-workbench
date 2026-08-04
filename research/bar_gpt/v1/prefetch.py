from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import torch

from research.bar_gpt.v1.data import BarGPTBatch


@dataclass(frozen=True, slots=True)
class _ProducerFailure:
    error: BaseException


_END = object()


def _shutdown_loader_iterator(iterator: Iterator[BarGPTBatch] | None) -> None:
    """Synchronously release multiprocessing DataLoader workers when present."""
    if iterator is None:
        return
    shutdown = getattr(iterator, "_shutdown_workers", None)
    if callable(shutdown):
        shutdown()


class HostBatchCache:
    """Continuously fill a bounded RAM cache from a DataLoader iterator."""

    def __init__(self, loader: Any, *, capacity_batches: int) -> None:
        if capacity_batches <= 0:
            raise ValueError("host batch cache capacity must be positive")
        # Create multiprocessing workers on the owner thread before the cache
        # producer starts. The producer only advances the existing iterator.
        self._iterator: Iterator[BarGPTBatch] = iter(loader)
        self._queue: queue.Queue[BarGPTBatch | _ProducerFailure | object] = queue.Queue(
            maxsize=int(capacity_batches)
        )
        self._capacity = int(capacity_batches)
        self._stop = threading.Event()
        self._ended = False
        self._produced = 0
        self._consumed = 0
        self._maximum_fill = 0
        self._thread = threading.Thread(target=self._produce, name="bar-gpt-host-cache", daemon=True)
        self._thread.start()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def fill(self) -> int:
        return self._queue.qsize()

    @property
    def maximum_fill(self) -> int:
        return self._maximum_fill

    @property
    def produced(self) -> int:
        return self._produced

    @property
    def consumed(self) -> int:
        return self._consumed

    def _put(self, item: BarGPTBatch | _ProducerFailure | object) -> bool:
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=0.1)
                self._maximum_fill = max(self._maximum_fill, self._queue.qsize())
                return True
            except queue.Full:
                continue
        return False

    def _produce(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    item = next(self._iterator)
                except StopIteration:
                    self._put(_END)
                    return
                self._produced += 1
                if not self._put(item):
                    return
        except BaseException as exc:
            self._put(_ProducerFailure(exc))

    def get(self, *, block: bool) -> BarGPTBatch | None:
        if self._ended:
            raise StopIteration
        try:
            item = self._queue.get(block=block)
        except queue.Empty:
            return None
        if item is _END:
            self._ended = True
            raise StopIteration
        if isinstance(item, _ProducerFailure):
            self._ended = True
            raise item.error
        self._consumed += 1
        return item

    def close(self) -> None:
        self._stop.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        # PyTorch exposes no public iterator-cancellation API. Its iterator
        # owns the worker processes and their outstanding ClickHouse reads, so
        # use the guarded iterator shutdown hook before validation starts a new
        # bounded worker pool. Merely stopping this producer thread leaves
        # persistent workers querying in the background.
        _shutdown_loader_iterator(self._iterator)
        self._thread.join(timeout=30.0)
        if self._thread.is_alive():
            raise RuntimeError("host batch-cache producer did not stop after DataLoader shutdown")


class DeviceBatchPrefetcher:
    """Bounded host cache plus one-batch asynchronous CUDA staging."""

    def __init__(
        self,
        loader: Any,
        device: torch.device,
        *,
        enabled: bool,
        host_cache_batches: int = 0,
    ) -> None:
        self.device = device
        self.enabled = bool(enabled and device.type == "cuda")
        self.stream = torch.cuda.Stream(device=device) if self.enabled else None
        self.host_cache = HostBatchCache(loader, capacity_batches=host_cache_batches) if host_cache_batches else None
        self.iterator: Iterator[BarGPTBatch] | None = None if self.host_cache is not None else iter(loader)
        self._next_batch: BarGPTBatch | None = None
        self._next_raw: BarGPTBatch | None = None
        self._exhausted = False
        first = self._raw(block=True)
        if first is not None:
            self._stage(first)

    @property
    def cache_fill(self) -> int:
        return self.host_cache.fill if self.host_cache is not None else 0

    @property
    def cache_capacity(self) -> int:
        return self.host_cache.capacity if self.host_cache is not None else 0

    def _raw(self, *, block: bool) -> BarGPTBatch | None:
        if self._exhausted:
            return None
        try:
            if self.host_cache is not None:
                return self.host_cache.get(block=block)
            if not block:
                return None
            assert self.iterator is not None
            return next(self.iterator)
        except StopIteration:
            self._exhausted = True
            return None

    def _stage(self, raw: BarGPTBatch) -> None:
        self._next_raw = raw
        if self.stream is None:
            self._next_batch = raw.to(self.device)
            return
        with torch.cuda.stream(self.stream):
            self._next_batch = raw.to(self.device, non_blocking=True)

    def next(self) -> tuple[BarGPTBatch, float]:
        started = time.perf_counter()
        if self._next_batch is None:
            raw = self._raw(block=True)
            if raw is None:
                raise StopIteration
            self._stage(raw)
        if self.stream is not None:
            current = torch.cuda.current_stream(self.device)
            current.wait_stream(self.stream)
            self._next_batch.record_stream(current)
        batch = self._next_batch
        self._next_batch = None
        self._next_raw = None

        # Never hold an already-ready batch behind a slow producer. Stage the
        # successor only when it is immediately available; the RAM producer
        # continues filling while the GPU computes the returned batch.
        raw = self._raw(block=False)
        if raw is not None:
            self._stage(raw)
        return batch, time.perf_counter() - started

    def close(self) -> None:
        if self.host_cache is not None:
            self.host_cache.close()
        else:
            _shutdown_loader_iterator(self.iterator)
            self.iterator = None

    def __enter__(self) -> "DeviceBatchPrefetcher":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def __iter__(self) -> "DeviceBatchPrefetcher":
        return self

    def __next__(self) -> BarGPTBatch:
        return self.next()[0]
