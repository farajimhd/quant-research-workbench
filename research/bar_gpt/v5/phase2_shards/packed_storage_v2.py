"""Small immutable containers for identical phase-two arrays, not new bar math.

Physical payloads are at most 16 MiB. Receipt offsets expose readonly NumPy views
without decompressing/materializing a shard. Context arrays share the containers,
reducing tiny files and durable flushes. Legacy NPY/NPZ publications stay readable.
"""

import json
from pathlib import Path
import time

import numpy as np

from .contract import BAR, DECISION, Context, digest, file_hash, write_json

LIMIT = 16 * 1024**2
DTYPES = {"bar": BAR, "decision": DECISION, "clock": np.dtype("<i8")}


class Writer:
    """Buffer one bounded payload; logical descriptors retain dtype and shape."""

    def __init__(self, path):
        self.path, self.buffer, self.files = path, bytearray(), []

    def flush(self):
        """Hash source bytes and fsync before the receipt can reference this file."""
        from .storage import save_payload

        if self.buffer:
            self.files.append(
                save_payload(
                    self.path, f"payload-{len(self.files):04d}.bin", self.buffer, 0
                )
            )
            self.buffer = bytearray()

    def add(self, array, kind):
        """Append exact C-order bytes, or an explicit empty-array descriptor."""
        array = np.asarray(array, dtype=DTYPES[kind], order="C")
        size = array.nbytes
        if size > LIMIT:
            raise ValueError("Packed array exceeds the 16 MiB publication bound")
        descriptor = dict(
            kind=kind, shape=list(array.shape), rows=len(array), name=None, offset=0
        )
        if not size:
            return descriptor
        padding = (-len(self.buffer)) % 64
        if len(self.buffer) + padding + size > LIMIT:
            self.flush()
            padding = 0
        self.buffer.extend(b"\0" * padding)
        descriptor.update(
            name=f"payload-{len(self.files):04d}.bin", offset=len(self.buffer)
        )
        self.buffer.extend(memoryview(array).cast("B"))
        return descriptor


def load(path, descriptor):
    """Map a validated typed slice; empty arrays need no physical file."""
    dtype, shape = DTYPES[descriptor["kind"]], tuple(descriptor["shape"])
    size = int(np.prod(shape)) * dtype.itemsize
    if not size:
        return np.empty(shape, dtype=dtype)
    file = Path(path) / descriptor["name"]
    offset = descriptor["offset"]
    if offset < 0 or offset + size > file.stat().st_size:
        raise ValueError("Packed array lies outside its immutable payload")
    return np.memmap(file, mode="r", dtype=dtype, shape=shape, offset=offset)


def restore(path, receipt):
    """Validate context payload hashes, retaining legacy NPZ's corruption detection."""
    context = receipt["context"]
    descriptors = [*context["tails"], context["pending"]]
    needed = {item["name"] for item in descriptors if item["name"]}
    for item in receipt["files"]:
        if (
            item["name"] in needed
            and file_hash(Path(path) / item["name"]) != item["sha256"]
        ):
            raise ValueError("Packed context payload mutation")
    return Context(
        context["counts"],
        [load(path, item).copy() for item in context["tails"]],
        load(path, context["pending"]).copy(),
        initialized=context["initialized"],
    )


def publish(root, stream, unit, arrays, starts, blocks, splits, context, timing):
    """Seal the same logical range, references and context with fewer physical files."""
    from .storage import MAX_ROWS

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
    writer, segments, decisions = Writer(temporary), [], []
    for frame, (rows, start) in enumerate(zip(arrays, starts)):
        for offset in range(0, len(rows), MAX_ROWS):
            segments.append(
                dict(
                    writer.add(rows[offset : offset + MAX_ROWS], "bar"),
                    frame=frame,
                    start=start + offset,
                )
            )
    for offset in range(0, len(blocks), 128):
        decisions.append(writer.add(blocks[offset : offset + 128], "decision"))
    checkpoint = dict(
        counts=context.counts,
        initialized=context.initialized,
        tails=[writer.add(tail, "clock") for tail in context.tails],
        pending=writer.add(context.pending, "decision"),
    )
    writer.flush()
    hashes = {item["name"]: item["sha256"] for item in writer.files}
    for item in [*segments, *decisions]:
        item["sha256"] = hashes[item["name"]]
    timing["publication_seconds"] = time.monotonic() - began
    receipt = dict(
        storage="packed-v2",
        stream=stream["key"],
        ticker=stream["ticker"],
        year=stream["year"],
        liquid=stream["liquid"],
        unit=unit,
        files=writer.files,
        segments=segments,
        block_segments=decisions,
        context=checkpoint,
        splits=splits.tolist(),
        blocks=len(blocks),
        bytes=sum(item["bytes"] for item in writer.files),
        timing=timing,
        deferred_tail=len(context.pending),
        counts=context.counts,
    )
    receipt["hash"] = digest(receipt)
    write_json(temporary / "receipt.json", receipt)
    temporary.rename(final)
    return receipt
