"""Exact split-membership invariants for quote-repair handoff preparation.

These CPU-only tests exercise metadata transitions, never a live trainer.
Payload correctness and complete-release gates remain separate audit evidence.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from .quote_handoff_v1 import membership_hash, rebind_split
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
