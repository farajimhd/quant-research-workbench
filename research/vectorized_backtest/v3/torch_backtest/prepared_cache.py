"""Atomic, hash-verified snapshot of previously certified CPU tape inputs.

This is a job-owned replay snapshot, not a producer certificate or alternative
market authority. Resume still rechecks its producer build certificate. A whole
directory publishes atomically; an interrupted staging directory is preserved.
"""

import json
import os
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


def import_prepared(origin, destination, identity, grammar, *, execution_contract_only=False):
    """Explicit reuse of a sealed dataset by a NEW consumer implementation.

    Never resume the old experiment as new code or rewrite its source receipt.
    Certification is rechecked by the caller. Preserve the creator's provenance,
    bytes and hash; bind a new consumer receipt with an auditable import record.
    """
    from hashlib import sha256

    origin, destination = Path(origin), Path(destination)
    previous = json.loads((origin / "receipt.json").read_text(encoding="utf-8"))
    creator = origin.parent.parent / "identity.json"
    if not creator.exists():
        creator = origin.parent.parent / "study_identity.json"
    experiment = json.loads(creator.read_text(encoding="utf-8"))
    if experiment["code_hash"] != previous["identity"]["code_hash"]:
        raise ValueError("Dataset creator code identity mismatch")
    old_grammar = json.loads(json.dumps(experiment['grammar']))
    new_grammar = json.loads(json.dumps(grammar))
    if execution_contract_only:
        # Explicit dataset migration: these fields affect execution/search only,
        # never prepared bars/features. All input/timing/source fields still
        # match, and the unchanged producer algorithm hash is checked below.
        for value in (old_grammar, new_grammar):
            value.pop('version', None)
            for name in ('maximum_stop_risk_fraction', 'maximum_position_hold_seconds'):
                value['fixed_settings'].pop(name, None)
    if old_grammar != new_grammar:
        raise ValueError("Prepared dataset grammar/settings/timing contract differs")
    old_request = {k: v for k, v in previous["identity"].items() if k != "code_hash"}
    new_request = {k: v for k, v in identity.items() if k != "code_hash"}
    if old_request != new_request:
        raise ValueError("Prepared import producer/session/manifest differs")
    tape = load_prepared(origin, previous["identity"])
    algorithm = sha256(Path(__file__).with_name("prepare.py").read_bytes()).hexdigest()
    if tape.provenance.get("preparation_algorithm") != algorithm:
        raise ValueError("Prepared import uses a different preparation algorithm")
    if tape.provenance["source_build"] != identity["build_id"]:
        raise ValueError("Prepared import producer build differs")
    if destination.exists():
        raise ValueError("Never overwrite an owned dataset snapshot")
    staging = require_runtime(destination.parent / ("import-" + uuid4().hex))
    # Both task-owned jobs are on the same runtime volume. Hard-link immutable
    # bytes; failure is explicit, never an unreported copy/identity fallback.
    os.link(origin / "tape.pt", staging / "tape.pt")
    write_json(staging / "receipt.json", dict(
        identity=identity, sha256=previous["sha256"], bytes=previous["bytes"],
        source_fingerprint=previous["source_fingerprint"],
        imported_from=dict(path=str(origin), identity=previous["identity"],
                           sha256=previous["sha256"], contract="sealed-dataset-import-v1",
                           execution_contract_only=execution_contract_only),
    ))
    staging.rename(destination)
    return tape
