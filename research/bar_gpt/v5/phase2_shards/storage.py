"""Receipt-first publication, indexed recovery and exact block-ratio selection."""

import hashlib
from contextlib import nullcontext
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import time

import numpy as np

from .contract import BLOCK, BAR, DECISION, Context, digest, file_hash, write_json

MAX_ROWS = 16 * 1024**2 // BAR.itemsize


def save_array(directory, name, array):
    """Serialize one <=16 MiB array, then write/fsync its exact hashed bytes.

    Hashing the serialized source avoids reopening every new file under parallel
    publication. Readers/audits still verify the on-disk payload against this
    source hash. Serialization memory is bounded by one segment per I/O worker.
    """
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=False)
    return save_payload(directory, name, buffer.getbuffer(), len(array))


def save_payload(directory, name, payload, rows):
    """Publish a bounded byte buffer durably, preserving the existing file format."""
    path = directory / name
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return dict(
        name=name,
        bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        rows=rows,
    )


def publish(root, stream, unit, arrays, starts, blocks, splits, context, timing):
    """Seal one source range. Payloads <=16 MiB; receipt is the commit marker.

    Bars are five BAR[N] arrays. Blocks are DECISION[B,256], with Int64 causal
    indices into the stream; split=2 rows are explicitly purged, never selected.
    Atomic directory rename exposes the complete receipt and checkpoint at once.
    """
    began = time.monotonic()
    timing = dict(timing)
    timing["publication_queue_seconds"] = max(
        0, began - timing.pop("_enqueued_clock", began)
    )
    parent = Path(root) / "streams" / stream["key"]
    parent.mkdir(parents=True, exist_ok=True)
    final = parent / f"{unit:04d}"
    if final.exists():
        return json.loads((final / "receipt.json").read_text())
    temporary = parent / f"{unit:04d}.partial"
    temporary.mkdir(exist_ok=True)
    # A previous incomplete attempt is not authoritative. Overwrite only its
    # deterministic task-owned filenames; no published directory is deleted.
    files, segments = [], []
    for frame, (rows, start) in enumerate(zip(arrays, starts)):
        for offset in range(0, len(rows), MAX_ROWS):
            item = save_array(
                temporary,
                f"bars-{frame}-{offset:09d}.npy",
                rows[offset : offset + MAX_ROWS],
            )
            files.append(item)
            segments.append(dict(frame=frame, start=start + offset, **item))
    for offset in range(0, len(blocks), 128):
        files.append(
            save_array(
                temporary, f"blocks-{offset:09d}.npy", blocks[offset : offset + 128]
            )
        )
    # Build the small ZIP/NPZ checkpoint in RAM instead of many seeks/writes on
    # the training SSD. Its schema and crash-safe fsync boundary are unchanged.
    checkpoint = io.BytesIO()
    np.savez(
        checkpoint,
        counts=np.asarray(context.counts),
        pending=context.pending,
        initialized=np.asarray(context.initialized),
        **{f"tail{i}": tail for i, tail in enumerate(context.tails)},
    )
    files.append(save_payload(temporary, "context.npz", checkpoint.getbuffer(), 0))
    timing["publication_seconds"] = time.monotonic() - began
    receipt = dict(
        stream=stream["key"],
        ticker=stream["ticker"],
        year=stream["year"],
        liquid=stream["liquid"],
        unit=unit,
        files=files,
        segments=segments,
        splits=splits.tolist(),
        blocks=len(blocks),
        bytes=sum(f["bytes"] for f in files),
        timing=timing,
        deferred_tail=len(context.pending),
        counts=context.counts,
    )
    receipt["hash"] = digest(receipt)
    write_json(temporary / "receipt.json", receipt)
    temporary.rename(final)
    return receipt


def restore(root, stream, unit):
    """Restore only the checkpoint belonging to the last catalogued receipt."""
    if unit < 0:
        return Context()
    path = Path(root) / "streams" / stream["key"] / f"{unit:04d}" / "context.npz"
    with np.load(path, allow_pickle=False) as saved:
        return Context(
            saved["counts"].tolist(),
            [saved[f"tail{i}"].copy() for i in range(5)],
            saved["pending"].copy(),
            # Legacy producers always warmed on unit zero, including empty
            # ranges. Never prepend history again to their existing indices.
            initialized=bool(saved["initialized"]) if "initialized" in saved else True,
        )


class Catalog:
    """Single coordinator owns this WAL ledger; workers only publish receipts.

    A crash after rename but before registration is recovered on startup.
    Selection is append-only, exactly 3:1 within each train/validation split.
    Unselected candidates remain visible as balance-deferred, not silent drops.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.db = sqlite3.connect(self.root / "catalog.sqlite", timeout=30)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS units(stream TEXT, unit INTEGER, bytes INTEGER,
              hash TEXT, PRIMARY KEY(stream,unit));
            CREATE TABLE IF NOT EXISTS blocks(stream TEXT,unit INTEGER,ordinal INTEGER,
              liquid INTEGER,split INTEGER,selection INTEGER UNIQUE,
              PRIMARY KEY(stream,unit,ordinal));
            CREATE INDEX IF NOT EXISTS pending ON blocks(split,liquid,selection);
            CREATE TABLE IF NOT EXISTS stream_checks(stream TEXT PRIMARY KEY,
              trades INTEGER, proof TEXT, skipped INTEGER);
        """)
        # Reconcile once on open, then update counters from committed deltas.
        # Repeated GROUP BY scans would otherwise grow with a multi-TB corpus.
        self.used, self.unit_count = self.db.execute(
            "SELECT coalesce(sum(bytes),0),count(*) FROM units"
        ).fetchone()
        self.totals = {
            (s, l): [n, k]
            for s, l, n, k in self.db.execute(
                "SELECT split,liquid,count(*),count(selection) FROM blocks GROUP BY split,liquid"
            )
        }
        self.skipped_count = self.db.execute(
            "SELECT count(*) FROM stream_checks WHERE skipped=1"
        ).fetchone()[0]
        self.batching = False

    def transaction(self):
        """Use the caller's bounded batch transaction, or own a single commit."""
        return nullcontext() if self.batching else self.db

    def register_many(self, receipts):
        """Commit ready receipts and exact-ratio selection in one FULL-sync batch.

        Lanes cannot advance until this returns. A rollback leaves immutable
        directories recoverable as each lane's next orphan; counters roll back
        with SQLite, so progress cannot report uncommitted work.
        """
        if not receipts:
            return
        if self.batching:
            raise RuntimeError("Nested receipt batches are not allowed")
        before = self.used, self.unit_count, self.skipped_count, deepcopy(self.totals)
        self.batching = True
        try:
            with self.db:
                for receipt in receipts:
                    self.register(receipt)
                self.select()
        except BaseException:
            self.used, self.unit_count, self.skipped_count, self.totals = before
            raise
        finally:
            self.batching = False

    def check(self, key):
        """Return a frozen raw-event upper bound, never an economic label."""
        row = self.db.execute(
            "SELECT proof,skipped FROM stream_checks WHERE stream=?", (key,)
        ).fetchone()
        return (json.loads(row[0]), bool(row[1])) if row else (None, False)

    def remember_check(self, proof, skipped=False):
        """A trade-count bound can only skip streams with zero possible blocks.

        Each 1s bar requires a trade. Fewer than 256 raw trades in the entire
        core year cannot yield one full block. No reporting-status/condition
        exclusion is used in this count, and previous publications are kept.
        """
        key = proof["stream"]
        if proof["hash"] != digest({k: v for k, v in proof.items() if k != "hash"}):
            raise ValueError("Raw-trade proof seal changed")
        old, was_skipped = self.check(key)
        if old and old != proof:
            raise ValueError("Canonical raw-trade bound changed")
        if old and (was_skipped or not skipped):
            # Every later range repeats the same sealed yearly bound. Avoid an
            # identical INSERT/commit/fsync for each receipt; promotion to skipped
            # remains validated below, and a changed proof still fails above.
            return
        if skipped and (
            proof["raw_trade_events"] >= BLOCK
            or self.db.execute(
                "SELECT count(*) FROM blocks WHERE stream=?", (key,)
            ).fetchone()[0]
        ):
            raise ValueError("Cannot skip a stream that may contain a complete block")
        with self.transaction():
            self.db.execute(
                "INSERT OR REPLACE INTO stream_checks VALUES(?,?,?,?)",
                (
                    key,
                    proof["raw_trade_events"],
                    json.dumps(proof, sort_keys=True),
                    int(skipped or was_skipped),
                ),
            )
        if skipped and not was_skipped:
            self.skipped_count += 1

    def register(self, receipt):
        """Idempotent receipt registration; detect conflicting immutable units."""
        if receipt["hash"] != digest({k: v for k, v in receipt.items() if k != "hash"}):
            raise ValueError("Receipt seal mismatch")
        if receipt["timing"].get("raw_trade_proof"):
            self.remember_check(receipt["timing"]["raw_trade_proof"])
        row = self.db.execute(
            "SELECT hash FROM units WHERE stream=? AND unit=?",
            (receipt["stream"], receipt["unit"]),
        ).fetchone()
        if row:
            if row[0] != receipt["hash"]:
                raise ValueError("Conflicting publication identity")
            return
        with self.transaction():
            self.db.execute(
                "INSERT INTO units VALUES(?,?,?,?)",
                (receipt["stream"], receipt["unit"], receipt["bytes"], receipt["hash"]),
            )
            self.db.executemany(
                "INSERT INTO blocks VALUES(?,?,?,?,?,NULL)",
                [
                    (receipt["stream"], receipt["unit"], i, receipt["liquid"], split)
                    for i, split in enumerate(receipt["splits"])
                ],
            )
        self.used += receipt["bytes"]
        self.unit_count += 1
        for split, count in enumerate(np.bincount(receipt["splits"], minlength=3)):
            if count:
                self.totals.setdefault((split, receipt["liquid"]), [0, 0])[0] += int(
                    count
                )
        if not self.batching:
            self.select()

    def select(self):
        """Commit balanced groups deterministically in publication order."""
        order = self.db.execute(
            "SELECT coalesce(max(selection),-1)+1 FROM blocks"
        ).fetchone()[0]
        for split in (0, 1):
            counts = [self.totals.get((split, liquid), [0, 0]) for liquid in (1, 0)]
            counts = [total - selected for total, selected in counts]
            groups = min(counts[0] // 3, counts[1])
            if not groups:
                continue
            with self.transaction():
                for liquid, count in ((1, groups * 3), (0, groups)):
                    rows = self.db.execute(
                        "SELECT rowid FROM blocks WHERE split=? AND liquid=? AND selection IS NULL ORDER BY rowid LIMIT ?",
                        (split, liquid, count),
                    ).fetchall()
                    self.db.executemany(
                        "UPDATE blocks SET selection=? WHERE rowid=?",
                        [(order + i, row[0]) for i, row in enumerate(rows)],
                    )
                    order += len(rows)
            for liquid, count in ((1, groups * 3), (0, groups)):
                self.totals[(split, liquid)][1] += count

    def recover(self):
        """Recover the next orphan publication, not reopen every historical unit.

        A lane advances only after ledger registration, so at most its next unit
        can be published but unregistered. With an empty ledger this same loop
        rebuilds all consecutive receipts. Payload hashes remain checked on read.
        """
        directory = self.root / "streams"
        for stream in sorted(directory.iterdir()) if directory.exists() else ():
            if not stream.is_dir():
                continue
            unit = self.cursor(stream.name) + 1
            while (path := stream / f"{unit:04d}" / "receipt.json").exists():
                self.register(json.loads(path.read_text()))
                unit += 1
        # Repair a crash between registration and balanced-group selection.
        self.select()

    def stats(self):
        """Small bounded aggregate used by scheduler and live panel."""
        return dict(
            bytes=self.used,
            units=self.unit_count,
            skipped_streams=self.skipped_count,
            blocks=[
                dict(split=s, liquid=l, total=n, selected=k)
                for (s, l), (n, k) in sorted(self.totals.items())
            ],
        )

    def cursor(self, key):
        return self.db.execute(
            "SELECT coalesce(max(unit),-1) FROM units WHERE stream=?", (key,)
        ).fetchone()[0]

    def export(self, plan_hash):
        """Seal an immutable selected-block release; increasing cap appends data."""
        import polars as pl

        rows = self.db.execute(
            "SELECT stream,unit,ordinal,split,selection FROM blocks WHERE selection IS NOT NULL ORDER BY selection"
        ).fetchall()
        path = self.root / f"release-{len(rows):012d}.parquet"
        if not path.exists():
            frame = pl.DataFrame(
                rows,
                schema=["stream", "unit", "ordinal", "split", "selection"],
                orient="row",
            )
            temporary = path.with_suffix(".partial")
            frame.write_parquet(temporary, compression="zstd")
            temporary.replace(path)
        write_json(
            path.with_suffix(".json"),
            dict(
                plan=plan_hash,
                blocks=len(rows),
                sha256=file_hash(path),
                bytes=path.stat().st_size,
            ),
        )
        return str(path)


def read_window(root, stream, latest, frame, actions):
    """Read one full encoder view across immutable small NPY segments.

    This audit/reference reader deliberately verifies hashes. A future loader
    can cache validated mmaps and prefetch these exact same segment references.
    """
    from .contract import FRAMES, encoder_window

    if latest < 63:
        return None
    result = np.empty(64, dtype=BAR)
    filled = np.zeros(64, dtype=bool)
    for path in sorted((Path(root) / "streams" / stream).glob("*/receipt.json")):
        if path.parent.name.endswith(".partial"):
            continue
        receipt = json.loads(path.read_text())
        for item in receipt["segments"]:
            left, right = max(latest - 63, item["start"]), min(
                latest + 1, item["start"] + item["rows"]
            )
            if item["frame"] != frame or left >= right:
                continue
            file = path.parent / item["name"]
            if file_hash(file) != item["sha256"]:
                raise ValueError("Raw bar payload mutation")
            bars = np.load(file, mmap_mode="r", allow_pickle=False)
            target = slice(left - (latest - 63), right - (latest - 63))
            result[target] = bars[left - item["start"] : right - item["start"]]
            filled[target] = True
    if not filled.all():
        raise ValueError("Missing referenced bar segment")
    return encoder_window(result, FRAMES[frame], actions)
