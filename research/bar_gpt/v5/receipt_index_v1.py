"""Index fixed v6 records from sealed publication metadata without opening payloads.

Each record occupies 6281 bytes, larger than its producer's fixed-schema NPY
header (under 4096 bytes). Thus floor(file_bytes / record_bytes) is exactly the
record count. Receipt seals and per-publication totals verify this index. Actual
headers, SHA256 and records remain verified by targeted audit and loader access.
This shortcut applies only to the known production v6 schema, never arbitrary NPY.
"""

import json
from pathlib import Path

from .shards.phase1_contract import SAMPLE_DTYPE, digest


def index_receipt(path, binding):
    """Return ordinary split-index entries from an immutable v6 receipt.

    No file contents or filesystem stat calls are needed. Timestamp bounds and
    SHA256 values retain their receipt authority. Any inconsistent byte layout,
    unsafe filename, foreign binding or count mismatch fails closed.
    """
    receipt = json.loads(path.read_text())
    if receipt["binding"] != binding:
        raise ValueError(f"Receipt belongs to a different dataset contract: {path}")
    if receipt["receipt_hash"] != digest(
        {k: v for k, v in receipt.items() if k != "receipt_hash"}
    ):
        raise ValueError(f"Foreign or corrupt receipt: {path}")
    entries = []
    for item in receipt["files"]:
        name = item["name"]
        if Path(name).name != name or not name.startswith("samples-"):
            raise ValueError(f"Unsafe or unexpected v6 payload name: {path}")
        rows, header = divmod(item["bytes"], SAMPLE_DTYPE.itemsize)
        if rows < 1 or not 0 < header < 4096:
            raise ValueError(f"Unexpected fixed-schema NPY byte layout: {path}")
        entries.append(
            dict(
                path=str(path.parent / name),
                rows=rows,
                start=0,
                stop=rows,
                day=receipt["day"],
                sha256=item["sha256"],
                bytes=item["bytes"],
                first_start_us=item.get("first_start_us"),
                last_end_us=item.get("last_end_us"),
                receipt_hash=receipt["receipt_hash"],
                frames=receipt["frames"],
            )
        )
    if sum(item["rows"] for item in entries) != receipt["samples"]:
        raise ValueError(
            f"Receipt record count differs from payload byte layout: {path}"
        )
    return entries
