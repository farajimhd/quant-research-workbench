"""Atomic monthly mmap pools, cached block readers, and a compact selection ledger."""

import json
import os
from pathlib import Path
import sqlite3

import numpy as np

from .contract import BAR, BLOCK, FRAMES, ORIGIN, Compiler, Geometry, VERSION
from ..shards.contract import digest, file_hash, write_json
from ..shards.phase1_contract import pack
from ..shards.storage import adjust_splits


def read_receipt(directory, binding=None):
    path = Path(directory) / "receipt.json"
    value = json.loads(path.read_text())
    if value["hash"] != digest({k: v for k, v in value.items() if k != "hash"}):
        raise ValueError("Monthly receipt seal changed")
    if binding is not None and value["binding"] != binding:
        raise ValueError("Monthly source/view contract changed")
    for item in value["files"]:
        name = item["name"]
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("Unsafe monthly payload name")
    return value


def publish(root, ticker, month, liquid, binding, source_hash, compiled, geometry):
    """Publish one month; no per-query or per-origin durable file transactions."""
    key = digest(ticker)[:24]
    final = Path(root) / "shards" / key / month
    identity = digest([binding, ticker, month, source_hash])
    if final.exists():
        receipt = read_receipt(final, binding)
        if receipt["identity"] != identity:
            raise ValueError("Existing monthly source identity differs")
        return receipt
    temporary = final.with_name(final.name + ".partial")
    temporary.mkdir(parents=True, exist_ok=True)
    marker = temporary / "publication.json"
    if marker.exists():
        if json.loads(marker.read_text())["identity"] != identity:
            raise ValueError("Foreign incomplete publication")
    elif any(temporary.iterdir()):
        raise ValueError("Unidentified incomplete publication")
    else:
        write_json(marker, dict(identity=identity))
    files = []

    def save(name, array):
        path = temporary / name
        with path.open("wb") as stream:
            np.save(stream, np.ascontiguousarray(array), allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        item = dict(name=name, bytes=path.stat().st_size, sha256=file_hash(path))
        files.append(item)
        return name

    views = []
    # Each bounded part contains <=128 MiB; descriptors preserve one shared view.
    for frame, rows in enumerate(compiled["views"]):
        limit = max(1, 128 * 1024**2 // BAR.itemsize)
        parts = []
        for start in range(0, len(rows), limit):
            selected = rows[start : start + limit]
            name = save(f"view-{frame}-{start:010d}.npy", selected)
            parts.append(dict(name=name, start=start, rows=len(selected)))
        views.append(dict(rows=len(rows), parts=parts))
    save("origins.npy", compiled["blocks"])
    save("pending.npy", compiled["pending"])
    receipt = dict(
        version=VERSION,
        identity=identity,
        binding=binding,
        ticker=ticker,
        ticker_key=key,
        month=month,
        liquid=int(liquid),
        source_hash=source_hash,
        geometry=geometry.manifest(),
        views=views,
        blocks=len(compiled["blocks"]),
        splits=compiled["splits"],
        origins=len(compiled["blocks"]) * BLOCK,
        pool_starts=compiled["pool_starts"],
        cache_slices=compiled["cache_slices"],
        pending_key=compiled["pending_key"],
        stats=compiled["stats"],
        files=files,
        bytes=sum(f["bytes"] for f in files),
    )
    receipt = json.loads(json.dumps(receipt))
    receipt["hash"] = digest(receipt)
    write_json(temporary / "receipt.json", receipt)
    temporary.rename(final)
    return receipt


class MonthReader:
    """Verify each used file once; every origin reads a causal slice of cached pools."""

    def __init__(self, directory, binding=None):
        self.directory = Path(directory)
        self.receipt = read_receipt(directory, binding)
        self.geometry = Geometry(
            tuple(self.receipt["geometry"]["history"]),
            self.receipt["geometry"]["cutoff"],
        )
        if self.receipt["version"] != VERSION or self.receipt["geometry"] != json.loads(
            json.dumps(self.geometry.manifest())
        ):
            raise ValueError("Unknown cached-context geometry")
        self.files = {item["name"]: item for item in self.receipt["files"]}
        self.arrays = {}
        self.views = {}

    def array(self, name):
        if name not in self.arrays:
            path, item = self.directory / name, self.files[name]
            if (
                path.stat().st_size != item["bytes"]
                or file_hash(path) != item["sha256"]
            ):
                raise ValueError("Monthly payload mutation")
            self.arrays[name] = np.load(path, mmap_mode="r", allow_pickle=False)
        return self.arrays[name]

    def view(self, frame):
        if frame not in self.views:
            descriptor = self.receipt["views"][frame]
            pieces = [self.array(p["name"]) for p in descriptor["parts"]]
            self.views[frame] = (
                pieces[0]
                if len(pieces) == 1
                else np.concatenate(pieces) if pieces else np.empty(0, dtype=BAR)
            )
            if (
                self.views[frame].dtype != BAR
                or len(self.views[frame]) != descriptor["rows"]
            ):
                raise ValueError("Cached view geometry changed")
        return self.views[frame]

    def slice(self, frame, start, stop):
        """Load only intersecting mmap parts, never concatenate a whole month."""
        descriptor = self.receipt["views"][frame]
        if not 0 <= start <= stop <= descriptor["rows"]:
            raise ValueError("Invalid cached view slice")
        position, pieces = 0, []
        for part in descriptor["parts"]:
            if part["start"] != position or part["rows"] <= 0:
                raise ValueError("Noncontiguous cached view parts")
            end = position + part["rows"]
            if position < stop and end > start:
                a = self.array(part["name"])
                if a.dtype != BAR or a.shape != (part["rows"],):
                    raise ValueError("Cached view geometry changed")
                pieces.append(
                    a[max(0, start - position) : min(part["rows"], stop - position)]
                )
            position = end
        if position != descriptor["rows"]:
            raise ValueError("Incomplete cached view parts")
        return (
            pieces[0]
            if len(pieces) == 1
            else np.concatenate(pieces) if pieces else np.empty(0, dtype=BAR)
        )

    def block(self, index):
        blocks = self.array("origins.npy")
        if blocks.dtype != ORIGIN or blocks.shape != (self.receipt["blocks"], BLOCK):
            raise ValueError("A block must contain exactly4096 origin records")
        if not 0 <= index < len(blocks):
            raise IndexError("Block index out of range")
        origins = blocks[index]
        pools, latest = [], origins["latest"].copy()
        for frame, history in enumerate(self.geometry.history):
            lo, hi = (
                int(latest[:, frame].min()) - history + 1,
                int(latest[:, frame].max()) + 1,
            )
            if lo < 0 or hi > self.receipt["views"][frame]["rows"]:
                raise ValueError("Origin is missing its cached context")
            pools.append(self.slice(frame, lo, hi))
            latest[:, frame] -= lo
            if np.any(pools[-1]["end_us"][latest[:, frame]] > origins["end_us"]):
                raise ValueError("Cached context contains a future bar")
        if not np.array_equal(pools[0]["end_us"][latest[:, 0]], origins["end_us"]):
            raise ValueError("Origin must equal its last completed 1s close")
        return CachedBlock(
            origins["end_us"],
            latest,
            pools,
            self.geometry,
            self.receipt["splits"][index],
        )

    def restore(self):
        compiler = Compiler(self.geometry)
        for frame, begin in enumerate(self.receipt["cache_slices"]):
            compiler.views[frame] = self.slice(
                frame, begin, self.receipt["views"][frame]["rows"]
            ).copy()
            compiler.starts[frame] = self.receipt["pool_starts"][frame] + begin
        compiler.pending = self.array("pending.npy").copy()
        if compiler.pending.dtype != ORIGIN or len(compiler.pending) >= BLOCK:
            raise ValueError("Invalid pending origin checkpoint")
        key = self.receipt["pending_key"]
        compiler.pending_key = tuple(key) if key is not None else None
        return compiler


class CachedBlock:
    """Shared arrays in RAM/mmap; origin access does no filesystem or CH queries."""

    def __init__(self, ends, latest, views, geometry, split=0):
        self.ends, self.latest, self.views, self.geometry = (
            ends,
            latest,
            views,
            geometry,
        )
        self.split = split

    def context(self, origin, frame):
        if not 0 <= origin < len(self.ends) or not 0 <= frame < 5:
            raise IndexError("Origin/frame out of range")
        end = int(self.latest[origin, frame]) + 1
        result = self.views[frame][end - self.geometry.history[frame] : end]
        if (
            len(result) != self.geometry.history[frame]
            or result["end_us"][-1] > self.ends[origin]
        ):
            raise ValueError("Invalid origin context")
        return result

    def windows(self, origins, frame, *, actions, stride=64, count=None):
        """Batch phase-one 64-bar records on EACH origin's split basis.

        Token overlap is a reader choice inside the declared cached history.
        Returns [origins,windows] records; nine UTC features/scaling stay in the
        existing phase-one transform, never in the extractor.
        """
        if stride < 1 or int(stride) != stride or not 0 <= frame < 5:
            raise ValueError("Positive tokenizer stride required")
        history = self.geometry.history[frame]
        count = (history - 64) // stride + 1 if count is None else count
        if count < 1 or int(count) != count or 64 + (count - 1) * stride > history:
            raise ValueError("Requested tokenizer context exceeds the cached budget")
        origins = np.atleast_1d(origins)
        if (
            origins.dtype.kind not in "iu"
            or origins.ndim != 1
            or np.any(origins < 0)
            or np.any(origins >= len(self.ends))
        ):
            raise IndexError("Origin indices must be in-range integers")
        origins = origins.astype(np.int64)
        offsets = np.arange(count - 1, -1, -1) * stride
        ends = self.latest[origins, frame, None] - offsets
        indices = ends[..., None] - np.arange(63, -1, -1)
        bars = self.views[frame][indices]
        shape = bars.shape[:2]
        flat = bars.reshape(-1, 64)
        result = pack(
            flat["values"], flat["valid"], flat["end_us"], FRAMES[frame], self.split
        )
        adjust_splits(result, actions, origin_us=np.repeat(self.ends[origins], count))
        return result.reshape(shape)


class Catalog:
    """One coordinator, monthly acknowledgements, exact3:1 full-block selection."""

    def __init__(self, root):
        self.root = Path(root)
        self.db = sqlite3.connect(self.root / "catalog.sqlite")
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS units(path TEXT PRIMARY KEY,seal TEXT,bytes INTEGER,
            source_origins INTEGER,warmup INTEGER,boundary INTEGER);
          CREATE TABLE IF NOT EXISTS tails(ticker TEXT PRIMARY KEY,month TEXT,pending INTEGER);
          CREATE TABLE IF NOT EXISTS blocks(path TEXT,ordinal INTEGER,split INTEGER,
            liquid INTEGER,selection INTEGER UNIQUE,PRIMARY KEY(path,ordinal));
          CREATE INDEX IF NOT EXISTS pending ON blocks(split,liquid,selection);""")
        # One restart reconciliation; progress polling never rescans the ledger.
        values = self.db.execute(
            "SELECT count(*),coalesce(sum(bytes),0),coalesce(sum(source_origins),0),coalesce(sum(warmup),0),coalesce(sum(boundary),0) FROM units"
        ).fetchone()
        self.totals = dict(
            zip(
                (
                    "units",
                    "bytes",
                    "source_origins",
                    "warmup_deferred",
                    "boundary_tail_deferred",
                ),
                values,
            )
        )
        self.totals["candidate_blocks"], self.totals["selected_blocks"] = (
            self.db.execute("SELECT count(*),count(selection) FROM blocks").fetchone()
        )
        self.order = self.totals["selected_blocks"]
        self.pending = {
            (split, liquid): count
            for split, liquid, count in self.db.execute(
                "SELECT split,liquid,count(*) FROM blocks WHERE selection IS NULL GROUP BY split,liquid"
            )
        }
        self.tails = {
            ticker: (month, pending)
            for ticker, month, pending in self.db.execute(
                "SELECT ticker,month,pending FROM tails"
            )
        }

    def register(self, path, receipt):
        relative = str(Path(path).relative_to(self.root))
        prior = self.db.execute(
            "SELECT seal FROM units WHERE path=?", (relative,)
        ).fetchone()
        if prior:
            if prior[0] != receipt["hash"]:
                raise ValueError("Registered month changed")
            return
        pending, order = self.pending.copy(), self.order
        prior_tail = self.tails.get(receipt["ticker"])
        if prior_tail and prior_tail[0] >= receipt["month"]:
            raise ValueError("Monthly registration must preserve ticker chronology")
        for split in receipt["splits"]:
            key = (split, receipt["liquid"])
            pending[key] = pending.get(key, 0) + 1
        with self.db:
            self.db.execute(
                "INSERT INTO units VALUES(?,?,?,?,?,?)",
                (
                    relative,
                    receipt["hash"],
                    receipt["bytes"],
                    receipt["stats"]["source_origins"],
                    receipt["stats"]["warmup_deferred"],
                    receipt["stats"]["boundary_tail_deferred"],
                ),
            )
            self.db.executemany(
                "INSERT INTO blocks VALUES(?,?,?,?,NULL)",
                (
                    (relative, i, split, receipt["liquid"])
                    for i, split in enumerate(receipt["splits"])
                ),
            )
            for split in (0, 1):
                counts = [pending.get((split, liquid), 0) for liquid in (1, 0)]
                groups = min(counts[0] // 3, counts[1])
                for liquid, count in ((1, groups * 3), (0, groups)):
                    ids = self.db.execute(
                        "SELECT rowid FROM blocks WHERE split=? AND liquid=? AND selection IS NULL ORDER BY rowid LIMIT ?",
                        (split, liquid, count),
                    ).fetchall()
                    self.db.executemany(
                        "UPDATE blocks SET selection=? WHERE rowid=?",
                        ((order + i, row[0]) for i, row in enumerate(ids)),
                    )
                    order += count
                    pending[(split, liquid)] = pending.get((split, liquid), 0) - count
            self.db.execute(
                "INSERT OR REPLACE INTO tails VALUES(?,?,?)",
                (
                    receipt["ticker"],
                    receipt["month"],
                    receipt["stats"]["pending_origins"],
                ),
            )
        self.pending, self.order = pending, order
        self.tails[receipt["ticker"]] = (
            receipt["month"],
            receipt["stats"]["pending_origins"],
        )
        for key, value in (
            ("units", 1),
            ("bytes", receipt["bytes"]),
            ("source_origins", receipt["stats"]["source_origins"]),
            ("warmup_deferred", receipt["stats"]["warmup_deferred"]),
            ("boundary_tail_deferred", receipt["stats"]["boundary_tail_deferred"]),
            ("candidate_blocks", receipt["blocks"]),
        ):
            self.totals[key] += value
        self.totals["selected_blocks"] = order

    def stats(self):
        return dict(
            self.totals,
            pending_tail_origins=sum(p[1] for p in self.tails.values()),
            selected_origins=self.order * BLOCK,
            unselected_complete_blocks=self.totals["candidate_blocks"] - self.order,
        )

    def export(self, binding):
        import polars as pl

        rows = self.db.execute(
            "SELECT path,ordinal,split,liquid,selection FROM blocks WHERE selection IS NOT NULL ORDER BY selection"
        ).fetchall()
        path = self.root / f"selection-{len(rows):012d}-{digest(rows)[:16]}.parquet"
        if not path.exists():
            temporary = path.with_suffix(".partial")
            pl.DataFrame(
                rows,
                schema={
                    "path": pl.String,
                    "ordinal": pl.Int64,
                    "split": pl.Int64,
                    "liquid": pl.Int64,
                    "selection": pl.Int64,
                },
                orient="row",
            ).write_parquet(temporary)
            temporary.replace(path)
        elif pl.read_parquet(path).rows() != rows:
            raise ValueError("Published selection changed")
        body = dict(
            binding=binding,
            path=path.name,
            sha256=file_hash(path),
            blocks=len(rows),
            origins=len(rows) * BLOCK,
            mix="3 liquid : 1 less-liquid per split",
            input_context_only=True,
            future_target_purge_required=True,
        )
        body["hash"] = digest(body)
        write_json(self.root / "selection.json", body)
        return body
