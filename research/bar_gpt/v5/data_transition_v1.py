"""Strict quote-data transition checks and RNG-preserving baseline collection.

These helpers do not activate a trainer or modify a checkpoint. The caller must
first publish the repaired cohort, prepare an exact-membership split/certificate,
and verify a separately serialized checkpoint copy. Old-data learning evidence
must remain separate from the new-data baselines returned here.
"""

from copy import deepcopy
import hashlib
import math
import random
import re

import numpy as np
import torch

from .shards.contract import digest
from .quote_repair_v1 import verify_seal


def validate_contract(saved, current, manifest, calibration):
    """Return the exact expected resume contract for a certified data transition.

    Inputs are JSON-compatible dictionaries, apart from ``saved`` checkpoint
    tensors which this function never reads or edits. Only split identity and
    the explicit transition-certificate field may differ from the historical
    contract. Existing explicit overfit-patience amendments remain supported.
    The current loader manifest may contain its derived mmap ``loader_index``.
    """
    from .campaign_v1 import compatible_resume_contract

    transition = saved["data_transition"]
    certificate = transition["certificate"]
    verify_seal(certificate, "hash")
    if (
        transition["version"] != "bargpt-quote-checkpoint-transition-v1"
        or certificate["version"] != "bargpt-quote-split-handoff-v1"
        or certificate.get("state") != "prepared-not-activated"
        or not re.fullmatch("[0-9a-f]{64}", certificate.get("membership_hash", ""))
        or not re.fullmatch(
            "[0-9a-f]{64}", transition.get("source_checkpoint_sha256", "")
        )
        or transition["state"]
        not in {"requires-corrected-data-baselines", "baselines-complete"}
    ):
        raise ValueError("Unknown or unfinished data-transition contract")
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"manifest_hash", "loader_index"}
    }
    if (
        digest(body) != manifest["manifest_hash"]
        or certificate["target_split"] != manifest["manifest_hash"]
    ):
        raise ValueError("Transition certificate does not bind the actual loader split")
    if any(
        certificate[key] != manifest[key]
        for key in ("counts", "cutoff_us", "purged_crossing")
    ):
        raise ValueError("Transition changed chronological membership or context purge")
    source = transition["source_contract"]
    if certificate["source_split"] != source["split_hash"]:
        raise ValueError("Transition source checkpoint belongs to another split")
    planned = dict(
        source,
        split_hash=manifest["manifest_hash"],
        data_transition=certificate["hash"],
    )
    expected = dict(current, data_transition=certificate["hash"])
    if not compatible_resume_contract(
        saved["contract"], planned
    ) or not compatible_resume_contract(saved["contract"], expected):
        raise ValueError(
            "Data repair cannot change model, normalization, optimizer or schedule contracts"
        )
    if (
        calibration["split_hash"] != manifest["manifest_hash"]
        or digest(calibration) != transition["target_calibration"]
    ):
        raise ValueError("Transition normalization provenance differs from the loader")
    if any(calibration[key] != source[key] for key in ("median", "scale")):
        raise ValueError("Transition must retain the original input units")
    return expected


def model_hash(model):
    """Hash named parameter/buffer bytes, including dtype/shape, without mutation.

    Input is a Torch module on CPU or CUDA. Tensors are copied to CPU one at a
    time, so additional memory is bounded by the largest tensor, not a second
    full model. The byte view also supports BF16 tensors without float conversion.
    """
    result = hashlib.sha256()
    for name, tensor in model.state_dict().items():
        result.update(str((name, str(tensor.dtype), tuple(tensor.shape))).encode())
        result.update(
            tensor.detach()
            .cpu()
            .contiguous()
            .reshape(-1)
            .view(torch.uint8)
            .numpy()
            .tobytes()
        )
    return result.hexdigest()


def collect_baselines(model, factory, evaluate, seed):
    """Evaluate untrained probe, trained probe and full validation with no updates.

    ``factory()`` constructs the SAME architecture/config on the model's device.
    ``evaluate(selected_model, probe)`` must run inference with the fixed epoch-0
    validation population/masks, returning ordinary metric dictionaries with a
    ``complete_pass`` flag. A probe is bounded; probe=False is normal FULL
    validation, not an additional corpus audit. Model/optimizer/epoch/scheduler
    state must not be advanced by the caller. CPU/CUDA/Python/NumPy RNG states
    and the trained model's mode are restored, even on failure.
    """
    versions = {
        name: (tensor.dtype, tuple(tensor.shape))
        for name, tensor in model.state_dict().items()
    }
    before = model_hash(model)
    devices = sorted(
        {
            tensor.device.index
            for tensor in model.parameters()
            if tensor.device.type == "cuda"
        }
    )
    python_rng, numpy_rng, was_training = (
        random.getstate(),
        np.random.get_state(),
        model.training,
    )
    try:
        with torch.random.fork_rng(devices=devices):
            torch.manual_seed(seed)
            untrained = factory()
            if versions != {
                name: (tensor.dtype, tuple(tensor.shape))
                for name, tensor in untrained.state_dict().items()
            }:
                raise ValueError(
                    "Untrained baseline architecture differs from the resumed model"
                )
            untrained_probe = deepcopy(evaluate(untrained, True))
            del untrained
            trained_probe = deepcopy(evaluate(model, True))
            trained_validation = deepcopy(evaluate(model, False))
            if not all(
                row.get("complete_pass")
                for row in (untrained_probe, trained_probe, trained_validation)
            ):
                raise InterruptedError(
                    "A corrected-data baseline did not complete its required pass"
                )
            if not all(
                math.isfinite(float(row["loss"]))
                for row in (untrained_probe, trained_probe, trained_validation)
            ):
                raise FloatingPointError("Nonfinite corrected-data baseline")
            if model_hash(model) != before:
                raise RuntimeError("Baseline evaluation modified trained model tensors")
            return dict(
                untrained_probe=untrained_probe,
                trained_probe=trained_probe,
                trained_validation=trained_validation,
                trained_model_hash=before,
            )
    finally:
        random.setstate(python_rng)
        np.random.set_state(numpy_rng)
        model.train(was_training)
