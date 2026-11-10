"""Immutable, source-bound UNADJUSTED coarse bars; never session-rebased pools."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

import polars as pl
import psutil

from ..shards.contract import digest, file_hash, write_json
from ..quote_repair_v1 import verify_seal


@contextmanager
def owned_lock(path):
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.write(b"0")
        stream.flush()
        while True:
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class CoarseCache:
    def __init__(self, root, authority):
        self.root = Path(root)
        self.authority = authority

    def get(self, sql, source_rows, fetch):
        identity = dict(version="raw-coarse-arrow-v1", sql=sql,
                        source_authority=self.authority, source_rows=source_rows,
                        price_basis="unadjusted")
        key = digest(identity)
        directory = self.root / key[:2] / key
        with owned_lock(directory / "cache.lock"):
            marker, payload = directory / "receipt.json", directory / "bars.arrow"
            if marker.exists():
                receipt = json.loads(marker.read_text())
                verify_seal(receipt, "hash")
                if (receipt["identity"] != identity or payload.stat().st_size != receipt["bytes"]
                    or file_hash(payload) != receipt["sha256"]):
                    raise ValueError("Source-bound coarse cache changed")
                table = pl.read_ipc(payload, memory_map=False)
                if table.height != receipt["rows"] or {k:str(v) for k,v in table.schema.items()} != receipt["schema"]:
                    raise ValueError("Coarse cache Arrow schema changed")
                return table, True
            table = fetch().sort(["resolution", "end_us"]).rechunk()
            if psutil.disk_usage(str(directory)).free < 150 * 1024**3 + table.estimated_size():
                raise OSError("Coarse cache publication would consume disk reserve")
            temporary = directory / f"bars-{os.getpid()}.partial"
            with temporary.open("wb") as stream:
                table.write_ipc(stream)
                stream.flush()
                os.fsync(stream.fileno())
            sha = file_hash(temporary)
            if payload.exists():
                if file_hash(payload) != sha:
                    raise ValueError("Incomplete coarse cache disagrees with recomputed source")
            else:
                temporary.replace(payload)
            if file_hash(payload) != sha:
                raise ValueError("Coarse cache independent readback mismatch")
            receipt = dict(identity=identity, sha256=sha, bytes=payload.stat().st_size,
                           rows=table.height, schema={k:str(v) for k,v in table.schema.items()})
            receipt["hash"] = digest(receipt)
            write_json(marker, receipt)
            return table, False
