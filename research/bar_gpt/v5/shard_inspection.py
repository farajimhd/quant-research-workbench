"""Read-only, receipt-verified inspection of the v6 base and queued liquid supplement.

No canonical queries, aggregation, transforms or label generation occur here.
Only published files beneath the fixed workstation runtime authority are read.
"""

from functools import lru_cache
import os
import json
from pathlib import Path
import random
import re
import time
import threading

import numpy as np

from .shards.phase1_contract import (
    CHANNELS,
    FRAMES,
    SAMPLE_DTYPE,
    VERSION,
    digest,
    file_hash,
)
from .shards.balanced_plan import VERSION as BALANCED_VERSION
from .cohort_v1 import published_members

ROOT = Path(
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\bar_gpt\shards-2019-2025-v6"
)


def singleflight_cache(maxsize):
    """Serialize cold cache misses so concurrent requests share one SMB scan."""

    def decorate(function):
        cached = lru_cache(maxsize=maxsize)(function)
        lock = threading.RLock()

        def call(*args, **kwargs):
            with lock:
                return cached(*args, **kwargs)

        call.cache_clear = cached.cache_clear
        return call

    return decorate


@singleflight_cache(maxsize=2)
def _plan(bucket=0):
    """Cache immutable ticker-year identity metadata, not growing file listings."""
    wrapper = json.loads((ROOT / "contract.json").read_text())
    if wrapper["binding"] != digest(wrapper["contract"]) or wrapper["contract"][
        "version"
    ] not in (VERSION, BALANCED_VERSION):
        raise ValueError("The inspection source has an unsupported or corrupt contract")
    _, components = published_members(ROOT)
    rows, roots = [], {}
    for name, root in components:
        member = json.loads((root / "contract.json").read_text())
        if (
            member["binding"] != digest(member["contract"])
            or member["contract"]["version"] != VERSION
        ):
            raise ValueError("Invalid cohort member contract")
        roots[name] = root
        jobs = json.loads((root / "plan.json").read_text())
        prefix = "" if name == "base" else name + "~"
        rows.extend(
            dict(
                job=prefix + f"{r['year']}-{digest(r['ticker'])[:20]}",
                ticker=r["ticker"],
                year=r["year"],
                component=name,
                source_binding=member["binding"],
            )
            for r in jobs
        )
    return wrapper, rows, roots


def _current_plan():
    return _plan(
        int(time.time() // 30)
    )  # Discover new published components without restarting the application.


@singleflight_cache(maxsize=2)
def _available(bucket):
    """Refresh available job directories every thirty seconds during extraction."""
    result = set()
    for name, root in _current_plan()[2].items():
        prefix = "" if name == "base" else name + "~"
        # Windows directory enumeration already carries the directory flag.
        # Path.is_dir() would issue another SMB request for every ticker-year.
        with os.scandir(root / "jobs") as entries:
            result.update(prefix + entry.name for entry in entries if entry.is_dir())
    return result


def catalog(search="", year=0, offset=0):
    """Return a bounded page of materialized ticker-years and current run status."""
    wrapper, plan, roots = _current_plan()
    available = _available(int(time.time() // 30))
    jobs = [
        r
        for r in plan
        if r["job"] in available
        and (not year or r["year"] == year)
        and search.upper() in r["ticker"].upper()
    ]
    components = []
    for name, root in roots.items():
        status_path = root / "progress.json"
        if not status_path.exists():
            status_path = root / "queue.json"
        status = (
            json.loads(status_path.read_text())
            if status_path.exists()
            else dict(state="planned")
        )
        components.append(
            dict(
                name=name,
                state=status.get("state"),
                samples=status.get("samples", 0),
                bytes=status.get("bytes", 0),
            )
        )
    progress = json.loads((ROOT / "progress.json").read_text())
    progress.update(
        samples=sum(c["samples"] for c in components),
        bytes=sum(c["bytes"] for c in components),
    )
    return dict(
        version=wrapper["contract"]["version"],
        jobs=jobs[offset : offset + 100],
        total=len(jobs),
        offset=offset,
        years=wrapper["contract"]["years"],
        channels=CHANNELS,
        frames=FRAMES,
        binding=wrapper["binding"],
        components=components,
        progress={k: progress.get(k) for k in ("state", "samples", "bytes", "updated")},
    )


def _job(job):
    if not re.fullmatch(r"(?:[a-z][a-z0-9-]*~)?20\d{2}-[a-f0-9]{20}", job):
        raise ValueError("Invalid ticker-year identifier")
    row = next((r for r in _current_plan()[1] if r["job"] == job), None)
    if row is None:
        raise ValueError("Ticker-year is not in the frozen extraction plan")
    return row, _current_plan()[2][row["component"]] / "jobs" / job.split("~")[-1]


@lru_cache(maxsize=64)
def _shards(job, bucket):
    """List sealed files for one ticker-year; never enumerate the entire corpus."""
    identity, directory = _job(job)
    result = []
    for p in sorted(directory.glob("*/receipt.json")):
        r = json.loads(p.read_text())
        if (
            digest({k: v for k, v in r.items() if k != "receipt_hash"})
            != r["receipt_hash"]
        ):
            raise ValueError("Shard publication receipt is corrupt")
        if r["binding"] != identity["source_binding"]:
            raise ValueError("Shard belongs to another dataset contract")
        for f in r["files"]:
            result.append(
                dict(
                    shard=p.parent.name,
                    file=f["name"],
                    samples=r["samples"],
                    frames=r["frames"],
                    bytes=f["bytes"],
                )
            )
    return result


def shards(job):
    return dict(**_job(job)[0], shards=_shards(job, int(time.time() // 30)))


@lru_cache(maxsize=128)
def _verify(path, size, mtime, expected_hash):
    """Hash each unchanged file once; changing a sealed file fails closed."""
    if file_hash(Path(path)) != expected_hash:
        raise ValueError("Shard payload SHA-256 differs from its publication receipt")


def sample(
    job="", shard="", file="", row=0, timeframe=0, randomize=False, search="", year=0
):
    """Read one sample as 64 raw rows with explicit padding and validity.

    Market arrays remain [64,18]. JSON null means invalid evidence; raw zero
    placeholders are returned separately so inspection can verify stored bytes.
    Random selection chooses a matching job/file/row, not a uniform corpus draw.
    """
    if timeframe and timeframe not in FRAMES:
        raise ValueError("Unsupported timeframe")
    if not job:
        available = _available(int(time.time() // 30))
        jobs = [
            r
            for r in _current_plan()[1]
            if r["job"] in available
            and (not year or r["year"] == year)
            and search.upper() in r["ticker"].upper()
        ]
        random.shuffle(jobs)
        for candidate in jobs[:100]:
            choices = [
                f
                for f in shards(candidate["job"])["shards"]
                if not timeframe or f["frames"].get(str(timeframe), 0)
            ]
            if choices:
                job = candidate["job"]
                chosen = random.choice(choices)
                shard, file = chosen["shard"], chosen["file"]
                randomize = True
                break
        if not job:
            raise ValueError(
                "No matching published sample found; select a materialized ticker-year"
            )
    identity, directory = _job(job)
    if not re.fullmatch(
        r"(?:\d{4}-\d{2}-\d{2}|coarse|(?:fine|coarse)-[0-9-]{1,85})", shard
    ) or not re.fullmatch(r"samples-\d{4}\.npy", file):
        raise ValueError("Select a published shard file")
    receipt = json.loads((directory / shard / "receipt.json").read_text())
    if (
        digest({k: v for k, v in receipt.items() if k != "receipt_hash"})
        != receipt["receipt_hash"]
        or receipt["binding"] != identity["source_binding"]
    ):
        raise ValueError("Invalid publication receipt")
    entry = next((f for f in receipt["files"] if f["name"] == file), None)
    if entry is None:
        raise ValueError("File is not published in this receipt")
    path = directory / shard / file
    stat = path.stat()
    if stat.st_size != entry["bytes"]:
        raise ValueError("Published file size changed")
    _verify(str(path), stat.st_size, stat.st_mtime_ns, entry["sha256"])
    data = np.load(path, mmap_mode="r", allow_pickle=False)
    try:
        if data.dtype != SAMPLE_DTYPE or data.ndim != 1:
            raise ValueError("Unexpected shard dtype")
        indices = (
            np.flatnonzero(data["timeframe_us"] == timeframe)
            if timeframe
            else np.arange(len(data))
        )
        if not len(indices):
            raise ValueError("No sample of this timeframe in the selected file")
        if randomize:
            row = int(random.choice(indices))
        if row not in indices:
            raise ValueError("Sample row does not match the selected timeframe")
        record = data[row].copy()  # One 7 KB record detaches from the file mapping.
        present = record["end_us"] > 0
        if (
            int(record["timeframe_us"]) not in FRAMES
            or np.any(record["end_us"] < 0)
            or not np.isin(record["valid"], (0, 1)).all()
            or not np.isfinite(record["values"]).all()
            or not present[0]
            or np.any(present[1:] & ~present[:-1])
            or np.any((np.diff(record["end_us"]) <= 0) & present[1:])
            or np.any(record["values"][~present])
            or np.any(record["valid"][~present])
        ):
            raise ValueError("Sample has malformed evidence, clocks or padding")
        if not record["valid"][present, :6].all() or np.any(
            record["values"][present, :6] <= 0
        ):
            raise ValueError("Real bar has incomplete trade attributes")
        sample_rows = [
            dict(
                row=int(i),
                timeframe=int(data["timeframe_us"][i]),
                first_us=int(data["end_us"][i, 0]),
                last_us=int(data["end_us"][i].max()),
            )
            for i in indices
        ]
    finally:
        data._mmap.close()
    bars = []
    for i in range(64):
        end = int(record["end_us"][i])
        valid = record["valid"][i].astype(bool)
        raw = record["values"][i].tolist()
        bars.append(
            dict(
                index=i,
                end_us=end or None,
                padding=end == 0,
                gap_us=int(end - record["end_us"][i - 1]) if i and end else None,
                raw=raw,
                valid=valid.tolist(),
                values=[v if ok else None for v, ok in zip(raw, valid)],
            )
        )
    return dict(
        **identity,
        version=_current_plan()[0]["contract"]["version"],
        shard=shard,
        file=file,
        row=row,
        timeframe=int(record["timeframe_us"]),
        samples=sample_rows,
        bars=bars,
        channels=CHANNELS,
        sha256=entry["sha256"],
        receipt_hash=receipt["receipt_hash"],
        binding=receipt["binding"],
        observed=sum(not b["padding"] for b in bars),
        reporting_counts=receipt.get("reporting_counts"),
        basis=(
            "sample-end executed split basis"
            if _current_plan()[0]["contract"]["version"] == BALANCED_VERSION
            or shard == "coarse"
            else "session raw share basis"
        ),
    )
