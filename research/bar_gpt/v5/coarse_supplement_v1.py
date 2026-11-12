"""Append-only phase-one coarse windows; never attaches to a running trainer.

One CPU lane owns a ticker's complete chronology. Small, whole-session canonical
queries populate a receipt-sealed UNADJUSTED bar cache once; vectorized windows
are packed only after parent anti-join and overlap selection. No SQLite, random
resampling, synthetic bars, alternate historical source or automatic activation.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import threading
import time

import numpy as np
import psutil

from . import bar_semantics_v1, quote_quality_v1
from .cohort_v1 import members
from .quote_rebuild_v1 import session_batches
from .quote_repair_v1 import require_complete, safe_file, verify_seal
from .shards import source
from .shards.balanced_plan import ticker_bounds
from .shards.origin_samples import arrays
from .shards.phase1_contract import FRAMES, SAMPLE_DTYPE, VERSION, digest, file_hash, pack, write_json
from .shards.range_sql import range_query
from .shards.sql import clock
from .shards.storage import BAR_DTYPE, Budget, adjust_splits, publish, verify_receipt

COARSE = FRAMES[-2:]
FORMAT = "bargpt-phase1-coarse-supplement-v1"
BAR = np.dtype([("values", "<f4", (18,)), ("valid", "u1", (18,)), ("end_us", "<i8")])
KEY = np.dtype([("first", "<i8"), ("last", "<i8")])


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sealed(path, key="hash"):
    value = read(path)
    if key == "binding":
        if value["binding"] != digest(value["contract"]):
            raise ValueError("Contract seal mismatch")
    else:
        verify_seal(value, key)
    return value


@contextmanager
def writer_lease(output):
    """OS-released exclusive writer lease; a stale PID never requires deletion."""
    with (Path(output) / "writer.lock").open("a+b") as handle:
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_json(Path(output) / "owner.json", dict(pid=os.getpid(), created=psutil.Process().create_time()))
        yield


def atomic_array(path, data):
    """Publish a new cache file; never overwrite sealed files or source shards."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("wb") as handle:
        np.save(handle, data, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return dict(name=path.name, bytes=path.stat().st_size, sha256=file_hash(path))


def window_keys(ends):
    keys = np.empty(len(ends), dtype=KEY)
    keys["first"], keys["last"] = ends[:, 0], ends[:, -1]
    return keys


def split_masks(ends, frame, cutoff):
    """Same full-context chronological purge as the active split, not old CUTOFF."""
    return ends[:, -1] < cutoff, ends[:, 0] - frame >= cutoff


def spaced(starts, distance):
    """Greedy chronological interval scheduling; loops over selected windows only."""
    starts = np.asarray(starts, dtype=np.int64)
    result, offset = [], 0
    while offset < len(starts):
        first = int(starts[offset])
        result.append(first)
        offset = int(np.searchsorted(starts, first + distance))
    return np.asarray(result, dtype=np.int64)


def choose(starts, parent_covered, *, allow_half_overlap=True):
    """Prioritize windows of unseen bars, then novel windows with shared context.

    Each tier first chooses disjoint windows, then fills eligible half-overlap
    gaps. The hard <=32/64 common bars applies BETWEEN NEW windows only. Parent
    exact identities were already removed; parent context overlap is permitted.
    Returns priority order (not time order) so quota clipping preserves priority.
    """
    starts = np.asarray(starts, dtype=np.int64)
    if len(starts) and np.any(np.diff(starts) <= 0):
        raise ValueError("Candidate starts must be unique and chronological")
    covered = np.r_[0, np.cumsum(parent_covered, dtype=np.int64)]
    unseen = covered[starts + 64] == covered[starts]
    chosen = np.empty(0, dtype=np.int64)
    order = []
    for tier in (starts[unseen], starts[~unseen]):
        for distance in ((64, 32) if allow_half_overlap else (64,)):
            eligible = tier
            if len(chosen):
                indices = np.searchsorted(chosen, eligible)
                left = chosen[np.maximum(indices - 1, 0)]
                right = chosen[np.minimum(indices, len(chosen) - 1)]
                eligible = eligible[(np.abs(eligible-left) >= distance) & (np.abs(eligible-right) >= distance)]
            extra = spaced(eligible, distance)
            order.extend(extra.tolist())
            chosen = np.sort(np.r_[chosen, extra])
    return np.asarray(order, dtype=np.int64)


def describe(cohort, split_path, configuration):
    """Freeze metadata only. No ClickHouse calls, cohort edits or output writes."""
    split = sealed(split_path, "manifest_hash")
    cohort = Path(cohort).resolve()
    cohort_hash, parents = members(cohort, require_all=True)
    if cohort_hash is None or split.get("cohort_hash") != cohort_hash:
        raise ValueError("Require the exact released cohort bound by the active split")
    contracts = [sealed(root / "contract.json", "binding") for _, root in parents]
    base = parents[0][1]
    contract = contracts[0]["contract"]
    for wrapper in contracts:
        body = wrapper["contract"]
        if (body["version"] != VERSION or body["authority"] != contract["authority"]
                or body.get("quote_quality") != quote_quality_v1.POLICY
                or body.get("quote_semantics") != bar_semantics_v1.CONTRACT):
            raise ValueError("All parents must use the current corrected phase-one contract")
    expected = [dict(name=row["name"], path=str(Path(row["path"]).resolve()), binding=row["binding"])
                for row in split["dataset_members"]]
    actual = [dict(name=name, path=str(root), binding=item["binding"]) for (name, root), item in zip(parents, contracts)]
    if expected != actual:
        raise ValueError("Split parent membership differs")
    plan = sealed(base / "plan/manifest.json")
    if plan["hash"] != contract["plan_hash"]:
        raise ValueError("Frozen ordinal plan differs")
    counts = {name: Counter() for name in ("train", "validation")}
    for name, rows in split["splits"].items():
        for row in rows:
            frames = [int(f) for f, n in row["frames"].items() if n]
            if len(frames) != 1 or not 0 <= row["start"] <= row["stop"] <= row["rows"]:
                raise ValueError("Cannot prove a split entry's single timeframe")
            counts[name][frames[0]] += row["stop"] - row["start"]
        if sum(counts[name].values()) != split["counts"][name]:
            raise ValueError("Split count reconciliation failed")
    targets = {name: {str(f): max(0, max(counts[name][v] for v in FRAMES[:4])-counts[name][f])
                      for f in COARSE} for name in counts}
    code = {name: file_hash(Path(__file__).parent / name) for name in (
        "coarse_supplement_v1.py", "shards/range_sql.py", "shards/origin_samples.py",
        "shards/phase1_contract.py", "shards/storage.py", "shards/source.py", "shards/sql.py",
        "shards/balanced_plan.py", "quote_rebuild_v1.py", "run_coarse_supplement_v1.py",
        "bar_semantics_v1.py", "quote_quality_v1.py")}
    body = dict(version=VERSION, component=FORMAT, training_ready=False,
                parent_binding=contracts[0]["binding"], parents=actual,
                split_hash=split["manifest_hash"], cutoff_us=split["cutoff_us"],
                cutoff_day=split["cutoff_day"], plan_hash=plan["hash"],
                authority=contract["authority"], quote_quality=quote_quality_v1.POLICY,
                quote_semantics=bar_semantics_v1.CONTRACT, configuration=configuration,
                parent_counts={k: {str(f): n for f, n in v.items()} for k, v in counts.items()}, targets=targets, code=code,
                overlap="new windows only; <=32 of 64 bars; exact parent duplicates excluded",
                split_policy="frozen cutoff, full-context purge; original validation retained separately")
    return dict(contract=body, binding=digest(body)), plan, parents


class Resources:
    """Bound per-query reservations without changing global server settings."""

    def __init__(self, output, stop, memory_gib=1, reserve_gib=64, disk_gib=150):
        self.output, self.stop = output, stop
        self.query = int(memory_gib * 1024**3)
        self.reserve = int(reserve_gib * 1024**3)
        self.disk = int(disk_gib * 1024**3)
        self.lock, self.pending = threading.Lock(), 0

    @contextmanager
    def admit(self):
        while True:
            if self.stop.is_set() or (self.output / "STOP").exists():
                raise InterruptedError("Supplement STOP; no trainer signals sent")
            if shutil.disk_usage(self.output).free < self.disk:
                raise RuntimeError("Supplement disk reserve reached")
            with self.lock:
                if psutil.virtual_memory().available >= self.reserve + self.pending + self.query:
                    self.pending += self.query
                    break
            self.stop.wait(.5)
        try:
            yield
        finally:
            with self.lock:
                self.pending -= self.query


class Progress:
    def __init__(self, output, total, workers):
        self.output, self.lock = output, threading.RLock()
        self.data = dict(state="running", total=total, completed=0, failed=0, active={},
                         saved={}, queries=0, retries=0, workers=workers)

    def update(self, ticker=None, **fields):
        with self.lock:
            if ticker is None:
                self.data.update(fields)
            else:
                self.data["active"][ticker] = fields
            write_json(self.output / "progress.json", dict(self.data, updated=time.time()))

    def increment(self, key):
        with self.lock:
            self.data[key] += 1


def parent_index(parents, job, frame, bars):
    """Read/hash each overlapping coarse parent once per ticker; bounded by ticker.

    All parent clocks must be consecutive in this same canonical observed-bar
    sequence. This also proves first/last identity cannot hide different interiors.
    Parent window payloads are not retained; only sorted endpoints and coverage.
    """
    starts, coverage = [], np.zeros(len(bars), dtype=np.int64)
    ends = bars["end_us"]
    for _, root in parents:
        binding = sealed(root / "contract.json", "binding")["binding"]
        for year in job["years"]:
            folder = root / "jobs" / f"{year['year']}-{job['key']}"
            for path in sorted(folder.glob("coarse-*/receipt.json")):
                receipt = verify_receipt(path.parent, hashes=False)
                if receipt["binding"] != binding:
                    raise ValueError("Parent publication binding differs")
                if not receipt["frames"].get(str(frame), 0):
                    continue
                for item in receipt["files"]:
                    if not item["name"].startswith("samples-"):
                        continue
                    payload = safe_file(path.parent, item["name"])
                    if file_hash(payload) != item["sha256"]:
                        raise ValueError("Parent payload SHA256 differs")
                    old = np.load(payload, mmap_mode="r", allow_pickle=False)
                    try:
                        if old.dtype != SAMPLE_DTYPE:
                            raise ValueError("Parent sample schema differs")
                        clocks = old["end_us"][old["timeframe_us"] == frame]
                        positions = np.searchsorted(ends, clocks[:, 0])
                        if len(positions) and (np.any(positions + 63 >= len(ends)) or
                                not np.array_equal(ends[positions[:, None] + np.arange(64)], clocks)):
                            raise ValueError("Parent coarse clocks differ from frozen canonical sequence")
                        starts.append(positions)
                        np.add.at(coverage, positions, 1)
                        stops = positions + 64
                        np.add.at(coverage, stops[stops < len(ends)], -1)
                    finally:
                        old._mmap.close()
    known = np.unique(np.concatenate(starts)) if starts else np.empty(0, dtype=np.int64)
    return known, np.cumsum(coverage) > 0


def cache_ticker(job, base, output, contract, transport, resources, progress, stop):
    """Whole sessions remain intact; persistent pages avoid replaying warmup queries."""
    bounds = ticker_bounds(base, job)
    cfg = contract["contract"]["configuration"]
    pages = session_batches(bounds, max_events=cfg["chunk_events"], max_days=cfg["chunk_days"])
    directory = output / "bar-cache" / job["key"]
    directory.mkdir(parents=True, exist_ok=True)
    collected = {f: [] for f in COARSE}
    accumulated = Counter()
    for number, group in enumerate(pages):
        if stop.is_set() or (output / "STOP").exists():
            raise InterruptedError("Supplement stopped")
        identity = digest([contract["binding"], job, group, "unadjusted-coarse"])
        receipt_path = directory / f"{number:05d}.json"
        progress.update(job["ticker"], stage="coarse query/cache", page=number, pages=len(pages))
        if receipt_path.exists():
            receipt = sealed(receipt_path)
            if receipt["identity"] != identity:
                raise ValueError("Unadjusted bar-cache source binding differs")
        else:
            statement = range_query(job["ticker"], bounds, contract["contract"]["authority"]["rules"],
                                    int(group[0]["first_ordinal"]), int(group[-1]["next_ordinal"]),
                                    begin_us=clock(group[0]["source_date"], 4),
                                    end_us=clock(group[-1]["source_date"], 20))
            # Transport errors retry the identical SQL. Query/memory failures are
            # explicit engineering failures: never truncate a dense session.
            for attempt in range(3):
                try:
                    with resources.admit():
                        frame = source.arrow(transport, statement, "bargpt-coarse-"+identity[:24], allow_empty=True)
                    progress.increment("queries")
                    break
                except (ConnectionError, OSError, TimeoutError):
                    if attempt == 2:
                        raise
                    progress.increment("retries")
                    if stop.wait(2**attempt):
                        raise InterruptedError("Stopped during transport retry")
            items = {}
            for resolution in COARSE:
                rows = frame.filter(frame["resolution"] == resolution)
                data = np.empty(len(rows), dtype=BAR)
                if len(rows):
                    data["values"], data["valid"], data["end_us"] = arrays(rows)
                target = directory / f"{number:05d}-{resolution}.npy"
                # An unsealed file belongs only to this deterministic page.
                # Reuse exact bytes after a crash; reject a different orphan.
                if target.exists():
                    old = np.load(target, allow_pickle=False)
                    if old.dtype != BAR or old.tobytes() != data.tobytes():
                        raise ValueError("Unsealed cache file differs on replay")
                    items[str(resolution)] = dict(name=target.name, bytes=target.stat().st_size, sha256=file_hash(target))
                else:
                    items[str(resolution)] = atomic_array(target, data)
            receipt = dict(identity=identity, files=items, query_sha256=digest(statement))
            receipt["hash"] = digest(receipt)
            write_json(receipt_path, receipt)
        for resolution in COARSE:
            item = receipt["files"][str(resolution)]
            path = safe_file(directory, item["name"])
            if path.stat().st_size != item["bytes"] or file_hash(path) != item["sha256"]:
                raise ValueError("Unadjusted bar-cache integrity failed")
            data = np.load(path, allow_pickle=False)
            if data.dtype != BAR or data.ndim != 1:
                raise ValueError("Invalid unadjusted cache schema")
            accumulated[resolution] += len(data)
            if accumulated[resolution] > cfg["max_ticker_bars"]:
                raise ValueError("Coarse cache exceeds bounded ticker memory")
            collected[resolution].append(data)
    result = {f: np.concatenate(parts) if parts else np.empty(0, dtype=BAR) for f, parts in collected.items()}
    for bars in result.values():
        if len(bars) > cfg["max_ticker_bars"] or np.any(np.diff(bars["end_us"]) <= 0):
            raise ValueError("Coarse cache exceeds ticker bound or clocks are unordered")
    return result


def candidates(job, parents, cached, cutoff):
    result, counts = {}, {}
    for frame in COARSE:
        bars = cached[frame]
        known, coverage = parent_index(parents, job, frame, bars)
        starts = np.arange(max(0, len(bars)-63), dtype=np.int64)
        starts = starts[~np.isin(starts, known, assume_unique=True)]
        ends = bars["end_us"]
        train = ends[starts+63] < cutoff
        validation = ends[starts]-frame >= cutoff
        counts[str(frame)] = dict(candidate=len(starts), purged=int((~train & ~validation).sum()), parent_windows=len(known))
        for name, mask in (("train", train), ("validation", validation)):
            result[name, frame] = choose(starts[mask], coverage)
    return result, counts


def build_job(job, base, parents, output, contract, resources, progress, stop, endpoint):
    if stop.is_set():
        raise InterruptedError("Supplement stopped")
    client = source.client(endpoint, contract["contract"]["configuration"]["query_memory_gib"])
    client.default_query_params.update(max_threads=1, max_execution_time=60)
    try:
        cached = cache_ticker(job, base, output, contract, client, resources, progress, stop)
        progress.update(job["ticker"], stage="parent anti-join and overlap selection")
        selection, counts = candidates(job, parents, cached, contract["contract"]["cutoff_us"])
        return job, cached, selection, counts
    except BaseException:
        stop.set()  # Wake sibling lanes before executor shutdown waits for them.
        raise
    finally:
        source.close(client)


def packed(bars, starts, frame, split, actions):
    indices = np.sort(starts)[:, None] + np.arange(64)
    result = pack(bars["values"][indices], bars["valid"][indices], bars["end_us"][indices], frame, split)
    adjust_splits(result, actions)
    return result


def extract(output, contract, plan, parents, *, endpoint=None):
    """Explicit extraction only: deterministic ticker order, bounded futures/I/O.

    Coordinator accepts results in frozen ticker order, so global quota selection
    is independent of worker completion order. No changes to parent/cohort/split.
    """
    output = Path(output)
    cfg = contract["contract"]["configuration"]
    if (output / "STOP").exists():
        raise InterruptedError("Human/owned STOP is never cleared")
    if sealed(output / "contract.json", "binding") != contract:
        raise ValueError("Supplement contract changed")
    base = parents[0][1]
    for item in plan["partitions"]:
        path = safe_file(base / "plan", item["name"])
        if path.stat().st_size != item["bytes"] or file_hash(path) != item["sha256"]:
            raise ValueError("Frozen ordinal partition changed")
    transport = source.client(endpoint, cfg["query_memory_gib"])
    transport.default_query_params.update(max_threads=1, max_execution_time=60)
    try:
        observed = source.preflight(transport)
        authority = contract["contract"]["authority"]
        if any(observed[k] != authority[k] for k in ("schema", "dictionary_hash", "rules")):
            raise ValueError("Canonical source schema/dictionary changed")
    finally:
        source.close(transport)
    stop = threading.Event()
    progress = Progress(output, len(plan["jobs"]), cfg["workers"])
    resources = Resources(output, stop, cfg["query_memory_gib"], cfg["ram_reserve_gib"], cfg["disk_reserve_gib"])
    budget = Budget(cfg["target_bytes"], sum(p.stat().st_size for p in output.rglob("*") if p.is_file()))
    (output / "completed").mkdir(exist_ok=True)
    saved = {name: Counter() for name in ("train", "validation")}
    # Frozen cost order avoids starting with a multi-billion-event alphabetical
    # ticker and holding all completed short ticker futures behind it. Counts
    # are metadata estimates only; every frozen job remains in the plan.
    jobs = sorted(plan["jobs"], key=lambda j: (
        sum(y["days"] / 3 + y["events"] / cfg["chunk_events"] for y in j["years"]),
        j["ticker"], j["key"]))
    actions = {}
    for action in authority["corporate_actions"]["rows"]:
        actions.setdefault(action["provider_ticker"], []).append(action)
    def remaining(name, f):
        return contract["contract"]["targets"][name][str(f)] - saved[name][f]
    # Resume completed ticker receipts without queries or expensive parent scans.
    completed = set()
    for job in jobs:
        done = output / "completed" / (job["key"] + ".json")
        if not done.exists():
            continue
        receipt = sealed(done)
        if receipt["binding"] != contract["binding"]:
            raise ValueError("Ticker completion belongs to another supplement")
        for name, frames in receipt["counts"].items():
            for f, count in frames.items():
                directory = output / "jobs" / job["key"] / f"{name}-{f}"
                published = verify_receipt(directory, hashes=True)
                if published is None or published["binding"] != contract["binding"] or published["samples"] != count:
                    raise ValueError("Completed ticker publication differs")
                saved[name][int(f)] += count
        completed.add(job["key"])
    progress.data["completed"] = len(completed)
    queue = [j for j in jobs if j["key"] not in completed]
    cpu = ThreadPoolExecutor(max_workers=cfg["workers"])
    disk = ThreadPoolExecutor(max_workers=cfg["io_workers"])
    try:
        pending, cursor = [], 0
        while cursor < len(queue) or pending:
            while cursor < len(queue) and len(pending) < cfg["workers"]:
                j = queue[cursor]; cursor += 1
                pending.append(cpu.submit(build_job, j, base, parents, output, contract, resources, progress, stop, endpoint))
            if not pending:
                break
            job, cached, selected, diagnostics = pending.pop(0).result()
            if (output / "STOP").exists():
                raise InterruptedError("Supplement STOP")
            publications, counts = [], {name: {} for name in saved}
            for name in saved:
                for frame in COARSE:
                    starts = selected[name, frame][:max(0, remaining(name, frame))]
                    directory = output / "jobs" / job["key"] / f"{name}-{frame}"
                    identity = digest([contract["binding"], job["key"], name, frame])
                    prior = verify_receipt(directory, identity, hashes=True)
                    if prior is not None:
                        if (prior["samples"] != len(starts)
                                or prior.get("selection_sha256") != digest(starts.tolist())):
                            raise ValueError("Partial ticker resume quota selection differs")
                    if not len(starts):
                        continue
                    # Gather only accepted windows; raw cache is never adjusted.
                    data = packed(cached[frame], starts, frame, int(name == "validation"), actions.get(job["ticker"], []))
                    metadata = dict(identity=identity, binding=contract["binding"], ticker=job["ticker"],
                                    unit=f"{name}-{frame}", split_name=name, parent_dedup_checked=True,
                                    cutoff_us=contract["contract"]["cutoff_us"], maximum_new_overlap_bars=32,
                                    selection_sha256=digest(starts.tolist()))
                    publications.append(disk.submit(publish, directory, data, np.empty(0, dtype=BAR_DTYPE), metadata, budget, 32*1024**2))
                    counts[name][str(frame)] = len(starts)
                    saved[name][frame] += len(starts)
            for future in publications:
                receipt = future.result()
                if receipt is None:
                    raise RuntimeError("Supplement hard byte budget exhausted; no silent quota skip")
            done = dict(binding=contract["binding"], ticker=job["ticker"], counts=counts, diagnostics=diagnostics)
            done["hash"] = digest(done)
            write_json(output / "completed" / (job["key"] + ".json"), done)
            with progress.lock:
                progress.data["active"].pop(job["ticker"], None)
                progress.data["completed"] += 1
            progress.update(saved={k: dict(v) for k, v in saved.items()})
            if not any(remaining(name, f) > 0 for name in saved for f in COARSE):
                stop.set()
                for future in pending:
                    future.cancel()
                break
        deficits = {name: {str(f): max(0, remaining(name, f)) for f in COARSE} for name in saved}
        state = "quotas_reached" if not any(v for row in deficits.values() for v in row.values()) else "inventory_exhausted"
        result = dict(state=state, failed=0, training_ready=False, binding=contract["binding"],
                      saved={k: dict(v) for k, v in saved.items()}, shortfall=deficits,
                      activation="not attached; separate full audit and explicit trainer transition required")
        result["hash"] = digest(result)
        write_json(output / "result.json", result)
        progress.update(state=state, shortfall=deficits)
        return result
    except BaseException as error:
        stop.set()
        progress.update(state="stopped" if isinstance(error, InterruptedError) else "failed", failed=1, error=str(error))
        raise
    finally:
        stop.set()
        cpu.shutdown(wait=True, cancel_futures=True)
        disk.shutdown(wait=True, cancel_futures=True)
