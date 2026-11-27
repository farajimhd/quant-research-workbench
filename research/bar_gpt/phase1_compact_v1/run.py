"""Build an independently verified, portable Phase 1 bar-pool release."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import hashlib
import io
from itertools import islice
import json
from pathlib import Path
import shutil
import sqlite3
import time

import numpy as np

from .format import pack_windows, part_path, unpack
from ..phase2_v2.train import lease
from ..v5.receipt_index_v1 import index_receipt
from ..v5.shards.phase1_contract import SAMPLE_DTYPE, digest, file_hash, write_json

VERSION = "phase1-compact-v1"
SPLITS = ("train", "validation")
CHUNK = 512
MAX_BATCH_INPUT_BYTES = 256 * 1024**2


def read_json(path):
    return json.loads(path.read_text())


def verify_container(output, item):
    number, expected_sha, expected_size = item
    path = part_path(output, number)
    if path.stat().st_size != expected_size or file_hash(path) != expected_sha:
        raise ValueError(f"Committed compact container differs: {number}")


def read_authority(cohort):
    cohort = cohort.resolve()
    release = read_json(cohort / "balanced-release.json")
    if release.pop("hash") != digest(release) or not release["training_ready"]:
        raise ValueError("Expected sealed corrected Phase 1 release")
    path = cohort / "split.json"
    if file_hash(path) != release["split_sha256"]:
        raise ValueError("Phase 1 split hash differs")
    split = read_json(path)
    members = split["dataset_members"]
    if {m["name"]: file_hash(Path(m["path"]) / "contract.json") for m in members} != release["member_contracts"]:
        raise ValueError("Phase 1 member contracts differ")
    index_root = cohort / ("loader-index-v1-" + split["manifest_hash"][:20])
    seal = read_json(index_root / "index.json")
    if seal.pop("hash") != digest(seal) or seal["split_hash"] != split["manifest_hash"]:
        raise ValueError("Loader index is outside the sealed split")
    indices = {}
    for name, ref in seal["splits"].items():
        file = index_root / ref["file"]
        if file.stat().st_size != ref["bytes"] or file_hash(file) != ref["sha256"]:
            raise ValueError("Loader index content differs")
        rows = np.load(file, mmap_mode="r", allow_pickle=False)
        if len(rows) != ref["rows"] or int((rows["stop"]-rows["start"]).sum()) != split["counts"][name]:
            raise ValueError(f"{name} selection count differs")
        indices[name] = rows
    authority = dict(split_sha256=release["split_sha256"], split_hash=split["manifest_hash"],
                     counts=split["counts"], cutoff_day=split["cutoff_day"], members=members,
                     inventory_samples=split["inventory_samples"], purged_crossing=split["purged_crossing"],
                     loader_index=seal)
    return authority, indices


def catalog(db, root, authority, indices, progress, workers):
    """Retain the exact split entry order and any purged source windows."""
    base = {Path(member["path"]).resolve(): member for member in authority["members"]}
    sources = {}

    def add(path, sha, size):
        path = Path(path)
        member = next((r for r in base if path.is_relative_to(r / "jobs")), None)
        if member is None:
            raise ValueError(f"Unapproved Phase 1 source path: {path}")
        relative = path.relative_to(root)
        if ".." in relative.parts or relative.suffix != ".npy":
            raise ValueError(f"Invalid Phase 1 source path: {path}")
        key = relative.as_posix()
        existing = sources.get(key)
        if existing is not None:
            if existing[1:] != (sha, size):
                raise ValueError(f"Conflicting Phase 1 source receipts: {key}")
            return existing[0]
        number = len(sources)
        sources[key] = (number, sha, size)
        return number

    db.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
            number INTEGER PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, source_sha TEXT NOT NULL,
            source_bytes INTEGER NOT NULL, windows INTEGER, bars INTEGER, exceptions INTEGER,
            output_bytes INTEGER, output_sha TEXT, container INTEGER, offset INTEGER);
        CREATE TABLE IF NOT EXISTS containers (
            number INTEGER PRIMARY KEY, sha TEXT NOT NULL, bytes INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS entries (
            split TEXT NOT NULL, ordinal INTEGER NOT NULL, number INTEGER NOT NULL,
            start INTEGER NOT NULL, stop INTEGER NOT NULL, PRIMARY KEY(split, ordinal));
        CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS pending_sources ON sources(number) WHERE output_sha IS NULL;
    """)
    # Build the mapping in a transaction. A killed planner leaves empty tables.
    with db:
        for name in SPLITS:
            for begin in range(0, len(indices[name]), 8192):
                batch = []
                for j, row in enumerate(indices[name][begin:begin+8192]):
                    number = add(row["path"].decode(), row["sha256"].decode(), int(row["bytes"]))
                    batch.append((name, begin+j, number, int(row["start"]), int(row["stop"])))
                db.executemany("INSERT INTO entries VALUES (?,?,?,?,?)", batch)
                progress("Indexing sealed split", files_done=len(sources))
        expected = authority["inventory_samples"]
        count = sum(size // SAMPLE_DTYPE.itemsize for _, _, size in sources.values())
        if count < expected:
            progress("Finding purged-only source parts", files_done=len(sources))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for member, info in base.items():
                    receipts = unknown_receipts(member, root, sources)
                    reader = partial(index_receipt, binding=info["binding"])
                    while batch := list(islice(receipts, CHUNK)):
                        if (progress.output / "STOP").exists():
                            raise InterruptedError("STOP")
                        for entries in pool.map(reader, batch):
                            for entry in entries:
                                add(entry["path"], entry["sha256"], entry["bytes"])
        if sum(size // SAMPLE_DTYPE.itemsize for _, _, size in sources.values()) != expected:
            raise ValueError("Source files do not reconcile with sealed inventory")
        db.executemany("INSERT INTO sources(number,source_key,source_sha,source_bytes) VALUES (?,?,?,?)",
                       ((number, key, sha, size) for key, (number, sha, size) in sources.items()))
        if db.execute("SELECT COUNT(*) FROM entries e JOIN sources s ON e.number=s.number "
                      "WHERE e.start < 0 OR e.stop <= e.start OR e.stop > s.source_bytes / ?",
                      (SAMPLE_DTYPE.itemsize,)).fetchone()[0]:
            raise ValueError("Invalid split slice")
        db.execute("INSERT INTO metadata VALUES ('planned','true')")


def unknown_receipts(member, root, sources):
    """Only open receipts for parts absent from the already sealed split index."""
    for directory, _, files in os.walk(member / "jobs"):
        if "receipt.json" in files:
            parent = Path(directory)
            prefix = parent.relative_to(root).as_posix()
            if any(name.startswith("samples-") and name.endswith(".npy") and f"{prefix}/{name}" not in sources
                   for name in files):
                yield parent / "receipt.json"


def pack_part(runtime, item):
    number, key, expected_sha, size = item
    source = runtime / key
    data = source.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError(f"Original payload SHA/size differs: {key}")
    original = np.load(io.BytesIO(data), allow_pickle=False)
    if len(original) != size // SAMPLE_DTYPE.itemsize:
        raise ValueError(f"Original payload row count differs: {key}")
    fields = pack_windows(original)
    stream = io.BytesIO()
    np.savez(stream, **fields)
    stored = stream.getvalue()
    with np.load(io.BytesIO(stored), allow_pickle=False) as compact:
        restored = unpack(compact)
    if original.tobytes() != restored.tobytes():
        raise ValueError(f"Compact shard changed a market value, validity bit, clock or window: {key}")
    return len(original), len(fields["pool"]), len(fields["exceptions"]), stored, number


def publish_container(output, number, parts):
    """Publish a sequential bundle after readback verifies the exact packed bytes."""
    target = part_path(output, number)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".partial")
    checksum = hashlib.sha256()
    rows = []
    with temporary.open("wb") as stream:
        for windows, bars, exceptions, stored, source_number in parts:
            offset = stream.tell()
            stream.write(stored)
            checksum.update(stored)
            rows.append((windows, bars, exceptions, len(stored), hashlib.sha256(stored).hexdigest(),
                         number, offset, source_number))
        size = stream.tell()
        stream.flush()
        os.fsync(stream.fileno())
    sha = checksum.hexdigest()
    if file_hash(temporary) != sha or temporary.stat().st_size != size:
        raise ValueError(f"Compact container failed disk readback: {number}")
    if target.exists():
        # The previous process may have stopped after publish and before its
        # catalog transaction. Admit only an identical orphan.
        if file_hash(target) != sha:
            raise ValueError(f"Uncommitted compact part differs: {number}")
        temporary.unlink()
    else:
        temporary.replace(target)
    return rows, (number, sha, size)


class Progress:
    def __init__(self, output):
        self.output = output
        self.started = time.time()
        self.fields = {}

    def __call__(self, stage, state="running", **fields):
        self.fields.update(fields)
        write_json(self.output / "progress.json", dict(self.fields, stage=stage, state=state,
                                                       updated=time.time(), started=self.started))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--pilot", type=int, default=0, help="Stop after this many verified files; no release seal")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    runtime = args.cohort.resolve().parent
    if not (1 <= args.workers <= 64 and args.pilot >= 0 and output.parent == runtime):
        raise ValueError("Use a new sibling runtime and 1–64 workers")
    proof = read_json(runtime / f"deployment-{args.source_revision[:9]}.json")
    snapshot = Path(__file__).resolve().parents[3]
    if proof["revision"] != args.source_revision or any(file_hash(snapshot / p) != sha for p, sha in proof["files"].items()):
        raise ValueError("Immutable workstation snapshot differs")
    output.mkdir(exist_ok=True)
    with lease(output):
        if (output / "certificate.json").exists():
            raise FileExistsError("Release already sealed")
        progress = Progress(output)
        try:
            progress("Checking source and split", workers=args.workers)
            authority, indices = read_authority(args.cohort)
            binding = dict(version=VERSION, source=args.source_revision, authority=authority)
            identity = output / "manifest.json"
            if identity.exists():
                if read_json(identity) != binding:
                    raise ValueError("Resume requires the original split, source and build identity")
            else:
                write_json(identity, binding)
            db_path = output / "catalog.sqlite"
            with sqlite3.connect(db_path) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                tables = db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone()
                if not tables or not db.execute("SELECT 1 FROM metadata WHERE name='planned'").fetchone():
                    catalog(db, runtime, authority, indices, progress, args.workers)
                total, source_bytes = db.execute("SELECT COUNT(*), SUM(source_bytes) FROM sources").fetchone()
                done, input_bytes, compact_bytes, windows, bars, exceptional = db.execute(
                    """SELECT COUNT(*), COALESCE(SUM(source_bytes),0), COALESCE(SUM(output_bytes),0),
                       COALESCE(SUM(windows),0), COALESCE(SUM(bars),0), COALESCE(SUM(exceptions),0)
                       FROM sources WHERE output_sha IS NOT NULL""").fetchone()
                initial_bytes, started = input_bytes, time.perf_counter()
                limit = min(total, args.pilot) if args.pilot else total
                if args.pilot:
                    # Seed-independent coverage across the whole inventory,
                    # including validation and the liquid/coarse supplements.
                    db.execute("CREATE TEMP TABLE pilot_ids(number INTEGER PRIMARY KEY)")
                    ids = np.linspace(0, total-1, limit, dtype=np.int64)
                    db.executemany("INSERT INTO pilot_ids VALUES (?)", ((int(i),) for i in ids))
                    selected = "number IN (SELECT number FROM pilot_ids)"
                    pilot_done, pilot_bytes = db.execute(
                        "SELECT COUNT(output_sha), SUM(source_bytes) FROM sources WHERE "+selected
                    ).fetchone()
                    source_bytes = pilot_bytes
                else:
                    selected = "1=1"
                    pilot_done = done
                progress("Packing and reading back", files_done=done, files_total=limit, source_bytes=source_bytes,
                         input_bytes=input_bytes, compact_bytes=compact_bytes, windows=windows, unique_bars=bars)
                work = partial(pack_part, runtime)
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    if done:
                        cursor = db.execute("SELECT number,sha,bytes FROM containers")
                        verify_total = db.execute("SELECT COUNT(*) FROM containers").fetchone()[0]
                        verified = 0
                        while batch := cursor.fetchmany(CHUNK):
                            if (output/"STOP").exists():
                                raise InterruptedError("STOP")
                            for _ in pool.map(partial(verify_container, output), batch):
                                verified += 1
                            progress("Verifying already committed containers", rechecked=verified, verify_total=verify_total)
                        started = time.perf_counter()
                    while pilot_done < limit and not (output/"STOP").exists():
                        if shutil.disk_usage(output).free < 8 * 1024**3:
                            raise OSError("Less than 8 GiB free for pending writes")
                        batch = db.execute(
                            "SELECT number,source_key,source_sha,source_bytes FROM sources "
                            f"WHERE output_sha IS NULL AND {selected} ORDER BY number LIMIT ?",
                            (min(CHUNK,limit-pilot_done),)).fetchall()
                        prefix = int((np.cumsum([item[3] for item in batch]) <= MAX_BATCH_INPUT_BYTES).sum())
                        batch = batch[:max(1, prefix)]
                        result, container = publish_container(output, batch[0][0], pool.map(work,batch))
                        with db:
                            db.executemany(
                                """UPDATE sources SET windows=?,bars=?,exceptions=?,output_bytes=?,output_sha=?,container=?,offset=?
                                   WHERE number=?""", result)
                            db.execute("INSERT INTO containers VALUES (?,?,?)", container)
                        done += len(result)
                        pilot_done += len(result)
                        input_bytes += sum(item[3] for item in batch)
                        compact_bytes += sum(row[3] for row in result)
                        windows += sum(row[0] for row in result)
                        bars += sum(row[1] for row in result)
                        exceptional += sum(row[2] for row in result)
                        progress("Packing and reading back", files_done=done, input_bytes=input_bytes,
                                 compact_bytes=compact_bytes, windows=windows, unique_bars=bars,
                                 bytes_per_second=(input_bytes-initial_bytes)/max(.001,time.perf_counter()-started))
                if pilot_done != limit:
                    progress("Gracefully stopped", "stopped")
                    return
                if args.pilot:
                    progress("Pilot verified", "complete")
                    return
                if windows != authority["inventory_samples"]:
                    raise ValueError("Published windows differ from certified inventory")
                containers = db.execute("SELECT COUNT(*) FROM containers").fetchone()[0]
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.execute("PRAGMA journal_mode=DELETE")
            db.close()
            seal = dict(version=VERSION, source=args.source_revision, split_sha256=authority["split_sha256"],
                        split_hash=authority["split_hash"], counts=authority["counts"], source_files=total,
                        containers=containers,
                        windows=windows, purged_crossing=authority["purged_crossing"], unique_bars=bars,
                        exceptional_windows=exceptional, source_payload_bytes=source_bytes,
                        compact_payload_bytes=compact_bytes, catalog_sha256=file_hash(db_path))
            write_json(output / "certificate.json", dict(seal, hash=digest(seal)))
            progress("Release sealed", "complete")
        except InterruptedError:
            progress("Gracefully stopped", "stopped")
        except BaseException as error:
            progress("Failed", "failed", error=repr(error))
            raise


if __name__ == "__main__":
    main()
