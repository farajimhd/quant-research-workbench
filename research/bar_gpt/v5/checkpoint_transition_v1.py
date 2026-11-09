"""Serialize an independent quote-data checkpoint copy with readback state proof.

This preparation utility never signals a process, activates a loader or edits its
parent. Inputs must be a completed-epoch checkpoint, the sealed exact-membership
certificate and old/frozen calibration dictionaries. Runtime output is a NEW
directory containing checkpoint_prepared.pt and a sealed preparation proof.
"""

import hashlib
import json
import os
from pathlib import Path
import struct

import torch

from .quote_handoff_v1 import prepare_checkpoint_state
from .shards.contract import digest, write_json

PRESERVED = (
    "model",
    "optimizer",
    "scheduler",
    "rng",
    "cuda_rng",
    "epoch",
    "step",
    "phase",
)


def file_hash(path):
    """Stream a file SHA256 with bounded additional memory."""
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def state_hash(state):
    """Hash nested checkpoint values with type/shape/dtype and exact tensor bytes.

    Supports the plain mappings/sequences/scalars and dense tensors used by this
    trainer. Unsupported types fail closed. Tensors copy to CPU one at a time;
    additional memory is bounded by the largest tensor rather than another model.
    Mapping insertion order does not affect the result; sequence order does.
    """
    result = hashlib.sha256()

    def frame(tag, payload=b""):
        result.update(tag + struct.pack("!Q", len(payload)) + payload)

    def visit(value):
        if isinstance(value, torch.Tensor):
            if value.layout != torch.strided:
                raise ValueError("Only dense checkpoint tensors are supported")
            frame(b"tensor", str((str(value.dtype), tuple(value.shape))).encode())
            frame(
                b"bytes",
                value.detach()
                .cpu()
                .contiguous()
                .reshape(-1)
                .view(torch.uint8)
                .numpy()
                .tobytes(),
            )
        elif isinstance(value, dict):
            frame(b"dict", str(len(value)).encode())
            if any(type(key) not in (str, int) for key in value):
                raise ValueError("Unsupported checkpoint mapping key")
            for key in sorted(value, key=lambda item: (type(item).__name__, str(item))):
                visit(key)
                visit(value[key])
            # Torch state_dict versions may carry module-version metadata.
            metadata = getattr(value, "_metadata", None)
            if metadata is not None:
                frame(b"module_metadata")
                visit(metadata)
        elif isinstance(value, (list, tuple)):
            frame(
                b"list" if isinstance(value, list) else b"tuple",
                str(len(value)).encode(),
            )
            for item in value:
                visit(item)
        elif value is None:
            frame(b"none")
        elif type(value) is bool:
            frame(b"bool", bytes([value]))
        elif type(value) is int:
            frame(b"int", str(value).encode())
        elif type(value) is float:
            frame(b"float", struct.pack("!d", value))
        elif type(value) is str:
            frame(b"str", value.encode())
        else:
            raise ValueError(
                f"Unsupported checkpoint value type: {type(value).__name__}"
            )

    visit(state)
    return result.hexdigest()


def prepare_copy(source, output, certificate, old_calibration, new_calibration):
    """Publish a new independently verified checkpoint and sealed CPU state proof.

    Source is read and hashed again after serialization to detect concurrent
    replacement. Use an immutable epoch checkpoint, never a changing last alias.
    Output must not already exist; interrupted output remains incident evidence
    and a retry uses a fresh directory. This is preparation, NOT activation.
    """
    source, output = Path(source).resolve(strict=True), Path(output).resolve()
    if not source.is_file():
        raise ValueError("Require an immutable source checkpoint file")
    output.mkdir(parents=True, exist_ok=False)
    source_sha = file_hash(source)
    saved = torch.load(source, map_location="cpu", weights_only=False)
    prepared = prepare_checkpoint_state(
        saved, certificate, old_calibration, new_calibration, source_sha
    )
    preserved = {key: state_hash(saved[key]) for key in PRESERVED}
    expected = state_hash(prepared)
    temporary, target = (
        output / "checkpoint_prepared.pt.partial",
        output / "checkpoint_prepared.pt",
    )
    with temporary.open("xb") as stream:
        torch.save(prepared, stream)
        stream.flush()
        os.fsync(stream.fileno())
    # Release the loaded parent before readback so resident memory is bounded
    # by two CPU checkpoint copies rather than three. No GPU allocation occurs.
    del saved, prepared
    readback = torch.load(temporary, map_location="cpu", weights_only=False)
    actual = {key: state_hash(readback[key]) for key in PRESERVED}
    if actual != preserved or state_hash(readback) != expected:
        raise ValueError(
            "Checkpoint serialization changed preserved state or transition metadata"
        )
    if file_hash(source) != source_sha:
        raise ValueError("Source checkpoint changed during preparation")
    target_sha = file_hash(temporary)
    proof = dict(
        version="bargpt-quote-checkpoint-copy-v1",
        state="prepared-not-activated",
        source=str(source),
        source_sha256=source_sha,
        target=str(target),
        target_sha256=target_sha,
        certificate=certificate["hash"],
        target_calibration=digest(new_calibration),
        preserved=preserved,
        prepared_state=expected,
        epoch=readback["epoch"],
        step=readback["step"],
    )
    proof["hash"] = digest(proof)
    temporary.replace(target)
    write_json(output / "checkpoint-preparation.json", proof)
    return proof


def prepare_reporting_identity(output, config, project):
    """Bind ONLY the certified data revision to the original W&B writer identity.

    ``config`` is the complete future run argument dictionary. All scientific
    W&B signature fields except dataset/split/calibration paths must match the
    parent attempt. The new paths must bind the verified prepared checkpoint.
    Writes preparation metadata beside that copy, never to the parent identity,
    and does not acquire a writer lease or contact W&B. The ordinary Reporter
    still requires its exact signature and exclusive original writer lease.
    """
    from .data_transition_v1 import validate_contract
    from .quote_repair_v1 import verify_seal
    from .wandb_identity_v1 import EXPERIMENT_KEYS, persist, read_identity, signature

    output = Path(output).resolve(strict=True)
    if (output / "wandb-identity.json").exists():
        raise FileExistsError("Prepared reporting identity already exists")
    proof = json.loads((output / "checkpoint-preparation.json").read_text())
    verify_seal(proof, "hash")
    target = output / "checkpoint_prepared.pt"
    if (
        proof["version"] != "bargpt-quote-checkpoint-copy-v1"
        or proof["state"] != "prepared-not-activated"
        or Path(proof["target"]).resolve() != target
        or Path(config["resume"]).resolve() != target
        or file_hash(target) != proof["target_sha256"]
    ):
        raise ValueError("Reporting bridge requires the verified prepared checkpoint")
    parent = Path(proof["source"])
    original = json.loads((parent.parent / "manifest.json").read_text())
    identity = read_identity(parent.parent)
    if identity["signature"] != signature(original) or identity["project"] != project:
        raise ValueError("Parent reporting identity does not bind its experiment")
    data_keys = {"dataset", "split_manifest", "calibration"}
    if any(
        original.get(key) != config.get(key)
        for key in EXPERIMENT_KEYS
        if key not in data_keys
    ):
        raise ValueError(
            "Only certified quote-data paths may change reporting identity"
        )
    manifest = json.loads(Path(config["split_manifest"]).read_text())
    calibration = json.loads(Path(config["calibration"]).read_text())
    if Path(config["dataset"]).resolve() != Path(manifest["dataset"]).resolve():
        raise ValueError("Reporting dataset differs from the certified loader")
    saved = torch.load(target, map_location="cpu", weights_only=False)
    if (
        state_hash(saved) != proof["prepared_state"]
        or saved["data_transition"]["certificate"]["hash"] != proof["certificate"]
    ):
        raise ValueError(
            "Reporting checkpoint state differs from its preparation proof"
        )
    current = {
        key: value
        for key, value in saved["contract"].items()
        if key != "data_transition"
    }
    validate_contract(saved, current, manifest, calibration)
    bridge = dict(
        version="bargpt-quote-reporting-bridge-v1",
        state="prepared-not-activated",
        preparation=proof["hash"],
        certificate=proof["certificate"],
        parent_identity=identity,
        source_signature=signature(original),
        target_signature=signature(config),
        target_data={key: config.get(key) for key in sorted(data_keys)},
    )
    bridge["hash"] = digest(bridge)
    write_json(output / "reporting-bridge.json", bridge)
    revised = dict(
        identity, signature=signature(config), quote_data_bridge=bridge["hash"]
    )
    persist(output, revised)
    write_json(
        output / "manifest.json",
        dict(config, artifact_role="quote-data-transition-preparation"),
    )
    return revised


def resume_inputs(checkpoint, proof_path, campaign):
    """Resolve a certified prepared copy to its original owned training attempt.

    This explicit controller entry does not relax ordinary checkpoint ancestry.
    It verifies file/state/bridge seals and actual loader inputs before returning
    the original parent directory and future run arguments. No process starts.
    """
    from .data_transition_v1 import validate_contract
    from .quote_repair_v1 import verify_seal
    from .wandb_identity_v1 import read_identity, signature

    checkpoint, proof_path = Path(checkpoint).resolve(strict=True), Path(
        proof_path
    ).resolve(strict=True)
    campaign = Path(campaign).resolve(strict=True)
    if proof_path != checkpoint.parent / "checkpoint-preparation.json":
        raise ValueError("Explicit transition proof must accompany its checkpoint")
    proof = json.loads(proof_path.read_text())
    verify_seal(proof, "hash")
    if (
        proof.get("version") != "bargpt-quote-checkpoint-copy-v1"
        or proof.get("state") != "prepared-not-activated"
        or Path(proof["target"]).resolve() != checkpoint
        or file_hash(checkpoint) != proof["target_sha256"]
    ):
        raise ValueError("Prepared checkpoint does not match its explicit proof")
    parent = Path(proof["source"]).resolve(strict=True).parent
    if (
        parent.parent != campaign
        or file_hash(proof["source"]) != proof["source_sha256"]
    ):
        raise ValueError("Transition parent must be intact in the owned campaign")
    # The controller's lock excludes a second controller, but an orphaned old
    # child may outlive its controller. Do not start a new data domain over it.
    ledger = campaign / (parent.name + "-child.json")
    if ledger.is_file():
        import psutil

        owner = json.loads(ledger.read_text())
        try:
            process = psutil.Process(owner["pid"])
            if (
                abs(process.create_time() - owner["created"]) < 1
                and process.is_running()
            ):
                raise RuntimeError(
                    "Previous owned trainer is still alive; drain it before transition"
                )
        except psutil.NoSuchProcess:
            pass
    config = json.loads((checkpoint.parent / "manifest.json").read_text())
    bridge = json.loads((checkpoint.parent / "reporting-bridge.json").read_text())
    verify_seal(bridge, "hash")
    identity = read_identity(checkpoint.parent)
    if (
        config.get("artifact_role") != "quote-data-transition-preparation"
        or Path(config["resume"]).resolve() != checkpoint
        or bridge["preparation"] != proof["hash"]
        or bridge["certificate"] != proof["certificate"]
        or identity["quote_data_bridge"] != bridge["hash"]
        or identity["signature"] != signature(config)
        or bridge["target_signature"] != signature(config)
        or any(
            identity[key] != bridge["parent_identity"][key]
            for key in ("id", "name", "project", "entity", "root")
        )
    ):
        raise ValueError("Transition reporting ancestry differs from prepared metadata")
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    if state_hash(saved) != proof["prepared_state"]:
        raise ValueError("Prepared checkpoint state differs from its sealed proof")
    manifest = json.loads(Path(config["split_manifest"]).read_text())
    calibration = json.loads(Path(config["calibration"]).read_text())
    if Path(config["dataset"]).resolve() != Path(manifest["dataset"]).resolve():
        raise ValueError("Transition loader root differs from its preparation")
    current = {k: v for k, v in saved["contract"].items() if k != "data_transition"}
    validate_contract(saved, current, manifest, calibration)
    return parent, config
