"""Small pickle state for Windows loader workers, with unchanged sample order.

The sealed split JSON is the authority. This derived, hash-bound NPY index holds
only the five fields actually consumed by EligibleStream. Workers mmap shared OS
pages instead of each unpickling the complete nested manifest. Market payload
SHA256 verification and all per-record validation remain in data_v1 unchanged.
"""

import json
from pathlib import Path

import numpy as np

from .shards.contract import digest, file_hash, write_json


def attach(manifest, directory, progress=None):
    """Attach derived split indices to an in-memory manifest, without rewriting it.

    ``manifest`` is the already-verified split dict. Each input row has native
    path, start/stop record offsets, payload bytes and expected SHA256. Generated
    data belongs in ``directory`` under a runtime root. Its seal binds the
    original manifest hash; this optimization never changes that model contract.
    """
    directory = Path(directory) / ("loader-index-v1-" + manifest["manifest_hash"][:20])
    directory.mkdir(parents=True, exist_ok=True)
    seal = directory / "index.json"
    if seal.exists():
        result = json.loads(seal.read_text())
        if result["hash"] != digest({k: v for k, v in result.items() if k != "hash"}):
            raise ValueError("Loader index seal changed")
        if result["split_hash"] != manifest["manifest_hash"]:
            raise ValueError("Loader index belongs to a different split")
        for item in result["splits"].values():
            path = directory / item["file"]
            if (
                path.stat().st_size != item["bytes"]
                or file_hash(path) != item["sha256"]
            ):
                raise ValueError("Loader metadata index changed")
    else:
        entries = {}
        for split in ("train", "validation"):
            rows = manifest["splits"][split]
            if progress:
                progress(
                    stage="preparing shared " + split + " loader index",
                    done=0,
                    total=len(rows),
                )
            width = max(len(row["path"].encode("utf-8")) for row in rows)
            dtype = np.dtype(
                [
                    ("path", f"S{width}"),
                    ("start", "<i8"),
                    ("stop", "<i8"),
                    ("bytes", "<i8"),
                    ("sha256", "S64"),
                ]
            )
            temporary = directory / (split + ".partial")
            array = np.lib.format.open_memmap(
                temporary, mode="w+", dtype=dtype, shape=(len(rows),)
            )
            # Fill bounded chunks; avoid a second full Python list of paths.
            for begin in range(0, len(rows), 8192):
                chunk = rows[begin : begin + 8192]
                end = begin + len(chunk)
                array["path"][begin:end] = [
                    row["path"].encode("utf-8") for row in chunk
                ]
                for name in ("start", "stop", "bytes", "sha256"):
                    array[name][begin:end] = [row[name] for row in chunk]
            array.flush()
            del array  # Close the writable mapping before publishing on Windows.
            final = directory / (split + ".npy")
            temporary.replace(final)
            entries[split] = dict(
                file=final.name,
                bytes=final.stat().st_size,
                rows=len(rows),
                sha256=file_hash(final),
            )
        result = dict(version=1, split_hash=manifest["manifest_hash"], splits=entries)
        result["hash"] = digest(result)
        write_json(seal, result)
    manifest["loader_index"] = {
        split: dict(item, path=str(directory / item["file"]))
        for split, item in result["splits"].items()
    }
    return manifest


class Entries:
    """Readonly sequence with tiny spawn serialization and exact legacy shuffle.

    ``order`` uses the same Python-list permutation algorithm as the original
    list of dictionaries. Its values are indices, so random draws and subsequent
    worker lanes, batches and masking seeds are identical to eligible-stream-v1.
    The large mmap itself is intentionally excluded from pickle state.
    """

    def __init__(self, item, shuffle_seed=None):
        self.item = item
        self.data = None
        self.order = None
        if shuffle_seed is not None:
            order = list(range(item["rows"]))
            np.random.default_rng(shuffle_seed).shuffle(order)
            self.order = np.asarray(order, dtype=np.int64)

    def __getstate__(self):
        return dict(item=self.item, order=self.order, data=None)

    def _open(self):
        if self.data is None:
            path = Path(self.item["path"])
            if path.stat().st_size != self.item["bytes"]:
                raise ValueError("Loader index size changed after parent verification")
            self.data = np.load(path, mmap_mode="r", allow_pickle=False)
            if len(self.data) != self.item["rows"] or self.data.dtype.names != (
                "path",
                "start",
                "stop",
                "bytes",
                "sha256",
            ):
                raise ValueError("Loader index header changed")
        return self.data

    def __len__(self):
        return self.item["rows"]

    def __getitem__(self, index):
        if isinstance(index, slice):
            return (self[i] for i in range(*index.indices(len(self))))
        if index < 0 or index >= len(self):
            raise IndexError(index)
        row = self._open()[index if self.order is None else self.order[index]]
        return dict(
            path=row["path"].decode("utf-8"),
            sha256=row["sha256"].decode("ascii"),
            start=int(row["start"]),
            stop=int(row["stop"]),
            bytes=int(row["bytes"]),
        )

    def sample_count(self, lane=0, workers=1):
        """Sum lane sizes vectorially without materializing metadata dictionaries."""
        data = self._open()
        positions = (
            slice(lane, None, workers)
            if self.order is None
            else self.order[lane::workers]
        )
        return int((data["stop"][positions] - data["start"][positions]).sum())
