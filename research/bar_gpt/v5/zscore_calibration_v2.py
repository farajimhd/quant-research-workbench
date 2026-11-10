"""One-time, resumable, vectorized population statistics over the training split.

Every selected payload is SHA-verified. Stored overlapping observations retain
their training weights. Validation/purge ranges never contribute. No data writer.
"""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import time

import numpy as np

from .data_v1 import load_split, validate_complete_market
from .shards.contract import FRAMES, digest, write_json
from .shards.phase1_contract import CHANNELS

VERSION = "all18-per-timeframe-training-population-zscore-v2"


def merge(left, right):
    """Parallel Welford merge, vectorized over [6,18], including empty groups."""
    n, mean, m2 = left
    k, other, v2 = right
    total = n + k
    delta = other - mean
    weight = k / np.maximum(total, 1)
    return total, mean + delta * weight, m2 + v2 + delta**2 * n * weight


def moments(rows, chunk_size=2048):
    """Bounded Float64 blocks; no Python loops over samples or observations."""
    total = (np.zeros((6, 18), np.int64), np.zeros((6, 18)), np.zeros((6, 18)))
    for begin in range(0, len(rows), chunk_size):
        chunk = rows[begin : begin + chunk_size]
        validate_complete_market(chunk)
        for f, frame in enumerate(FRAMES):
            part = chunk[chunk["timeframe_us"] == frame]
            if not len(part):
                continue
            x, mask = part["values"].astype(np.float64), part["valid"].astype(bool)
            count = mask.sum(axis=(0, 1), dtype=np.int64)
            mean = np.sum(x, axis=(0, 1), where=mask) / np.maximum(count, 1)
            x -= mean
            np.square(x, out=x)
            m2 = np.sum(x, axis=(0, 1), where=mask)
            n, mu, var = (np.zeros_like(t) for t in total)
            n[f], mu[f], var[f] = count, mean, m2
            total = merge(total, (n, mu, var))
    return total


def payload(item):
    path = Path(item["path"])
    raw = path.read_bytes()
    if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError(f"Changed sealed training payload: {path}")
    data = np.load(io.BytesIO(raw), allow_pickle=False)
    selected = data[item["start"] : item["stop"]]
    if len(selected) != item["stop"] - item["start"] or not len(selected):
        raise ValueError("Invalid training selection range")
    stats = moments(selected)
    return [x.tolist() for x in stats], len(selected), len(raw)


def load_calibration(path, manifest):
    """Validate frozen, complete, training-only certificate before any GPU work."""
    import torch
    from .pretraining_v2 import ZScoreStats

    result = json.loads(Path(path).read_text())
    if result.get("hash") != digest({k: v for k, v in result.items() if k != "hash"}):
        raise ValueError("Z-score calibration seal changed")
    if (
        result.get("version") != VERSION
        or result.get("split_hash") != manifest["manifest_hash"]
        or result.get("channels") != list(CHANNELS)
        or result.get("frames") != list(FRAMES)
        or result.get("training_samples") != manifest["counts"]["train"]
        or result.get("ddof") != 0
        or result.get("completed_files") != len(manifest["splits"]["train"])
    ):
        raise ValueError(
            "Z-score calibration is incomplete or belongs to a different training contract"
        )
    counts = np.asarray(result["counts"], dtype=np.int64)
    if counts.shape != (6, 18) or np.any(counts <= 0):
        raise ValueError("Missing feature/timeframe statistics")
    return result, ZScoreStats(
        torch.tensor(result["mean"], dtype=torch.float64),
        torch.tensor(result["std"], dtype=torch.float64),
    )


def fit(split, output, workers=8):
    import psutil

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = load_split(Path(split))
    entries = manifest["splits"]["train"]
    contract = dict(
        version=VERSION,
        split_hash=manifest["manifest_hash"],
        ddof=0,
        channels=list(CHANNELS),
        frames=list(FRAMES),
        weighting="every source-valid stored training occurrence; valid zeros included",
    )
    db = sqlite3.connect(output / "moments.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS done (idx INTEGER PRIMARY KEY, moments TEXT, samples INTEGER, bytes INTEGER)"
    )
    previous = db.execute("SELECT value FROM meta WHERE key='contract'").fetchone()
    if previous and previous[0] != digest(contract):
        raise ValueError("Statistics ledger belongs to a different contract")
    db.execute("INSERT OR IGNORE INTO meta VALUES ('contract',?)", (digest(contract),))
    db.commit()
    completed = {r[0] for r in db.execute("SELECT idx FROM done")}
    if completed and (min(completed) < 0 or max(completed) >= len(entries)):
        raise ValueError("Statistics ledger has an unexpected entry")
    todo = iter(i for i in range(len(entries)) if i not in completed)
    began, reported = time.monotonic(), 0.0
    pending = deque()
    try:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="zscore-fit"
        ) as pool:

            def admit():
                if psutil.virtual_memory().available < 64 * 2**30:
                    raise RuntimeError(
                        "Statistics pass requires 64 GiB available RAM reserve"
                    )
                i = next(todo, None)
                if i is not None:
                    pending.append((i, pool.submit(payload, entries[i])))
                return i is not None

            for _ in range(workers * 2):
                if not admit():
                    break
            while pending:
                if (output / "STOP").exists():
                    raise InterruptedError(
                        "Statistics STOP requested; completed ledger preserved"
                    )
                i, future = pending.popleft()
                stats, samples, size = future.result()
                db.execute(
                    "INSERT INTO done VALUES (?,?,?,?)",
                    (i, json.dumps(stats), samples, size),
                )
                completed.add(i)
                admit()
                if time.monotonic() - reported >= 2:
                    db.commit()
                    totals = db.execute(
                        "SELECT sum(samples),sum(bytes) FROM done"
                    ).fetchone()
                    write_json(
                        output / "progress.json",
                        dict(
                            state="running",
                            stage="training-only z-score statistics",
                            done=len(completed),
                            total=len(entries),
                            samples=totals[0],
                            bytes=totals[1],
                            workers=workers,
                            elapsed_seconds=time.monotonic() - began,
                        ),
                    )
                    reported = time.monotonic()
        db.commit()
        total = (np.zeros((6, 18), np.int64), np.zeros((6, 18)), np.zeros((6, 18)))
        for (value,) in db.execute("SELECT moments FROM done ORDER BY idx"):
            n, mean, m2 = json.loads(value)
            total = merge(
                total, (np.asarray(n, dtype=np.int64), np.asarray(mean), np.asarray(m2))
            )
        count, mean, m2 = total
        done, samples, size = db.execute(
            "SELECT count(*),sum(samples),sum(bytes) FROM done"
        ).fetchone()
        if (
            done != len(entries)
            or samples != manifest["counts"]["train"]
            or np.any(count <= 0)
        ):
            raise ValueError("Training statistics coverage is incomplete")
        variance = m2 / count
        std = np.sqrt(variance)
        zero = std == 0
        std[zero] = 1.0  # Explicit constant-feature convention; no epsilon refit.
        result = dict(
            contract,
            mean=mean.tolist(),
            std=std.tolist(),
            counts=count.tolist(),
            constant_features=zero.tolist(),
            training_samples=samples,
            completed_files=done,
            source_bytes_hash_verified=size,
            dataset_binding=manifest.get("dataset_binding"),
        )
        result["hash"] = digest(result)
        write_json(output / "calibration.json", result)
        load_calibration(output / "calibration.json", manifest)
        write_json(
            output / "progress.json",
            dict(
                state="complete",
                stage="training-only z-score statistics",
                done=done,
                total=len(entries),
                samples=samples,
                elapsed_seconds=time.monotonic() - began,
                calibration_hash=result["hash"],
            ),
        )
        return result
    finally:
        db.commit()
        db.close()


def main(argv=None):
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16:
        parser.error("Use 1..16 bounded statistics workers")
    fit(args.split, args.output, args.workers)


if __name__ == "__main__":
    main()
