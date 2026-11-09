"""Corrected-data checks must reject scientific drift and preserve training state."""

import random

import numpy as np
import pytest
import torch

from .data_transition_v1 import collect_baselines, model_hash, validate_contract
from .quote_handoff_v1 import prepare_checkpoint_state, rebind_calibration
from .shards.contract import digest
from .test_quote_handoff_v1 import checkpoint_fixture


def transition_fixture():
    saved, certificate, old, _ = checkpoint_fixture()
    manifest = dict(
        dataset="new",
        counts=dict(train=8, validation=2),
        cutoff_us=100,
        purged_crossing=2,
    )
    manifest["manifest_hash"] = digest(manifest)
    certificate.update(
        membership_hash="b" * 64,
        target_split=manifest["manifest_hash"],
        counts=manifest["counts"],
        cutoff_us=100,
        purged_crossing=2,
    )
    certificate["hash"] = digest(
        {key: value for key, value in certificate.items() if key != "hash"}
    )
    calibration = rebind_calibration(old, certificate, "old", manifest["manifest_hash"])
    prepared = prepare_checkpoint_state(saved, certificate, old, calibration, "a" * 64)
    current = dict(saved["contract"], split_hash=manifest["manifest_hash"])
    return prepared, current, manifest, calibration


def test_transition_contract_requires_exact_scientific_settings():
    saved, current, manifest, calibration = transition_fixture()
    assert validate_contract(saved, current, manifest, calibration) == saved["contract"]
    current["epochs"] = 51
    with pytest.raises(ValueError, match="model, normalization"):
        validate_contract(saved, current, manifest, calibration)


def test_transition_rejects_tampered_split_and_calibration():
    saved, current, manifest, calibration = transition_fixture()
    manifest["purged_crossing"] = 0
    with pytest.raises(ValueError, match="actual loader split"):
        validate_contract(saved, current, manifest, calibration)
    saved, current, manifest, calibration = transition_fixture()
    calibration["scale"][0] = 99
    with pytest.raises(ValueError, match="normalization provenance"):
        validate_contract(saved, current, manifest, calibration)


def test_completed_baseline_resume_requires_sealed_domain_and_best_state():
    """A resumed domain cannot silently lose or substitute its validation baseline."""
    saved, current, manifest, calibration = transition_fixture()
    baseline = dict(loss=0.5, complete_pass=True)
    evidence = dict(
        epoch=saved["epoch"],
        step=saved["step"],
        split_hash=manifest["manifest_hash"],
        certificate=saved["data_transition"]["certificate"]["hash"],
        baselines=dict(
            untrained_probe=baseline,
            trained_probe=baseline,
            trained_validation=baseline,
        ),
    )
    evidence["hash"] = digest(evidence)
    saved["data_transition"].update(
        state="baselines-complete", baseline_evidence=evidence
    )
    saved.update(probe_baseline=0.5, best_validation=0.5, best_epoch=saved["epoch"])
    validate_contract(saved, current, manifest, calibration)
    saved["best_epoch"] = None
    with pytest.raises(ValueError, match="best/probe"):
        validate_contract(saved, current, manifest, calibration)
    saved["best_epoch"] = saved["epoch"]
    evidence["split_hash"] = "foreign"
    evidence["hash"] = digest({k: v for k, v in evidence.items() if k != "hash"})
    with pytest.raises(ValueError, match="another data state"):
        validate_contract(saved, current, manifest, calibration)


def test_baselines_preserve_weights_mode_and_all_rngs():
    model = torch.nn.Linear(2, 2)
    before, rng = model_hash(model), torch.get_rng_state().clone()
    python_state, numpy_state = random.getstate(), np.random.get_state()
    calls = []

    def evaluate(selected, probe):
        selected.eval()
        calls.append((selected is model, probe))
        random.random()
        np.random.rand()
        torch.rand(2)
        return dict(
            loss=float(selected(torch.ones(1, 2)).detach().abs().sum()),
            complete_pass=True,
        )

    result = collect_baselines(model, lambda: torch.nn.Linear(2, 2), evaluate, 17)
    assert calls == [(False, True), (True, True), (True, False)]
    assert (
        before == model_hash(model) == result["trained_model_hash"] and model.training
    )
    assert torch.equal(rng, torch.get_rng_state()) and python_state == random.getstate()
    assert numpy_state[0] == np.random.get_state()[0]
    np.testing.assert_array_equal(numpy_state[1], np.random.get_state()[1])


def test_baselines_refuse_partial_validation():
    model = torch.nn.Linear(2, 2)
    with pytest.raises(InterruptedError):
        collect_baselines(
            model,
            lambda: torch.nn.Linear(2, 2),
            lambda selected, probe: dict(loss=1, complete_pass=probe),
            17,
        )


def test_baselines_detect_accidental_weight_updates():
    model = torch.nn.Linear(2, 2)

    def corrupt(selected, probe):
        if selected is model:
            with torch.no_grad():
                selected.weight.add_(1)
        return dict(loss=1, complete_pass=True)

    with pytest.raises(RuntimeError, match="modified trained model"):
        collect_baselines(model, lambda: torch.nn.Linear(2, 2), corrupt, 17)


def test_nonfinite_baseline_is_not_accepted():
    model = torch.nn.Linear(2, 2)
    with pytest.raises(FloatingPointError):
        collect_baselines(
            model,
            lambda: torch.nn.Linear(2, 2),
            lambda *args: dict(loss=float("nan"), complete_pass=True),
            17,
        )
