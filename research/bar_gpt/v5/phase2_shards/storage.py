"""Receipt-first publication, indexed recovery and exact block-ratio selection."""

import json
import os
from pathlib import Path
import sqlite3

import numpy as np

from .contract import BLOCK, BAR, DECISION, Context, digest, file_hash, write_json

MAX_ROWS = 16 * 1024**2 // BAR.itemsize


def save_array(directory, name, array):
    """Write and fsync one immutable NPY payload before recording its hash."""
    path = directory / name
    with path.open("wb") as handle:
        np.save(handle, array, allow_pickle=False)
        handle.flush()
        os.fsync(handle.fileno())
    return dict(
        name=name, bytes=path.stat().st_size, sha256=file_hash(path), rows=len(array)
    )


def publish(root, stream, unit, arrays, starts, blocks, splits, context, timing):
    """Seal one source range. Payloads <=16 MiB; receipt is the commit marker.

    Bars are five BAR[N] arrays. Blocks are DECISION[B,256], with Int64 causal
    indices into the stream; split=2 rows are explicitly purged, never selected.
    Atomic directory rename exposes the complete receipt and checkpoint at once.
    """
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
    checkpoint = temporary / "context.npz"
    with checkpoint.open("wb") as handle:
        np.savez(
            handle,
            counts=np.asarray(context.counts),
            pending=context.pending,
            **{f"tail{i}": tail for i, tail in enumerate(context.tails)},
        )
        handle.flush()
        os.fsync(handle.fileno())
    files.append(
        dict(
            name=checkpoint.name,
            bytes=checkpoint.stat().st_size,
            sha256=file_hash(checkpoint),
            rows=0,
        )
    )
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
        )


class Catalog:
    """Single coordinator owns this WAL ledger; workers only publish receipts.

    A crash after rename but before registration is recovered on startup.
    Selection is append-only, exactly 3:1 within each train/validation split.
    Unselected candidates remain visible as balance-deferred, not silent drops.
    """

    def __init__(self, root):
        self.root = Path(root)
        self._cached = None
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
        """)

    def register(self, receipt):
        """Idempotent receipt registration; detect conflicting immutable units."""
        row = self.db.execute(
            "SELECT hash FROM units WHERE stream=? AND unit=?",
            (receipt["stream"], receipt["unit"]),
        ).fetchone()
        if row:
            if row[0] != receipt["hash"]:
                raise ValueError("Conflicting publication identity")
            return
        if receipt["hash"] != digest({k: v for k, v in receipt.items() if k != "hash"}):
            raise ValueError("Receipt seal mismatch")
        with self.db:
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
        self.select()
        self._cached = None

    def select(self):
        """Commit balanced groups deterministically in publication order."""
        order = self.db.execute(
            "SELECT coalesce(max(selection),-1)+1 FROM blocks"
        ).fetchone()[0]
        for split in (0, 1):
            counts = [
                self.db.execute(
                    "SELECT count(*) FROM blocks WHERE split=? AND liquid=? AND selection IS NULL",
                    (split, liquid),
                ).fetchone()[0]
                for liquid in (1, 0)
            ]
            groups = min(counts[0] // 3, counts[1])
            if not groups:
                continue
            with self.db:
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

    def recover(self):
        """Discover only receipt directories; payload hashes are checked on read."""
        for path in sorted((self.root / "streams").glob("*/*/receipt.json")):
            self.register(json.loads(path.read_text()))

    def stats(self):
        """Small bounded aggregate used by scheduler and live panel."""
        if self._cached is not None:
            return self._cached
        result = dict(
            bytes=self.db.execute(
                "SELECT coalesce(sum(bytes),0) FROM units"
            ).fetchone()[0],
            units=self.db.execute("SELECT count(*) FROM units").fetchone()[0],
        )
        result["blocks"] = [
            dict(split=s, liquid=l, total=n, selected=k)
            for s, l, n, k in self.db.execute(
                "SELECT split,liquid,count(*),count(selection) FROM blocks GROUP BY split,liquid"
            )
        ]
        self._cached = result
        return result

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
