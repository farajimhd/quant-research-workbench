"""Atomic, hash-verified snapshot of previously certified CPU tape inputs.

This is a job-owned replay snapshot, not a producer certificate or alternative
market authority. Resume still rechecks its producer build certificate. A whole
directory publishes atomically; an interrupted staging directory is preserved.
"""

import json
from dataclasses import fields
from pathlib import Path
from uuid import uuid4

import torch

from .runtime import file_hash, require_runtime, write_json
from .tape import SqueezeTape


def save_prepared(path, tape, identity):
    path = Path(path)
    require_runtime(path.parent)
    if path.exists():
        raise ValueError("Prepared snapshot already published; never overwrite it")
    staging = require_runtime(path.parent / ("staging-" + uuid4().hex))
    blob = staging / "tape.pt"
    torch.save({f.name: getattr(tape, f.name) for f in fields(tape)}, blob)
    write_json(
        staging / "receipt.json",
        dict(
            identity=identity,
            sha256=file_hash(blob),
            source_fingerprint=tape.provenance["fingerprint"],
            bytes=tape.bytes,
        ),
    )
    staging.rename(path)


def load_prepared(path, identity):
    path = Path(path)
    if not path.exists():
        return None
    receipt = json.loads((path / "receipt.json").read_text(encoding="utf-8"))
    if receipt["identity"] != identity:
        raise ValueError("Prepared snapshot source/code/request identity mismatch")
    blob = path / "tape.pt"
    if receipt["sha256"] != file_hash(blob):
        raise ValueError(
            "Prepared snapshot integrity mismatch; do not skip or rebuild silently"
        )
    tape = SqueezeTape(
        **torch.load(blob, map_location="cpu", weights_only=True)
    ).validate()
    if (
        tape.provenance["fingerprint"] != receipt["source_fingerprint"]
        or tape.bytes != receipt["bytes"]
    ):
        raise ValueError("Prepared snapshot provenance/size mismatch")
    return tape
