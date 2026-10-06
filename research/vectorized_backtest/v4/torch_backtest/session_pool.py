"""Bounded session residency and captured-runner reuse, entirely local to v3.

Immutable host tapes are the source. A subset can reside on GPU; one padded
working tape owns fixed pointers captured by a runner. Binding copies evidence,
never financial state. Every call resets the independent session account.
"""

import gc
from collections import OrderedDict
from dataclasses import fields
from time import perf_counter

import torch

from .search_objective import SessionObjective
from .tape import SqueezeTape

INTERVAL_BOOK_FIELDS = frozenset(("level_from", "level_to", "level_lower", "level_resistance"))


def _axis(name, value):
    return 0 if name.startswith("level_") or name == "admission" else 1


def padded_tape(source, listings, device):
    """Only append permanently inactive slots; preserve original ticker order."""
    values = {}
    for field in fields(source):
        name, value = field.name, getattr(source, field.name)
        if not isinstance(value, torch.Tensor):
            values[name] = value
            continue
        if name == "clocks":
            values[name] = value.to(device).clone()
            continue
        if source.structural_targets is not None and name in INTERVAL_BOOK_FIELDS:
            # Streamed 15-target geometry is the execution authority. Retain the
            # full certified interval book on host; no kernel reads it here.
            values[name] = torch.zeros((listings, 1), dtype=value.dtype, device=device)
            continue
        shape = list(value.shape)
        axis = _axis(name, value)
        shape[axis] = listings
        fill = float("inf") if name == "structural_targets" else 0
        target = torch.full(shape, fill, dtype=value.dtype, device=device)
        target.narrow(axis, 0, len(source.tickers)).copy_(value)
        values[name] = target
    # Padding names are metadata only, and sort strictly after real symbols.
    values["tickers"] = source.tickers + tuple(
        "~pad%06d" % i for i in range(listings - len(source.tickers))
    )
    values["provenance"] = dict(source.provenance)
    return SqueezeTape(**values).validate()


def bind_tape(target, source):
    """Copy source fields in place; CUDA graph references remain unchanged."""
    for field in fields(source):
        name, value = field.name, getattr(source, field.name)
        if not isinstance(value, torch.Tensor):
            continue
        out = getattr(target, name)
        if source.structural_targets is not None and name in INTERVAL_BOOK_FIELDS:
            continue
        if name == "clocks":
            out.copy_(value)
        else:
            axis = _axis(name, value)
            n = len(source.tickers)
            out.narrow(axis, 0, n).copy_(value)
            if out.shape[axis] > n:
                fill = float("inf") if name == "structural_targets" else 0
                out.narrow(axis, n, out.shape[axis] - n).fill_(fill)
    target.provenance = dict(source.provenance)
    width = target.close.shape[1]
    target.tickers = source.tickers + tuple(
        "~pad%06d" % i for i in range(width - len(source.tickers))
    )


class SessionPool:
    """One graph per clock/layout shape, not one graph per calendar date.

    GPU residency is a hard budget, not a target. Nonresident tapes remain on
    host and transfer once per session evaluation, never per decision tick.
    """

    def __init__(
        self,
        tapes,
        space,
        batch,
        *,
        device="cuda",
        backend="compiled_graph",
        maximum_host_gib=320,
        resident_gib=48,
        supplier=None,
        prefetch=True,
        capacity=None,
        cycle_prefetch=False,
        **runner_options,
    ):
        if supplier is not None and tapes:
            raise ValueError("Use tapes OR a certified preparation supplier")
        if supplier is None and (not tapes or sum(t.bytes for t in tapes) > maximum_host_gib * 1024**3):
            raise MemoryError("Training tapes exceed explicit host memory budget")
        self.supplier = supplier
        self.tapes = [None] * len(supplier) if supplier is not None else [t.validate() for t in tapes]
        self.space, self.batch, self.device = space, batch, torch.device(device)
        self.backend, self.options = backend, runner_options
        self.evaluators, self.resident = OrderedDict(), {}
        self.progress = None
        self.maximum_n = max((len(t.tickers) for t in tapes), default=0)
        # Avoid excessive padding for tiny witnesses and unnecessary rounding.
        if capacity is not None and (type(capacity) is not int or capacity < self.maximum_n):
            raise ValueError("Explicit capacity must cover the entire known ticker axis")
        self.capacity = self.maximum_n if capacity is None else capacity
        self.prefetch_enabled = prefetch
        self.cycle_prefetch = cycle_prefetch
        self.pending = {}
        self.resident_budget = int(resident_gib * 1024**3)
        self.prefetched_sessions = self.capacity_growths = 0
        self.transfer_wait_seconds = 0.0
        self.transfer_stream = torch.cuda.Stream(device=self.device) if self.device.type == "cuda" else None
        used = 0
        if self.device.type == "cuda":
            free, total = torch.cuda.mem_get_info(self.device)
            reserve = max(15 * 1024**3, int(total * 0.20))
            budget = min(
                resident_gib * 1024**3,
                max(0, free - reserve - max((t.bytes for t in tapes), default=0)),
            )
            for index, tape in enumerate(tapes):
                if used + tape.bytes <= budget:
                    self.resident[index] = tape.to(
                        self.device, maximum_gib=max(1, tape.bytes / 1024**3 + 1)
                    )
                    used += tape.bytes
        self.residency = dict(
            host_gib=sum(t.bytes for t in tapes) / 1024**3,
            gpu_resident_gib=used / 1024**3,
            resident_sessions=len(self.resident),
            sessions=len(self.tapes),
            padded_tickers=self.capacity,
        )

    def prepare(self, index):
        """Wait only for the session about to replay, not the full campaign."""
        if self.tapes[index] is None:
            self.tapes[index] = self.supplier.get(index)
        source = self.tapes[index]
        if len(source.tickers) > self.capacity:
            # Online preparation cannot know final width yet. Small 64-slot
            # buckets bound padding and rebuild only at session boundaries.
            if self.evaluators:
                if self.device.type == "cuda":
                    torch.cuda.synchronize(self.device)
                self.evaluators.clear()
                gc.collect()
                self.capacity_growths += 1
            self.capacity = ((len(source.tickers) + 63) // 64) * 64
        self.residency.update(
            host_gib=sum(t.bytes for t in self.tapes if t is not None) / 1024**3,
            padded_tickers=self.capacity,
            capacity_growths=self.capacity_growths,
        )
        return source

    def _prefetch(self, index):
        """One pinned-host/async-device staging slot, never candidate state."""
        if not self.prefetch_enabled or self.device.type != "cuda" or index >= len(self.tapes):
            return
        if index in self.resident or index in self.pending or self.pending:
            return
        source = self.tapes[index]
        if source is None and self.supplier is not None:
            source = self.supplier.peek(index)
        if source is None:
            return  # Producer queue reports waiting/ready truthfully.
        free, total = torch.cuda.mem_get_info(self.device)
        reserve = max(15 * 1024**3, int(total * .20))
        if source.bytes + reserve > free:
            self.residency["transfer_prefetch_blocked"] = "GPU headroom"
            return
        if self.supplier is not None and not self.supplier.reserve_staging(source.bytes):
            self.residency["transfer_prefetch_blocked"] = "host envelope"
            return
        # Pin only one staging tape, not every retained host tape. Keep this
        # owner alive until the copy event completes; graph capture is over.
        try:
            host = {}
            for field in fields(source):
                value = getattr(source, field.name)
                host[field.name] = value.pin_memory() if isinstance(value, torch.Tensor) else value
            pinned = SqueezeTape(**host)
            with torch.cuda.stream(self.transfer_stream):
                device = SqueezeTape(**{
                    f.name: getattr(pinned, f.name).to(self.device, non_blocking=True)
                    if isinstance(getattr(pinned, f.name), torch.Tensor) else getattr(pinned, f.name)
                    for f in fields(pinned)
                })
                done = torch.cuda.Event()
                done.record(self.transfer_stream)
            self.pending[index] = (device, pinned, done)
        except BaseException:
            self.transfer_stream.synchronize()
            if self.supplier is not None:
                self.supplier.release_staging(source.bytes)
            raise
        self.prefetched_sessions += 1
        self.residency.update(prefetched_sessions=self.prefetched_sessions,
                              transfer_prefetch_blocked=None)

    def _source(self, index):
        if index in self.resident:
            return self.resident[index]
        if index not in self.pending:
            return self.tapes[index]
        device, pinned, done = self.pending.pop(index)
        started = perf_counter()
        done.synchronize()
        if self.supplier is not None:
            self.supplier.release_staging(pinned.bytes)
        self.transfer_wait_seconds += perf_counter() - started
        device.validate()
        if sum(t.bytes for t in self.resident.values()) + device.bytes <= self.resident_budget:
            self.resident[index] = device
        self.residency.update(
            gpu_resident_gib=sum(t.bytes for t in self.resident.values()) / 1024**3,
            resident_sessions=len(self.resident),
            transfer_wait_seconds=self.transfer_wait_seconds,
        )
        return device

    def close(self):
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        if self.supplier is not None:
            for device, pinned, done in self.pending.values():
                self.supplier.release_staging(pinned.bytes)
        self.pending.clear()
        if self.supplier is not None:
            self.supplier.close()

    def evaluate(self, index, rows):
        self.prepare(index)
        source = self._source(index)
        key = (
            len(source.clocks),
            source.structural_targets is not None,
            1 if source.structural_targets is not None else source.level_lower.shape[1],
        )
        started = perf_counter()
        if key not in self.evaluators:
            if len(self.evaluators) >= 2:
                self.evaluators.popitem(last=False)
                gc.collect()
            working = padded_tape(source, self.capacity, self.device)
            self.evaluators[key] = SessionObjective(
                working, self.space, self.batch, backend=self.backend, **self.options
            )
        evaluator = self.evaluators[key]
        self.evaluators.move_to_end(key)
        bind_tape(evaluator.tape, source)
        for runner in evaluator.runners.values():
            runner.start = int(
                source.provenance.get("start_second", int(source.clocks[0]))
            )
            runner.end = int(source.clocks[-1])
            runner.start_boundary.fill_(runner.start)
            runner.end_boundary.fill_(runner.end)
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        bind_seconds = perf_counter() - started
        next_index = (index + 1) % len(self.tapes) if self.cycle_prefetch else index + 1

        def progress(event):
            # Called outside capture, while graph blocks replay. If the next
            # CPU tape finished after replay began, stage it at this boundary.
            self._prefetch(next_index)
            if self.progress:
                self.progress(dict(event, **self.residency))

        evaluator.progress = progress
        evaluator.before_replay = lambda: self._prefetch(next_index)
        result = evaluator(rows)
        result.update(
            session=source.provenance.get("session", str(index)),
            source_fingerprint=source.provenance["fingerprint"],
            bind_seconds=bind_seconds,
        )
        return result

    def objectives(self):
        return [BoundSession(self, i) for i in range(len(self.tapes))]


class BoundSession:
    def __init__(self, pool, index):
        self.pool, self.index, self.progress = pool, index, None

    def __call__(self, rows):
        self.pool.progress = self.progress
        return self.pool.evaluate(self.index, rows)

    def prepare(self):
        return self.pool.prepare(self.index)
