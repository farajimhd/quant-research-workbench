"""Fair, duplicate-free liquid supplement using the unchanged v6 bar mathematics.

Coarse timestamp spans run first. Remaining payload capacity is divided equally
across the four fine frames and eligible ticker-years. A second allocation pass
redistributes unused allowances only after all initial ticker-years were visited.
No bars or redundant windows are materialized by the training loader.
"""

from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
import importlib
import json
import math
from pathlib import Path
import shutil
import threading
import time

import numpy as np

from .liquid_universe_v1 import select_universe
from .shards import balanced_build as bars
from .shards.phase1_contract import (
    FRAMES,
    SAMPLE_DTYPE,
    VERSION,
    digest,
    file_hash,
    seed_for,
    write_json,
)
from .shards.storage import BAR_DTYPE, Budget, publish, verify_receipt

KEY_DTYPE = np.dtype([("first", "<i8"), ("last", "<i8")])


def window_keys(rows):
    """Identify one ticker/frame's consecutive windows by first/last real clocks, [N] records."""
    keys = np.empty(len(rows), dtype=KEY_DTYPE)
    keys["first"], keys["last"] = rows["end_us"][:, 0], rows["end_us"][:, -1]
    return keys


class ParentWindows:
    """Bounded per-ticker receipt index and eight-file LRU for exact parent anti-joins."""

    def __init__(self, root, job, binding):
        self.files, self.cache, self.verified = [], OrderedDict(), set()
        for year in job["years"]:
            folder = root / "jobs" / f"{year['year']}-{job['key']}"
            for path in folder.glob("*/receipt.json"):
                receipt = verify_receipt(path.parent, hashes=False)
                if receipt["binding"] != binding:
                    raise ValueError(
                        "Parent receipt does not belong to the frozen base"
                    )
                frame = next(int(f) for f, n in receipt["frames"].items() if n)
                for item in receipt["files"]:
                    self.files.append((frame, path.parent / item["name"], item))

    def keep(self, data, frame):
        """Return bool [N]; only overlapping parent files are read and hash-verified."""
        keys, duplicates = window_keys(data), np.zeros(len(data), dtype=bool)
        first, last = int(data["end_us"].min()), int(data["end_us"].max())
        for other_frame, path, item in self.files:
            if (
                frame != other_frame
                or item["last_end_us"] < first
                or item["first_start_us"] > last
            ):
                continue
            if path not in self.cache:
                if path not in self.verified:
                    if (
                        path.stat().st_size != item["bytes"]
                        or file_hash(path) != item["sha256"]
                    ):
                        raise ValueError(
                            "Parent payload changed during supplemental extraction"
                        )
                    self.verified.add(path)
                old = np.load(path, mmap_mode="r", allow_pickle=False)
                self.cache[path] = window_keys(old)
                old._mmap.close()
                if len(self.cache) > 8:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(path)
            duplicates |= np.isin(keys, self.cache[path])
        return ~duplicates


class Allocation:
    """Thread-safe global and extraction-slot quotas; all supplement samples are class 1."""

    def __init__(self):
        self.lock = threading.RLock()
        self.saved, self.pending = Counter(), Counter()
        self.frame_saved, self.frame_pending = Counter(), Counter()
        self.targets, self.limits = {}, {}

    def needed(self, slot, frames):
        with self.lock:
            return any(
                self.saved[slot, f] + self.pending[slot, f]
                < self.limits.get((slot, f), 0)
                and self.total(f) + self.reserved(f) < self.targets.get(f, 0)
                for f in frames
            )

    def total(self, frame):
        return self.frame_saved[frame]

    def reserved(self, frame):
        return self.frame_pending[frame]

    def take(self, slot, data, frame):
        """Uniformly select a bounded subset, reserving capacity before asynchronous I/O."""
        with self.lock:
            count = max(
                0,
                min(
                    len(data),
                    self.limits.get((slot, frame), 0)
                    - self.saved[slot, frame]
                    - self.pending[slot, frame],
                    self.targets.get(frame, 0)
                    - self.total(frame)
                    - self.reserved(frame),
                ),
            )
            self.pending[slot, frame] += count
            self.frame_pending[frame] += count
        if count == len(data):
            return data
        chosen = np.random.default_rng(
            seed_for(slot, frame, int(data["end_us"][0, 0]))
        ).choice(len(data), count, replace=False)
        return data[np.sort(chosen)]

    def finish(self, slot, frame, count, accepted):
        with self.lock:
            self.pending[slot, frame] -= count
            self.frame_pending[frame] -= count
            if accepted:
                self.saved[slot, frame] += count
                self.frame_saved[frame] += count

    def snapshot(self):
        with self.lock:
            return dict(
                targets={str(f): self.targets.get(f, 0) for f in FRAMES},
                saved={str(f): [0, self.total(f)] for f in FRAMES},
            )


def fine_jobs(jobs, args):
    """Freeze disjoint ordinal chunks across year boundaries before concurrent dispatch."""
    result = []
    for job in jobs:
        consumed = 0
        maximum = max(int(y["next_ordinal"]) for y in job["years"])
        for year in sorted(job["years"], key=lambda row: row["year"]):
            first, limit = int(year["first_ordinal"]), int(year["next_ordinal"])
            start = max(
                consumed,
                first
                + seed_for(args.seed, job["ticker"], year["year"], "offset")
                % min(args.chunk_events, max(1, limit - first)),
            )
            consumed = min(
                maximum,
                start
                + max(0, math.ceil((limit - start) / args.chunk_events))
                * args.chunk_events,
            )
            if year["liquid"] and start < limit:
                result.append(
                    dict(
                        job=job,
                        year=year["year"],
                        start=start,
                        limit=limit,
                        maximum=maximum,
                        slot=f"fine:{job['ticker']}:{year['year']}",
                    )
                )
    return sorted(result, key=lambda item: seed_for(args.seed, item["slot"]))


def submit(
    args,
    job,
    slot,
    unit,
    data,
    binding,
    authority,
    parent,
    quota,
    budget,
    status,
    disk,
    pending,
):
    """Publish final [N,64,18] samples after liquid-year, parent-dedup and quota checks."""
    if not len(data):
        return
    frame = int(data["timeframe_us"][0])
    years = np.asarray(
        [
            datetime.fromtimestamp(int(t) / 1e6, bars.NY).year
            for t in data["end_us"][:, -1]
        ]
    )
    activity = {r["year"]: r["liquid"] for r in job["years"]}
    for year in np.unique(years):
        if not activity.get(int(year), 0):
            continue  # Preserve the base's actual activity classification; do not relabel illiquid history.
        path = args.output / "jobs" / f"{year}-{job['key']}" / f"{unit}-{frame}"
        if verify_receipt(path, hashes=False):
            continue  # An interrupted checkpoint replay cannot publish or count a unit twice.
        candidates = data[years == year]
        keep = parent.keep(candidates, frame)
        novel = candidates[keep]
        if not len(novel):
            continue
        selected = quota.take(slot, novel, frame)
        if not len(selected):
            continue
        metadata = dict(
            identity=digest([binding, job["ticker"], unit, frame, int(year)]),
            binding=binding,
            ticker=job["ticker"],
            year=int(year),
            liquid=1,
            unit=unit,
            extraction_slot=slot,
            day=datetime.fromtimestamp(int(selected["end_us"][0, -1]) / 1e6, bars.NY)
            .date()
            .isoformat(),
            origin_fraction=1.0 if frame in FRAMES[4:] else 0.2,
            parent_binding=authority["parent_binding"],
            parent_dedup_checked=True,
            parent_duplicates_rejected=int((~keep).sum()),
            candidates_before_parent_dedup=len(candidates),
        )

        def commit(path=path, selected=selected, metadata=metadata, frame=frame):
            accepted = False
            try:
                receipt = publish(
                    path,
                    selected,
                    np.empty(0, dtype=BAR_DTYPE),
                    metadata,
                    budget,
                    32 * 1024**2,
                )
                accepted = receipt is not None
                if receipt is None:
                    raise RuntimeError(
                        "Supplement exhausted its hard byte budget before reserved quotas"
                    )
                status.accepted(receipt)
                return receipt
            finally:
                quota.finish(slot, frame, len(selected), accepted)

        pending.append(disk.submit(commit))
        if len(pending) >= 2:
            pending.pop(
                0
            ).result()  # Bound each lane to two pending small publications.


def extract(
    lane, item, phase, args, binding, authority, quota, budget, status, disk, stop
):
    """Execute one resumable ticker coarse pass or one ticker-year fine pass."""
    job = item["job"]
    slot = item["slot"]
    bounds = bars.ticker_bounds(args.output, job)
    actions = [
        r
        for r in authority["corporate_actions"]["rows"]
        if r["provider_ticker"] == job["ticker"]
    ]
    checkpoint_path = args.output / "checkpoints" / (digest(slot)[:20] + ".json")
    checkpoint = (
        json.loads(checkpoint_path.read_text())
        if checkpoint_path.exists()
        else dict(binding=binding, slot=slot)
    )
    if checkpoint["binding"] != binding or checkpoint["slot"] != slot:
        raise ValueError("Supplement checkpoint identity changed")
    parent = ParentWindows(args.base, job, authority["parent_binding"])
    transport = bars.source.client(args.endpoint, args.query_memory_gib)
    pending = []
    tail = {
        int(f): (
            np.asarray(v[0], dtype=np.float32),
            np.asarray(v[1], dtype=np.uint8),
            np.asarray(v[2], dtype=np.int64),
        )
        for f, v in checkpoint.get("tail", {}).items()
    }

    def commit_cursor():
        for future in pending:
            future.result()
        pending.clear()
        checkpoint["tail"] = {
            str(f): [a.tolist() for a in value] for f, value in tail.items()
        }
        write_json(
            checkpoint_path, checkpoint
        )  # A source cursor advances only after all payload receipts are durable.

    try:
        if phase == "coarse":
            start = checkpoint.get("offset", 0)
            status.update(
                lane,
                ticker=job["ticker"],
                phase="supplement hourly/daily",
                done=start,
                total=len(bounds),
            )
            for offset in range(start, len(bounds), args.coarse_days):
                if stop.is_set():
                    return False
                group = bounds[offset : offset + args.coarse_days]
                unit = f"coarse-{group[0]['source_date']}-{group[-1]['source_date']}"
                statement = bars.range_query(
                    job["ticker"],
                    bounds,
                    authority["rules"],
                    int(group[0]["first_ordinal"]),
                    int(group[-1]["next_ordinal"]),
                    begin_us=bars.clock(group[0]["source_date"], 4),
                    end_us=bars.clock(group[-1]["source_date"], 20),
                )
                output = bars.execute_range(
                    transport,
                    statement,
                    "bargpt-extra-" + digest([slot, unit])[:24],
                    stop,
                    status,
                    lane,
                )
                if output is None:
                    return False
                for frame in FRAMES[4:]:
                    rows = output.filter(output["resolution"] == frame)
                    if len(rows):
                        data, tail[frame] = bars.samples(
                            *bars.arrays(rows),
                            frame,
                            seed_for(args.seed, slot, unit, frame),
                            actions,
                            fraction=1.0,
                            previous=tail.get(frame, ()),
                        )
                        submit(
                            args,
                            job,
                            slot,
                            unit,
                            data,
                            binding,
                            authority,
                            parent,
                            quota,
                            budget,
                            status,
                            disk,
                            pending,
                        )
                checkpoint["offset"] = offset + len(group)
                commit_cursor()
                status.update(
                    lane, done=checkpoint["offset"], day=group[-1]["source_date"]
                )
            checkpoint["exhausted"] = True
        else:
            origin = checkpoint.get("cursor", item["start"])
            status.update(
                lane,
                ticker=job["ticker"],
                day=str(item["year"]),
                phase="supplement ordinal chunks",
                done=0,
                total=max(0, math.ceil((item["limit"] - origin) / args.chunk_events)),
            )
            completed = 0
            while origin < item["limit"] and quota.needed(slot, FRAMES[:4]):
                if stop.is_set():
                    return False
                end = min(origin + args.chunk_events, item["maximum"])
                unit = f"fine-{origin}-{end}"
                statement = bars.range_query(
                    job["ticker"], bounds, authority["rules"], origin, end
                )
                output = bars.execute_range(
                    transport,
                    statement,
                    "bargpt-extra-" + digest([slot, unit])[:24],
                    stop,
                    status,
                    lane,
                )
                if output is None:
                    return False
                for frame in FRAMES[:4]:
                    rows = output.filter(output["resolution"] == frame)
                    if len(rows):
                        data, _ = bars.samples(
                            *bars.arrays(rows),
                            frame,
                            seed_for(args.seed, slot, unit, frame),
                            actions,
                        )
                        submit(
                            args,
                            job,
                            slot,
                            unit,
                            data,
                            binding,
                            authority,
                            parent,
                            quota,
                            budget,
                            status,
                            disk,
                            pending,
                        )
                origin = end
                completed += 1
                checkpoint["cursor"] = origin
                commit_cursor()
                status.update(lane, done=completed)
            checkpoint["exhausted"] = origin >= item["limit"]
        commit_cursor()
        return True
    finally:
        bars.source.close(transport)


def prepare(args):
    """Seal the explicit universe and canonical producer lineage without extracting events."""
    args.output.mkdir(parents=True, exist_ok=True)
    parent = json.loads((args.base / "contract.json").read_text())
    if (
        parent["binding"] != digest(parent["contract"])
        or parent["contract"]["version"] != VERSION
    ):
        raise ValueError("Supplement requires the sealed market-only v6 base")
    original = json.loads((args.base / "plan/manifest.json").read_text())
    if original["hash"] != digest({k: v for k, v in original.items() if k != "hash"}):
        raise ValueError("Parent ordinal plan seal changed")
    # The supplement must execute the exact canonical bar producer used by the base.
    for name, expected in parent["contract"]["code"].items():
        path = (
            Path(importlib.import_module(name).__file__)
            if "." in name and not name.endswith(".py")
            else Path(bars.__file__).parent / name
        )
        if file_hash(path) != expected:
            raise ValueError(f"Shared canonical producer changed: {name}")
    jobs, universe = select_universe(original, args.top, args.seed)
    fine = fine_jobs(jobs, args)
    plan = dict(
        parent_plan_hash=original["hash"], universe=universe, jobs=jobs, fine=fine
    )
    body = dict(
        version=VERSION,
        cohort_id="bargpt-2019-2025-phase1-v6",
        component="liquid-supplement",
        parent_binding=parent["binding"],
        plan_hash=digest(plan),
        authority=parent["contract"]["authority"],
        configuration=dict(
            target_bytes=args.target_bytes,
            workers=args.workers,
            io_workers=args.io_workers,
            chunk_events=args.chunk_events,
            coarse_days=args.coarse_days,
            query_memory_gib=args.query_memory_gib,
            seed=args.seed,
            top=args.top,
        ),
        policy="all originally liquid end-years; parent anti-join; all unique coarse windows; equal fine frames and ticker-year allowances, then redistribution",
        format="Float32[64,18] plus masks, UTC clocks, timeframe and split",
        code={
            name: file_hash(Path(__file__).parent / name)
            for name in ("supplement_v1.py", "liquid_universe_v1.py", "cohort_v1.py")
        },
    )
    wrapper = dict(contract=body, binding=digest(body))
    contract_path = args.output / "contract.json"
    if contract_path.exists() and json.loads(contract_path.read_text()) != wrapper:
        raise ValueError(
            "Supplement contract changed; preserve the existing output and use a new component"
        )
    write_json(contract_path, wrapper)
    write_json(args.output / "supplement-plan.json", plan)
    write_json(
        args.output / "plan.json",
        [dict(ticker=j["ticker"], year=y["year"]) for j in jobs for y in j["years"]],
    )
    (args.output / "plan").mkdir(exist_ok=True)
    (args.output / "jobs").mkdir(exist_ok=True)
    (args.output / "checkpoints").mkdir(exist_ok=True)
    for part in original["partitions"]:
        source = args.base / "plan" / part["name"]
        target = args.output / "plan" / part["name"]
        if (
            source.stat().st_size != part["bytes"]
            or file_hash(source) != part["sha256"]
        ):
            raise ValueError("Parent ordinal partition changed")
        if not target.exists():
            target.hardlink_to(
                source
            )  # Same workstation disk; metadata only, with no event/bar duplication.
        if (
            target.stat().st_size != part["bytes"]
            or file_hash(target) != part["sha256"]
        ):
            raise ValueError("Supplement ordinal partition changed")
    return wrapper, plan


def run(args):
    """Run the queued component with bounded workers, atomic receipts and resumable allocation passes."""
    wrapper, plan = prepare(args)
    binding = wrapper["binding"]
    transport = bars.source.client(args.endpoint)
    try:
        live = bars.source.preflight(transport)
        live["corporate_actions"] = bars.source.split_authority(transport, bars.YEARS)
        if digest(live) != digest(wrapper["contract"]["authority"]):
            raise ValueError(
                "Canonical source authority changed after the supplement was planned"
            )
        args.endpoint = transport.base_url
    finally:
        bars.source.close(transport)
    authority = dict(
        wrapper["contract"]["authority"],
        parent_binding=wrapper["contract"]["parent_binding"],
    )
    import msvcrt

    owner = (args.output / "controller.lock").open("a+b")
    owner.write(b"0")
    owner.flush()
    owner.seek(0)
    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
    quota, status, stop = Allocation(), bars.Status(args.workers), threading.Event()
    budget = Budget(
        args.target_bytes,
        sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file()),
    )
    phase_path = args.output / "phase.json"
    phase = (
        json.loads(phase_path.read_text())
        if phase_path.exists()
        else dict(stage="coarse", pass_number=0, allocated=False)
    )
    state, completed, active, failures = "resuming", 0, {}, []
    began, baseline_bytes = time.time(), budget.used
    jobs_total = 0

    def snapshot():
        if (args.output / "STOP").exists() or shutil.disk_usage(
            args.output
        ).free < 100 * 10**9:
            stop.set()
        write_json(
            args.output / "progress.json",
            dict(
                status.snapshot(),
                state="draining" if stop.is_set() and state == "running" else state,
                updated=time.time(),
                bytes=budget.used,
                target_bytes=budget.limit,
                elapsed_seconds=time.time() - began,
                bytes_per_second=max(0, budget.used - baseline_bytes)
                / max(1, time.time() - began),
                jobs_completed=completed,
                jobs_total=jobs_total,
                jobs_active=len(active),
                jobs_queued=max(0, jobs_total - completed - len(active)),
                failed=failures,
                quotas=quota.snapshot(),
                stage=phase["stage"],
                allocation_pass=phase["pass_number"],
                configuration=dict(
                    workers=args.workers,
                    io_workers=args.io_workers,
                    chunk_events=args.chunk_events,
                    max_chunk_events=args.chunk_events,
                    coarse_days=args.coarse_days,
                ),
            ),
        )

    snapshot()
    for index, path in enumerate(args.output.glob("jobs/*/*/receipt.json")):
        receipt = verify_receipt(path.parent, hashes=False)
        if (
            receipt["binding"] != binding
            or receipt.get("parent_binding") != authority["parent_binding"]
        ):
            raise ValueError("Foreign receipt in the liquid supplement")
        slot = receipt["extraction_slot"]
        for f, count in receipt["frames"].items():
            quota.saved[slot, int(f)] += count
            quota.frame_saved[int(f)] += count
        status.accepted(receipt, reused=True)
        if index % 512 == 0:
            snapshot()
    coarse = [dict(job=job, slot=f"coarse:{job['ticker']}") for job in plan["jobs"]]
    fine = plan["fine"]

    def dispatch(items, current_phase):
        nonlocal completed, jobs_total, state
        completed, jobs_total, state = 0, len(items), "running"
        iterator = iter(items)
        with ThreadPoolExecutor(
            args.io_workers, thread_name_prefix="supplement-io"
        ) as disk:
            with ThreadPoolExecutor(
                args.workers, thread_name_prefix="supplement-query"
            ) as pool:

                def admit(lane):
                    item = next(iterator, None)
                    if item is not None and not stop.is_set():
                        active[
                            pool.submit(
                                extract,
                                lane,
                                item,
                                current_phase,
                                args,
                                binding,
                                authority,
                                quota,
                                budget,
                                status,
                                disk,
                                stop,
                            )
                        ] = lane

                for lane in range(args.workers):
                    admit(lane)
                while active:
                    try:
                        ready, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                    except KeyboardInterrupt:
                        stop.set()
                        continue
                    for future in ready:
                        lane = active.pop(future)
                        try:
                            completed += int(future.result())
                            status.update(lane, phase="completed")
                        except Exception as error:
                            failures.append(
                                dict(worker=lane + 1, error=str(error)[:1600])
                            )
                            status.update(lane, phase="failed")
                            stop.set()
                        admit(lane)
                    snapshot()

    if phase["stage"] == "coarse":
        for item in coarse:
            days = sum(y["days"] for y in item["job"]["years"])
            quota.limits[item["slot"], FRAMES[-2]] = (
                days * 24
            )  # Conservative upper bound, never manufactured bars.
            quota.limits[item["slot"], FRAMES[-1]] = days
        for frame in FRAMES[4:]:
            quota.targets[frame] = sum(
                n for (slot, f), n in quota.limits.items() if f == frame
            )
        dispatch(coarse, "coarse")
        if not stop.is_set():
            phase = dict(stage="fine", pass_number=0, allocated=False)
            write_json(phase_path, phase)
    allocation_path = args.output / "allocation.json"
    if phase["stage"] == "fine" and allocation_path.exists():
        saved = json.loads(allocation_path.read_text())
        quota.targets = {
            int(f): n for f, n in saved["targets"].items()
        }  # Keep the original capacity target across resumes/top-up passes.
    while phase["stage"] == "fine" and not stop.is_set():
        if phase["allocated"]:
            saved = json.loads(allocation_path.read_text())
            quota.targets = {int(f): n for f, n in saved["targets"].items()}
            quota.limits = {(slot, int(f)): n for slot, f, n in saved["limits"]}
        else:
            if not quota.targets.get(FRAMES[0]):
                payload = (
                    max(0, int(args.target_bytes * 0.98) - budget.used)
                    // SAMPLE_DTYPE.itemsize
                )
                per_slot = payload // (4 * len(fine))
                if per_slot < 1:
                    raise ValueError(
                        "Insufficient byte capacity for a fair fine-frame allocation"
                    )
                for frame in FRAMES[:4]:
                    quota.targets[frame] = quota.total(frame) + per_slot * len(fine)
                for frame in FRAMES[4:]:
                    quota.targets[frame] = quota.total(
                        frame
                    )  # Scarce coarse inventory is final; remaining space belongs to fine frames.
            available = []
            for item in fine:
                path = (
                    args.output / "checkpoints" / (digest(item["slot"])[:20] + ".json")
                )
                if not path.exists() or not json.loads(path.read_text()).get(
                    "exhausted"
                ):
                    available.append(item)
            if not available:
                break
            for frame in FRAMES[:4]:
                allowance = math.ceil(
                    max(0, quota.targets[frame] - quota.total(frame)) / len(available)
                )
                for item in available:
                    quota.limits[item["slot"], frame] = (
                        quota.saved[item["slot"], frame] + allowance
                    )
            write_json(
                allocation_path,
                dict(
                    targets=quota.targets,
                    limits=[[slot, f, n] for (slot, f), n in quota.limits.items()],
                ),
            )
            phase["allocated"] = True
            write_json(phase_path, phase)
        if all(quota.total(f) >= quota.targets[f] for f in FRAMES[:4]):
            break
        before = status.samples
        dispatch(
            [item for item in fine if quota.needed(item["slot"], FRAMES[:4])], "fine"
        )
        if not stop.is_set():
            phase["allocated"] = False
            phase["pass_number"] += 1
            write_json(phase_path, phase)
            if status.samples == before:
                break  # All admitted source ranges exhausted without new eligible windows; report the shortfall.
    state = (
        "failed"
        if failures
        else (
            "stopped"
            if stop.is_set()
            else (
                "quotas_reached"
                if all(quota.total(f) >= quota.targets.get(f, 0) for f in FRAMES[:4])
                else "inventory_exhausted"
            )
        )
    )
    snapshot()
    result = dict(
        state=state,
        bytes=budget.used,
        target_bytes=budget.limit,
        samples=status.samples,
        frames=status.frames,
        failed=failures,
        binding=binding,
        parent_binding=authority["parent_binding"],
        selected_tickers=len(plan["jobs"]),
        fine_slots=len(fine),
        duplicate_policy="all published candidates anti-joined against the sealed parent",
    )
    write_json(args.output / "result.json", result)
    owner.close()
    return int(bool(failures))
