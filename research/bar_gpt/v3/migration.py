"""Fail-closed BarGPT v2 to v3 model-weight migration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

import torch


V2_MODEL_FAMILY = "bar_gpt"
V2_MODEL_VERSION = "v2"
V2_LEARNING_CONTRACT = "return_direction_3class_1bp_v1"
_REQUIRED_COPIED_PREFIXES = (
    "input_norm.",
    "input_projection.",
    "timeframe_embedding.",
    "pathway_embedding.",
    "blocks.",
    "output_norm.",
    "scale_gate.",
    "autoregressive_continuous_head.",
    "autoregressive_availability_head.",
    "horizon_embedding.",
    "horizon_state.",
    "horizon_head.",
    "horizon_availability_head.",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    # The short-lived context is deliberately closed before training starts.
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_v2_weights(
    model: torch.nn.Module,
    checkpoint_path: Path,
    *,
    map_location: torch.device | str = "cpu",
) -> dict[str, Any]:
    """Copy every exact v2 tensor and retain deterministic v3-only tensors."""
    source = checkpoint_path.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"v2 initialization checkpoint does not exist: {source}")
    payload = torch.load(source, map_location=map_location, weights_only=False)
    if not isinstance(payload, Mapping):
        raise RuntimeError("v2 initialization checkpoint must contain a mapping")
    observed = (
        payload.get("model_family"),
        payload.get("model_version"),
        payload.get("learning_contract"),
    )
    expected = (V2_MODEL_FAMILY, V2_MODEL_VERSION, V2_LEARNING_CONTRACT)
    if observed != expected:
        raise RuntimeError(
            "v2 initialization checkpoint contract mismatch: "
            f"expected {expected}, observed {observed}"
        )
    source_state = payload.get("model")
    if not isinstance(source_state, Mapping):
        raise RuntimeError("v2 initialization checkpoint has no model state")

    target_state = model.state_dict()
    copied: list[str] = []
    incompatible: list[dict[str, Any]] = []
    ignored_source: list[str] = []
    migrated = dict(target_state)
    for name, value in source_state.items():
        if name not in target_state:
            ignored_source.append(str(name))
            continue
        if not torch.is_tensor(value):
            incompatible.append({"name": str(name), "reason": "not_tensor"})
            continue
        if tuple(value.shape) != tuple(target_state[name].shape):
            incompatible.append(
                {
                    "name": str(name),
                    "source_shape": list(value.shape),
                    "target_shape": list(target_state[name].shape),
                }
            )
            continue
        migrated[name] = value.to(
            device=target_state[name].device,
            dtype=target_state[name].dtype,
        )
        copied.append(str(name))

    missing_required = [
        prefix
        for prefix in _REQUIRED_COPIED_PREFIXES
        if not any(name.startswith(prefix) for name in copied)
    ]
    if missing_required:
        raise RuntimeError(
            "v2 initialization omitted required model families: "
            + ", ".join(missing_required)
        )
    model.load_state_dict(migrated, strict=True)

    copied_parameters = sum(int(target_state[name].numel()) for name in copied)
    total_parameters = sum(int(value.numel()) for value in target_state.values())
    initialized = sorted(name for name in target_state if name not in copied)
    return {
        "migration_contract": "bar_gpt_v2_to_v3_exact_shape_v1",
        "source_checkpoint": str(source),
        "source_checkpoint_sha256": _sha256(source),
        "source_samples_seen": int(payload.get("samples_seen", 0)),
        "copied_tensor_count": len(copied),
        "copied_parameter_count": copied_parameters,
        "total_parameter_count": total_parameters,
        "copied_parameter_fraction": copied_parameters / max(1, total_parameters),
        "copied_tensors": sorted(copied),
        "initialized_v3_tensors": initialized,
        "ignored_v2_tensors": sorted(ignored_source),
        "incompatible_tensors": incompatible,
        "optimizer_state": "fresh_v3_state",
        "checkpoint_handle": "closed_before_training",
    }


def snapshot_v2_checkpoint(source: Path, destination: Path) -> Path:
    """Pin the current NTFS checkpoint inode without opening the trainer path."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"v2 initialization checkpoint does not exist: {source}")
    if destination.exists():
        raise FileExistsError(f"immutable v2 initialization snapshot exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError as exc:
        raise RuntimeError(
            "v2 initialization requires a same-volume hard-link snapshot so "
            "checkpoint_latest is never held open while the trainer replaces it; "
            "copy a stable checkpoint to the v3 runtime volume first"
        ) from exc
    return destination


__all__ = ["load_v2_weights", "snapshot_v2_checkpoint"]
