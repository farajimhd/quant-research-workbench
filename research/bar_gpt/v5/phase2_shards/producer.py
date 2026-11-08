"""Bounded CPU/ClickHouse extraction lanes, rolling warmup and async publication."""

from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time

import numpy as np
import psutil
import polars as pl

from . import sql, storage
from .contract import BAR, SPECS, BLOCK, digest
from .plan import bounds, ranges
from ..shards import source
from ..shards.origin_samples import arrays as unpack
from ..shards.range_sql import event_source


@dataclass(frozen=True)
class NoFullBlock:
    """Auditable source-event upper bound proving a stream cannot yield a block."""

    proof: dict


class Gate:
    """Reserve query memory before admission, preserving training/OS headroom.

    Available physical memory includes the running trainer's workers. Queries
    reserve a bounded server allowance plus one Arrow response. No CUDA/Torch
    import or GPU allocation is permitted in this generator.
    """

    def __init__(
        self,
        root,
        memory_gib=3,
        reserve_gib=96,
        disk_gib=150,
        query_threads=2,
        fine_warmup_events=250_000,
        coarse_warmup_events=32_000_000,
    ):
        self.root, self.memory = root, int(memory_gib * 1024**3)
        self.reserve, self.disk = int(reserve_gib * 1024**3), int(disk_gib * 1024**3)
        self.lock, self.reserved = threading.Lock(), 0
        self.cancel = threading.Event()
        self.waiting = {}
        self.query_threads = query_threads
        self.fine_warmup_events, self.coarse_warmup_events = (
            fine_warmup_events,
            coarse_warmup_events,
        )
        self.local, self.clients = threading.local(), []

    def transport(self, endpoint):
        """Reuse private worker-thread connections, never share a live socket."""
        client = getattr(self.local, "client", None)
        if client is None:
            client = source.client(endpoint, self.memory / 1024**3)
            client.default_query_params["max_threads"] = self.query_threads
            self.local.client = client
            with self.lock:
                self.clients.append(client)
        return client

    def close(self):
        """Close transports only after the query pool has drained."""
        for client in self.clients:
            source.close(client)
        self.clients.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def acquire(self):
        """Wait interruptibly; low capacity pauses work rather than harming training."""
        import shutil
        from pathlib import Path

        ident = threading.get_ident()
        while not self.cancel.is_set() and not (Path(self.root) / "STOP").exists():
            with self.lock:
                free = psutil.virtual_memory().available
                disk = shutil.disk_usage(self.root).free
                need = self.memory + 256 * 1024**2
                if (
                    free - self.reserved - need >= self.reserve
                    and disk >= self.disk + 256 * 1024**2
                ):
                    self.reserved += need
                    self.waiting.pop(ident, None)
                    return need
                self.waiting[ident] = (
                    "waiting_memory"
                    if free - self.reserved - need < self.reserve
                    else "waiting_disk"
                )
            self.cancel.wait(2)
        raise InterruptedError("Graceful stop requested before query admission")

    def release(self, amount):
        with self.lock:
            self.reserved -= amount


class Worker:
    """One ticker-year lane; state is restored from the last sealed unit.

    A logical fourteen-day range is subdivided only at complete source-session
    boundaries to bound SQL aggregation memory. Small sessions are coalesced up
    to an event budget; larger sessions stay intact. Context never resets at a
    subdivision/range boundary. Next-range reads overlap the previous I/O task.
    """

    def __init__(self, root, plan, stream, gate, endpoint, io_pool, chunk_events):
        self.root, self.plan, self.stream = root, plan, stream
        execution = Path(root) / "execution.json"
        self.revision = (
            json.loads(execution.read_text())["source_revision"]
            if execution.exists()
            else plan["revision"]
        )
        self.gate, self.endpoint, self.io = gate, endpoint, io_pool
        self.rows = bounds(root, plan, stream)
        self.units = []
        # Partitioning is deterministic and part of the execution contract.
        for group in ranges(self.rows, stream["year"]):
            chunk, count = [], 0
            for row in group:
                if chunk and count + int(row["event_count"]) > chunk_events:
                    self.units.append(chunk)
                    chunk, count = [], 0
                chunk.append(row)
                count += int(row["event_count"])
            if chunk:
                self.units.append(chunk)
        self.label = "queued"
        self.query_seconds = 0.0
        self.query_count = 0
        self.trade_proof = None

    def raw_trade_bound(self):
        """Count ALL raw trade messages in the frozen core year, conditions aside.

        One observed 1s bar needs at least one trade. This is solely a safe upper
        bound on 256-decision blocks, not a validity/reporting-status filter.
        ClickHouse reads its compact event_meta column, using ticker/ordinal keys.
        """
        if self.trade_proof is not None:
            return self.trade_proof
        year = self.stream["years"][0]
        first, stop = int(year["first_ordinal"]), int(year["next_ordinal"])
        reserved = self.gate.acquire()
        try:
            transport = self.gate.transport(self.endpoint)
            sql_text = (
                "SELECT count() raw_events,countIf(bitAnd(event_meta,1)=1) raw_trade_events FROM ("
                + event_source(self.stream["ticker"], self.rows, first, stop)
                + ") FORMAT JSONEachRow"
            )
            row = json.loads(
                transport.execute(
                    sql_text, query_id="bargpt-p2-check-" + self.stream["key"]
                )
            )
            if int(row["raw_events"]) != int(year["events"]):
                raise ValueError(
                    "Canonical source count differs from the frozen ordinal inventory"
                )
        finally:
            self.gate.release(reserved)
        proof = dict(
            plan=self.plan["hash"],
            stream=self.stream["key"],
            ticker=self.stream["ticker"],
            year=self.stream["year"],
            first_ordinal=first,
            next_ordinal=stop,
            raw_trade_events=int(row["raw_trade_events"]),
            raw_events=int(row["raw_events"]),
            method="countIf(bitAnd(event_meta,1)=1); no validity or reporting filter",
        )
        proof["hash"] = digest(proof)
        self.trade_proof = proof
        return proof

    def fetch(self, rows, coarse):
        """One private transport per call, final Arrow only; bounded retries."""
        reserved = self.gate.acquire()
        started = time.monotonic()
        try:
            transport = self.gate.transport(self.endpoint)
            statement = sql.query(
                self.stream["ticker"], rows, self.plan["authority"]["rules"], coarse
            )
            for attempt in range(3):
                try:
                    result = source.arrow(
                        transport,
                        statement,
                        f"bargpt-p2-{self.stream['key']}-{rows[0]['source_date']}-{int(coarse)}-{attempt}",
                        allow_empty=True,
                    )
                    break
                except (ConnectionError, OSError):
                    if attempt == 2:
                        raise
                    self.gate.cancel.wait(attempt + 1)
            # Fine SQL can infer UInt32 for its resolution literal, whereas
            # hour/day need UInt64. Normalize metadata before comparing all five
            # frame IDs; never cast a daily ID into a fine response's UInt32.
            result = result.with_columns(pl.col("resolution").cast(pl.Int64))
            output = []
            for frame, _, _ in SPECS:
                part = result.filter(result["resolution"] == frame)
                packed = np.empty(part.height, dtype=BAR)
                if part.height:
                    packed["values"], packed["valid"], packed["end_us"] = unpack(part)
                output.append(packed)
            return output
        finally:
            self.query_seconds += time.monotonic() - started
            self.query_count += 1
            self.gate.release(reserved)

    def warmup(self):
        """Read disjoint preceding sessions until every frame has its history.

        Fine and coarse reads have separate bounded algorithms. Raw histories
        are retained once per independent ticker-year stream; adjoining ranges
        use the checkpoint and never refetch their preceding contexts.
        """
        first = self.units[0][0]["source_date"]
        prior = [r for r in self.rows if r["source_date"] < first]
        result = [np.empty(0, dtype=BAR) for _ in SPECS]
        for coarse, indices in ((False, range(3)), (True, range(3, 5))):
            position = len(prior)
            while position and any(len(result[i]) < SPECS[i][1] for i in indices):
                # Fine histories used to issue one query per prior session,
                # sometimes hundreds for sparse tickers. Coalesce complete
                # sessions while keeping fine-result and aggregation bounds.
                start, events = position - 1, int(prior[position - 1]["event_count"])
                budget = (
                    self.gate.coarse_warmup_events
                    if coarse
                    else self.gate.fine_warmup_events
                )
                max_sessions = 120 if coarse else 14
                while (
                    start
                    and position - start < max_sessions
                    and events + int(prior[start - 1]["event_count"]) <= budget
                ):
                    start -= 1
                    events += int(prior[start]["event_count"])
                part = self.fetch(prior[start:position], coarse)
                for i in indices:
                    result[i] = np.concatenate((part[i], result[i]))[-SPECS[i][1] :]
                position = start
        return result

    def step(self, unit):
        """Build one durable unit; publication runs on the bounded I/O pool."""
        from .contract import Context

        context = storage.restore(self.root, self.stream, unit - 1)
        started = time.monotonic()
        self.query_seconds = 0
        self.query_count = 0
        rows = self.units[unit]
        self.label = f"{self.stream['ticker']} {rows[0]['source_date']}..{rows[-1]['source_date']}"
        proof = self.raw_trade_bound()
        if proof["raw_trade_events"] < BLOCK:
            return NoFullBlock(proof)
        # Discover current evidence BEFORE paying for up to years of sparse
        # warmup. An all-empty range cannot contribute to any frame's context.
        fine, coarse = self.fetch(rows, False), self.fetch(rows, True)
        arrays = [fine[i] if i < 3 else coarse[i] for i in range(5)]
        bootstrap = [np.empty(0, dtype=BAR) for _ in SPECS]
        if not context.initialized and any(len(a) for a in arrays):
            bootstrap = self.warmup()
            context.append(bootstrap, decisions=False)
            context.initialized = True
        starts = [n - len(b) for n, b in zip(context.counts, bootstrap)]
        blocks, splits = context.append(arrays)
        # Bootstrap and newly fetched bars are one ordered stream. The persisted
        # context contains timestamps only, keeping resident memory very small.
        saved = [np.concatenate((b, a)) for b, a in zip(bootstrap, arrays)]
        timing = dict(
            plan=self.plan["hash"],
            producer_revision=self.revision,
            raw_trade_proof=proof,
            query_seconds=self.query_seconds,
            query_count=self.query_count,
            processing_seconds=time.monotonic() - started,
            source_sessions=len(rows),
            source_events=sum(int(r["event_count"]) for r in rows),
            begin=rows[0]["source_date"],
            end=rows[-1]["source_date"],
            _enqueued_clock=time.monotonic(),
        )
        self.label = "saving " + self.label
        return self.io.submit(
            storage.publish,
            self.root,
            self.stream,
            unit,
            saved,
            starts,
            blocks,
            splits,
            context,
            timing,
        )
