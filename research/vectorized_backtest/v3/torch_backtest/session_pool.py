"""Bounded session residency and captured-runner reuse, entirely local to v3.

Immutable host tapes are the source. A subset can reside on GPU; one padded
working tape owns fixed pointers captured by a runner. Binding copies evidence,
never financial state. Every call resets the independent session account.
"""

from dataclasses import fields
from time import perf_counter

import torch

from .search_objective import SessionObjective
from .tape import SqueezeTape


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
        **runner_options,
    ):
        if not tapes or sum(t.bytes for t in tapes) > maximum_host_gib * 1024**3:
            raise MemoryError("Training tapes exceed explicit host memory budget")
        self.tapes = [t.validate() for t in tapes]
        self.space, self.batch, self.device = space, batch, torch.device(device)
        self.backend, self.options = backend, runner_options
        self.evaluators, self.resident = {}, {}
        self.progress = None
        self.maximum_n = max(len(t.tickers) for t in tapes)
        # Avoid excessive padding for tiny witnesses and unnecessary rounding.
        self.capacity = self.maximum_n
        used = 0
        if self.device.type == "cuda":
            free, total = torch.cuda.mem_get_info(self.device)
            reserve = max(15 * 1024**3, int(total * 0.20))
            budget = min(
                resident_gib * 1024**3,
                max(0, free - reserve - max(t.bytes for t in tapes)),
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
            sessions=len(tapes),
            padded_tickers=self.capacity,
        )

    def evaluate(self, index, rows):
        source = self.resident.get(index, self.tapes[index])
        key = (
            len(source.clocks),
            source.structural_targets is not None,
            source.level_lower.shape[1],
        )
        started = perf_counter()
        if key not in self.evaluators:
            working = padded_tape(source, self.capacity, self.device)
            self.evaluators[key] = SessionObjective(
                working, self.space, self.batch, backend=self.backend, **self.options
            )
        evaluator = self.evaluators[key]
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
        evaluator.progress = self.progress
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
