"""Exact split-membership invariants for quote-repair handoff preparation.

These CPU-only tests exercise metadata transitions, never a live trainer.
Payload correctness and complete-release gates remain separate audit evidence.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from .quote_handoff_v1 import (
    membership_hash,
    prepare_checkpoint_state,
    rebind_calibration,
    rebind_split,
)
from .shards.phase1_contract import digest


def fixture():
    """One file contributes disjoint ranges after the original overlap purge."""
    entry = dict(
        path=str(Path("old/base/jobs/session/unit/samples-00.npy")),
        sha256="old-payload",
        receipt_hash="old-receipt",
        rows=12,
        start=0,
        stop=4,
        day="coarse",
        bytes=75300,
        frames={"1h": 12},
        first_start_us=100,
        last_end_us=900,
    )
    original = dict(
        dataset="old/base",
        dataset_binding="old-binding",
        counts=dict(train=4, validation=4),
        cutoff_us=500,
        purged_crossing=4,
        splits=dict(train=[entry], validation=[dict(entry, start=8, stop=12)]),
    )
    original["manifest_hash"] = digest(original)
    replacement = dict(
        entry,
        path=str(Path("new/base/jobs/session/unit/samples-00.npy")),
        sha256="new-payload",
        receipt_hash="new-receipt",
        start=0,
        stop=12,
    )
    return original, replacement


def test_preserves_exact_ranges_order_and_purge():
    """Quote hash changes cannot move samples across the sealed chronology."""
    original, replacement = fixture()
    snapshot = deepcopy(original)
    new = rebind_split(
        original,
        dict(dataset_binding="new-binding"),
        Path("new/base"),
        lambda _: replacement,
    )
    assert original == snapshot
    assert new["splits"]["validation"][0]["start"] == 8
    assert new["splits"]["train"][0]["stop"] == 4
    assert new["counts"] == original["counts"]
    assert new["purged_crossing"] == 4 and new["cutoff_us"] == 500
    assert membership_hash(original, dict(base=Path("old/base"))) == membership_hash(
        new, dict(base=Path("new/base"))
    )
    assert new["manifest_hash"] != original["manifest_hash"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("rows", 13),
        ("bytes", 75301),
        ("day", "2020-01-02"),
        ("first_start_us", 101),
        ("frames", {"1s": 12}),
    ],
)
def test_rejects_geometry_and_clock_changes(field, value):
    """Source ancestry alone cannot authorize a different sample layout."""
    original, replacement = fixture()
    replacement[field] = value
    with pytest.raises(ValueError, match="geometry"):
        rebind_split(original, {}, Path("new/base"), lambda _: replacement)


def test_membership_hash_detects_shifted_selection():
    """Same counts are insufficient: every exact selected origin must agree."""
    original, _ = fixture()
    shifted = deepcopy(original)
    shifted["splits"]["train"][0].update(start=1, stop=5)
    roots = dict(base=Path("old/base"))
    assert membership_hash(original, roots) != membership_hash(shifted, roots)


def test_discards_old_derived_loader_index():
    """Mmap loader metadata must be rebuilt against the new manifest digest."""
    original, replacement = fixture()
    original["loader_index"] = dict(train="old-index")
    prepared = rebind_split(original, {}, Path("new/base"), lambda _: replacement)
    assert "loader_index" not in prepared


def calibration_fixture():
    """Small frozen vectors stand for the existing training-only activity units."""
    old = dict(
        split_hash="old",
        median=[1.0, 2.0, 1.0, 2.0, 1.0, 2.0],
        scale=[0.5, 1.5, 0.5, 1.5, 0.5, 1.5],
        selection_hash="selected-training-rows",
    )
    certificate = dict(
        version="bargpt-quote-split-handoff-v1",
        state="prepared-not-activated",
        source_split="old",
        target_split="new",
    )
    certificate["hash"] = digest(certificate)
    return old, certificate


def test_calibration_retains_units_and_records_historical_provenance():
    """A new split binding must not silently refit units for trained weights."""
    original, certificate = calibration_fixture()
    snapshot = deepcopy(original)
    new = rebind_calibration(original, certificate, "old", "new")
    assert original == snapshot
    assert new["median"] == original["median"] and new["scale"] == original["scale"]
    assert new["split_hash"] == "new"
    assert new["quote_migration"]["source_calibration"] == digest(original)
    assert new["quote_migration"]["source_selection_hash"] == original["selection_hash"]


def test_calibration_rejects_tampered_certificate():
    """An unsealed membership claim cannot authorize a calibration rebind."""
    original, certificate = calibration_fixture()
    certificate["target_split"] = "another"
    with pytest.raises(ValueError):
        rebind_calibration(original, certificate, "old", "new")


@pytest.mark.parametrize(
    "field,value",
    [
        ("scale", [0.0, 1.5, 0.5, 1.5, 0.5, 1.5]),
        ("median", [float("nan"), 2.0, 1.0, 2.0, 1.0, 2.0]),
        ("split_hash", "unrelated"),
    ],
)
def test_calibration_rejects_invalid_or_foreign_units(field, value):
    """Zero/nonfinite scales or foreign training provenance fail closed."""
    original, certificate = calibration_fixture()
    original[field] = value
    with pytest.raises(ValueError):
        rebind_calibration(original, certificate, "old", "new")


def checkpoint_fixture():
    """Small real tensors represent unchanged model/optimizer/schedule/RNG state."""
    import torch

    old, certificate = calibration_fixture()
    new = rebind_calibration(old, certificate, "old", "new")
    saved = dict(
        contract=dict(
            split_hash="old",
            median=old["median"],
            scale=old["scale"],
            epochs=50,
            batch=4096,
        ),
        model={"weight": torch.tensor([1.0, 2.0])},
        optimizer={"state": {0: {"exp_avg": torch.tensor([0.1, 0.2])}}},
        scheduler={"updates": 10000, "epoch": 21},
        rng=torch.tensor([1, 2], dtype=torch.uint8),
        cuda_rng=[torch.tensor([3, 4], dtype=torch.uint8)],
        epoch=22,
        step=10000,
        phase="train",
        history=[dict(epoch=22, validation=dict(loss=0.13))],
        best_validation=0.12,
        training_metrics={"loss": 0.05},
        probe_baseline=0.72,
    )
    return saved, certificate, old, new


def test_checkpoint_preparation_retains_all_scientific_state():
    """Only data binding and separately retained old-domain guard evidence change."""
    from .campaign_v1 import compatible_resume_contract

    saved, certificate, old, new = checkpoint_fixture()
    prepared = prepare_checkpoint_state(saved, certificate, old, new, "a" * 64)
    for key in ("model", "optimizer", "scheduler", "rng", "cuda_rng"):
        assert prepared[key] is saved[key]
    for key in ("epoch", "step", "phase"):
        assert prepared[key] == saved[key]
    assert prepared["history"] == [] and saved["history"][0]["epoch"] == 22
    assert prepared["data_transition"]["historical_evidence"]["best_validation"] == 0.12
    assert prepared["contract"]["split_hash"] == "new"
    assert not compatible_resume_contract(
        prepared["contract"], dict(saved["contract"], split_hash="new")
    )


@pytest.mark.parametrize(
    "change", [dict(phase="validation"), dict(epoch=23), dict(history=[])]
)
def test_checkpoint_preparation_rejects_partial_epoch(change):
    """A phase checkpoint or mismatched history cannot become a data boundary."""
    saved, certificate, old, new = checkpoint_fixture()
    saved.update(change)
    with pytest.raises(ValueError, match="completed epoch"):
        prepare_checkpoint_state(saved, certificate, old, new, "a" * 64)


def test_checkpoint_preparation_rejects_changed_normalization():
    """A calibration refit would change existing weights' input units."""
    saved, certificate, old, new = checkpoint_fixture()
    new["scale"][0] = 2.0
    with pytest.raises(ValueError, match="input units"):
        prepare_checkpoint_state(saved, certificate, old, new, "a" * 64)
