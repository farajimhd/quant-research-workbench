"""CPU-only extraction processes; only small sealed receipts cross IPC.

Each process reuses private ClickHouse transports and a bounded lane-metadata
cache. It runs the unchanged Worker/bar contract and publishes NPY/NPZ directly.
A shared semaphore bounds writers; a shared cancel event drains owned work.
"""

from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
import json
import multiprocessing as mp
from multiprocessing.util import Finalize
from pathlib import Path

from .producer import Gate, Worker, NoFullBlock, EndOfStream

_state = None


@dataclass(frozen=True)
class ProducedUnit:
    """Only publication metadata and lane length leave the producing process."""

    receipt: dict
    unit_count: int


class DirectPublisher:
    """Preserve Worker's submit/result contract, with a global I/O slot bound."""

    def __init__(self, slots):
        self.slots = slots

    def submit(self, function, *args, **kwargs):
        """Durably publish in the owning process without copying its bar arrays."""
        with self.slots:
            receipt = function(*args, **kwargs)
        result = Future()
        result.set_result(receipt)
        return result


def initialize(configuration, cancel, slots):
    """Load one frozen plan and resource gate per spawned extraction process."""
    global _state
    root = Path(configuration["output"])
    plan = json.loads((root / "plan.json").read_text())
    gate = Gate(
        root,
        configuration["query_memory_gib"],
        configuration["reserve_memory_gib"],
        configuration["reserve_disk_gib"],
        configuration["query_threads"],
        configuration["fine_warmup_events"],
        configuration["coarse_warmup_events"],
        configuration["fine_warmup_sessions"],
    )
    gate.cancel = cancel
    gate.storage_format = configuration["storage_format"]
    Finalize(None, gate.close, exitpriority=10)
    _state = dict(
        root=root,
        plan=plan,
        gate=gate,
        lanes=OrderedDict(),
        publisher=DirectPublisher(slots),
        configuration=configuration,
    )


def produce(stream, unit, proof):
    """Build/seal one unit using the original SQL, context and publication path."""
    state = _state
    lanes = state["lanes"]
    worker = lanes.pop(stream["key"], None)
    if worker is None:
        config = state["configuration"]
        worker = Worker(
            state["root"],
            state["plan"],
            stream,
            state["gate"],
            config["endpoint"],
            state["publisher"],
            config["chunk_events"],
        )
    lanes[stream["key"]] = worker
    while len(lanes) > 8:
        lanes.popitem(
            last=False
        )  # Resident history is bounded independently of corpus size.
    if proof is not None:
        worker.trade_proof = proof
    result = worker.step(unit)
    if isinstance(result, (NoFullBlock, EndOfStream)):
        return result
    return ProducedUnit(result.result(), len(worker.units))


def executor(args, cancel, slots):
    """Select measured engineering topology without changing any data semantics."""
    if not args.process_workers:
        return ThreadPoolExecutor(args.workers, thread_name_prefix="phase2-query")
    return ProcessPoolExecutor(
        args.process_workers,
        mp_context=mp.get_context("spawn"),
        initializer=initialize,
        initargs=(vars(args), cancel, slots),
    )
