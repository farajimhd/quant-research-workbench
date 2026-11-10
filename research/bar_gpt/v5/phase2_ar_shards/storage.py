"""Atomic monthly mmap pools, cached block readers, and a compact selection ledger."""

import json
import os
from pathlib import Path

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


def publish(root, ticker, month, liquid, binding, source_hash, compiled, geometry, observer=None):
    """Publish one session snapshot; no per-query/per-origin file transactions."""
    basis = compiled.get("price_basis")
    if basis:
        from ..shards.sql import clock

        day = basis["session_date"]
        ends = compiled["blocks"]["end_us"]
        if (
            month != day
            or basis["basis_us"] != clock(day, 4)
            or np.any(ends <= clock(day, 4))
            or np.any(ends > clock(day, 20))
        ):
            raise ValueError(
                "Publication must contain one session on its own split basis"
            )
        if any(s != int(day[:4] == "2026") for s in compiled["splits"]):
            raise ValueError("Publication changes trading-session year split")
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
    total_files = sum((len(rows)+(128*1024**2//BAR.itemsize)-1)//(128*1024**2//BAR.itemsize) for rows in compiled["views"]) + 2

    def notify(stage, **fields):
        if observer is not None:
            try:observer(state=stage,progress_done=len(files),progress_total=total_files,
                         bytes_done=sum(item["bytes"] for item in files),**fields)
            except Exception:pass  # Optional observer never controls publication.

    def save(name, array):
        path = temporary / name
        notify("Write", detail=name)
        with path.open("wb") as stream:
            np.save(stream, np.ascontiguousarray(array), allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        item = dict(name=name, bytes=path.stat().st_size, sha256=file_hash(path))
        files.append(item)
        notify("Verify", detail=name)
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
        session_date=basis["session_date"] if basis else None,
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
        sampling=compiled.get("sampling"),
        independent_unit=bool(compiled.get("independent_unit", False)),
        price_basis=compiled.get("price_basis"),
        files=files,
        bytes=sum(f["bytes"] for f in files),
    )
    receipt = json.loads(json.dumps(receipt))
    receipt["hash"] = digest(receipt)
    notify("Commit", detail="Sealing session receipt")
    write_json(temporary / "receipt.json", receipt)
    temporary.rename(final)
    notify("Published", detail="Session committed")
    return receipt


class ShardReader:
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
        self.price_basis = self.receipt.get("price_basis")

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
        if self.price_basis:
            from ..shards.sql import clock

            day = self.price_basis["session_date"]
            if np.any(origins["end_us"] <= clock(day, 4)) or np.any(
                origins["end_us"] > clock(day, 20)
            ):
                raise ValueError("Block mixes trading sessions")
        return CachedBlock(
            origins["end_us"],
            latest,
            pools,
            self.geometry,
            self.receipt["splits"][index],
            self.price_basis,
        )

    def restore(self):
        if self.price_basis:
            raise ValueError(
                "Session-basis snapshot cannot be restored as raw generation cache"
            )
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


# Name compatibility only; the same strict version/basis checks still apply.
MonthReader = ShardReader


class CachedBlock:
    """Shared arrays in RAM/mmap; origin access does no filesystem or CH queries."""

    def __init__(self, ends, latest, views, geometry, split=0, price_basis=None):
        self.ends, self.latest, self.views, self.geometry = (
            ends,
            latest,
            views,
            geometry,
        )
        self.split = split
        self.price_basis = price_basis

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
        if self.price_basis:
            if digest(actions) != self.price_basis["actions_hash"]:
                raise ValueError("Frozen session action catalog differs")
            # Already adjusted to the one basis shared by this session's origins.
        else:
            adjust_splits(
                result, actions, origin_us=np.repeat(self.ends[origins], count)
            )
        return result.reshape(shape)


from .ledger import Catalog  # Single coordinator, file-backed sealed ledger.
