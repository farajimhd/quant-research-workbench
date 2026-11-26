"""Publish an audited successor to the certified all-published Phase 2 release."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import time
from zipfile import ZipFile, ZIP_DEFLATED

os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import numpy as np
import polars as pl
from ..phase2_v2.release import load_released_units
from ..phase2_v2.train import lease
from ..v5.phase2_ar_shards.contract import BLOCK
from ..v5.shards.contract import digest, file_hash, write_json
from .data import prepare, successor_rows
from .unique_data import VERSION, merge_sessions, payload


def jobs(units):
    grouped = defaultdict(list)
    start = 0
    for _, _, table in units.groups:
        following = successor_rows(table)
        for i, (path, split, blocks, receipt) in enumerate(
                table.select("path", "unit_split", "ordinal", "receipt_hash").iter_rows()):
            other = start + int(following[i]) if following[i] >= 0 else None
            grouped[path.replace(chr(92),"/")].append((start+i, other, split, len(blocks), receipt))
        start += table.height
    return sorted(grouped.items())


def write_payload(path, arrays):
    """Use packed files to avoid small-file overhead. Compress integers and sparse bars together."""
    tmp = path.with_suffix(".partial")
    with tmp.open("wb") as stream:
        with ZipFile(stream, "w", compression=ZIP_DEFLATED, compresslevel=1) as bundle:
            for key, array in arrays.items():
                with bundle.open(key+".npy", "w") as part:
                    np.lib.format.write_array(part, array, allow_pickle=False)
        stream.flush()
        os.fsync(stream.fileno())
    # Check exact round trip after all bytes have reached the staged file.
    with np.load(tmp, allow_pickle=False) as actual:
        for key, expected in arrays.items():
            copy = actual[key]
            if (copy.dtype != expected.dtype or copy.shape != expected.shape
                    or copy.tobytes() != expected.tobytes()):
                raise ValueError(f"Staging readback differs: {path}/{key}")
    sha = file_hash(tmp)
    os.replace(tmp, path)
    return sha


def convert(units, root, key, refs):
    splits = {r[2] for r in refs}
    if len(splits) != 1:
        raise ValueError("Session crosses the certified partitions")
    occurrences = sum(r[3] for r in refs) * BLOCK
    items = [prepare(units[i], units[next_i] if next_i is not None else None, unique=True)
             for i, next_i, _, _, _ in refs]
    targets = []
    data = merge_sessions(items, audit=targets)
    ticker_key, day = key.split("/")[1:]
    if not (len(ticker_key)==24 and len(day)==10 and all(c in "0123456789abcdef" for c in ticker_key)
            and all(c in "0123456789-" for c in day)):
        raise ValueError(f"Invalid session identity: {key}")
    rel = f"sessions/{ticker_key[:2]}/{ticker_key}/{day}.npz"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    sha = write_payload(path, payload(data))
    eligible = np.logical_or.reduce([x[:,0]>=0 for x in data.targets])
    return (rel, sha, splits.pop(), occurrences, len(data.origins), int(eligible.sum()), path.stat().st_size,
            digest([r[4] for r in refs]), json.dumps(targets, separators=(",",":")))


FIELDS = ("path", "sha", "split", "occurrences", "candidates", "eligible", "bytes", "source_receipts", "target_audit")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--release", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--storage", type=Path, help="Separate staged payload root if required")
    p.add_argument("--source-revision", required=True)
    p.add_argument("--workers", type=int, default=4, choices=(1,2,4,8,16,32))
    p.add_argument("--resume-eight-worker-journal", action="store_true",
                   help="Adopt the preceding journal with identical session and payload rules")
    p.add_argument("--pilot", type=int, default=0, help="Pilot session count; never seals a production release")
    args = p.parse_args()
    out = args.output.resolve()
    root = args.storage.resolve() if args.storage else out
    allowed = (Path("D:/TradingML/runtimes"), Path("G:/TradingML/runtimes"))
    if not out.is_relative_to(allowed[0]) or not any(root.is_relative_to(x) and root != x for x in allowed):
        p.error("Use workstation runtime roots")
    if os.environ.get("COMPUTERNAME","").upper() != "DESKTOP-SAAI85T":
        p.error("Migration must run on the workstation")
    out.mkdir(parents=True,exist_ok=True)
    root.mkdir(parents=True,exist_ok=True)
    from .migrate_panel import status
    with lease(out):
        started = time.time()
        progress = dict(state="running",phase="Loading certified inventory", completed=0,total=0,
                        occurrences=0,unique=0,bytes=0,started=started,pid=os.getpid(),workers=args.workers)
        status(out, progress)
        try:
            units, authority = load_released_units(args.release)
            all_jobs = jobs(units)
            expected_occurrences = authority["blocks"] * BLOCK
            if sum(r[3] for _, refs in all_jobs for r in refs) * BLOCK != expected_occurrences:
                raise ValueError("Source block budget differs")
            selected = all_jobs
            if args.pilot:
                selected = random.Random(5198).sample(all_jobs,min(len(all_jobs),args.pilot))
                sample_overlap = next((item for item in all_jobs if len(item[1])>2), None)
                if sample_overlap and sample_overlap not in selected:
                    selected.append(sample_overlap)
            binding = dict(version=VERSION, parent=authority, storage=str(root),
                           source=args.source_revision, pilot=args.pilot)
            setup = out / "migration.json"
            if setup.exists():
                previous = json.loads(setup.read_text())
                parent_source = "a41627a36f8adad01a5b4b235a3f6e08c4b8118a"
                allowed = args.resume_eight_worker_journal and previous == dict(binding, source=parent_source)
                if previous != binding and not allowed:
                    raise ValueError("Journal is bound to a different migration")
                if allowed:
                    write_json(out/"previous-migration.json", previous)
            elif args.resume_eight_worker_journal:
                raise ValueError("Expected the preceding journal")
            write_json(setup,binding)
            run(out, root, units, selected, binding, expected_occurrences, args.workers, progress)
        except BaseException as e:
            progress.update(state="failed",error=repr(e))
            status(out,progress)
            raise


def run(out, root, units, selected, binding, expected_occurrences, workers, progress):
    from .migrate_panel import status
    db = sqlite3.connect(out / "migration.sqlite")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS sessions (key TEXT PRIMARY KEY, result TEXT NOT NULL)")
        saved = {k:json.loads(v) for k,v in db.execute("SELECT key, result FROM sessions")}
        pending = [(k,r) for k,r in selected if k not in saved]
        progress.update(total=len(selected), completed=len(saved), phase="Copying and verifying sessions")
        for row in saved.values():
            if file_hash(root/row[0]) != row[1]:
                raise ValueError(f"Previously committed payload changed: {row[0]}")
            progress["occurrences"]+=row[3]
            progress["unique"]+=row[4]
            progress["bytes"]+=row[6]
        status(out,progress)
        copy_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for first in range(0,len(pending),workers*4):
                if (out/"STOP").exists():
                    progress.update(state="stopped")
                    status(out,progress)
                    return
                if shutil.disk_usage(root.anchor).free < 180_000_000_000:
                    raise RuntimeError("Destination free space reached the 180 GB reserve")
                part = pending[first:first+workers*4]
                work = [(key,pool.submit(convert,units,root,key,refs)) for key,refs in part]
                for key, future in work:
                    try:
                        row = future.result()
                    except Exception as exc:
                        raise ValueError(f"Migration conflict for {key}: {exc}") from exc
                    db.execute("INSERT INTO sessions VALUES (?,?)",(key,json.dumps(row)))
                    saved[key]=row
                    progress["completed"]+=1
                    progress["occurrences"]+=row[3]
                    progress["unique"]+=row[4]
                    progress["bytes"]+=row[6]
                db.commit()
                status(out,progress)
        rows = pl.DataFrame([saved[k] for k,_ in selected],schema=list(FIELDS),orient="row")
        if binding["pilot"]:
            report = dict(binding, **progress, copy_seconds=time.perf_counter()-copy_started,
                          estimated_bytes=round(progress["bytes"]/len(selected)*len(jobs(units))))
            write_json(out/"pilot-result.json",report)
        else:
            if rows["occurrences"].sum()!=expected_occurrences or len(saved)!=len(selected):
                raise ValueError("Final source coverage reconciliation failed")
            catalog = root/"sessions.parquet"
            tmp = catalog.with_suffix(".partial")
            rows.write_parquet(tmp)
            os.replace(tmp,catalog)
            manifest = dict(binding, sessions=len(saved), unique_origins=rows["candidates"].sum(),
                            source_occurrences=rows["occurrences"].sum(), catalog_sha256=file_hash(catalog),
                            split_counts=rows.group_by("split").agg(pl.sum("candidates","eligible"),
                                pl.len().alias("sessions")).sort("split").to_dicts())
            write_json(root/"certificate.json",dict(manifest,hash=digest(manifest)))
        progress.update(state="complete",phase="Pilot verified" if binding["pilot"] else "Release sealed")
        status(out,progress)
    finally:
        db.close()


if __name__=="__main__":
    main()
