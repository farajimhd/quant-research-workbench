"""Selective publication must preserve hashes, coverage and untouched byte fields."""

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import hashlib

import numpy as np
import pytest

from .quote_selective_v1 import migrate, packed_bounds
from .quote_repair_v1 import GuardOnly
from .shards.phase1_contract import SAMPLE_DTYPE
from .shards.contract import digest, write_json
from .phase2_shards.contract import BAR


def fixture(tmp_path, spike=False):
    root = tmp_path / "source"
    relative = Path("jobs/job/day/receipt.json")
    directory = root / relative.parent
    directory.mkdir(parents=True)
    data = np.zeros(1, dtype=SAMPLE_DTYPE)
    data["values"] = 10
    data["valid"] = 1
    data["values"][..., (4, 10, 16)] = 100
    data["end_us"] = np.arange(1, 65)[None, :] * 1000000
    data["timeframe_us"] = 1000000
    if spike:
        data["values"][0, 30, 12:16] = [150, 150, 10, 10]
        data["values"][0, 30, 1] = 20
    path = directory / "samples-0.npy"
    np.save(path, data)
    content = path.read_bytes()
    item = dict(
        name=path.name,
        bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        first_start_us=0,
        last_end_us=64000000,
    )
    receipt = dict(
        ticker="TEST",
        binding="old",
        identity="identity",
        files=[item],
        valid_bars_by_channel=[64] * 18,
    )
    receipt["receipt_hash"] = digest(receipt)
    write_json(root / relative, receipt)
    return root, relative, data


def selector(intervals):
    return SimpleNamespace(
        rebuilder=GuardOnly(),
        binding="candidate-binding",
        intervals=lambda *args: intervals,
    )


def test_non_candidate_is_hashed_and_linked_without_decoding(tmp_path, monkeypatch):
    from . import quote_selective_v1 as module

    root, relative, data = fixture(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unnecessary bar decode")

    monkeypatch.setattr(module, "phase_one_payload", forbidden)
    receipt = migrate(root, tmp_path / "output", relative, "new", 1, selector([]))
    assert receipt["valid_bars_by_channel"] == [64] * 18
    assert receipt["quote_repair"]["counts"]["noncandidate_files"] == 1
    assert (
        receipt["files"][0]["sha256"]
        == hashlib.sha256(
            (root / relative.parent / "samples-0.npy").read_bytes()
        ).hexdigest()
    )


def test_non_candidate_still_rejects_payload_corruption(tmp_path):
    root, relative, data = fixture(tmp_path)
    (root / relative.parent / "samples-0.npy").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Source payload changed"):
        migrate(root, tmp_path / "output", relative, "new", 1, selector([]))


def test_candidate_reconciles_coverage_and_preserves_trades(tmp_path):
    root, relative, old = fixture(tmp_path, True)
    receipt = migrate(
        root,
        tmp_path / "output",
        relative,
        "new",
        1,
        selector([dict(begin_us=30000000, end_us=32000000)]),
    )
    revised = np.load(
        tmp_path / "output" / relative.parent / "samples-0.npy", allow_pickle=False
    )
    np.testing.assert_array_equal(old["values"][..., :6], revised["values"][..., :6])
    np.testing.assert_array_equal(old["end_us"], revised["end_us"])
    np.testing.assert_array_equal(
        receipt["valid_bars_by_channel"], revised["valid"].sum(axis=(0, 1))
    )
    assert receipt["quote_repair"]["counts"]["extreme_open_spread"] == 1


def test_packed_bounds_only_need_endpoint_clocks(tmp_path):
    bars = np.zeros(5, dtype=BAR)
    bars["end_us"] = np.arange(1, 6) * 1000000
    path = tmp_path / "payload.bin"
    path.write_bytes(b"x" * 64 + bars.tobytes() + b"y" * 64)
    assert packed_bounds(path, [dict(kind="bar", shape=[5], offset=64, frame=0)]) == (
        0,
        5000000,
    )
    with pytest.raises(ValueError, match="geometry"):
        packed_bounds(path, [dict(kind="bar", shape=[5000], offset=64, frame=0)])


def test_reuses_prior_fixed_bytes_with_original_receipt_ancestry(tmp_path):
    """A new selector must preserve completed fixes instead of starting over."""
    from copy import deepcopy
    import json

    root, relative, old = fixture(tmp_path, True)
    original = json.loads((root / relative).read_text())
    prior = deepcopy(original)
    reuse = tmp_path / "v2"
    directory = reuse / relative.parent
    directory.mkdir(parents=True)
    fixed = old.copy()
    fixed["values"][0, 30, 12:16] = 10
    path = directory / "samples-0.npy"
    np.save(path, fixed)
    prior["files"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    prior["quote_repair"] = dict(
        parent_receipt=original["receipt_hash"],
        policy=dict(version="bargpt-paired-quote-quarantine-v2"),
    )
    prior["receipt_hash"] = digest(
        {k: v for k, v in prior.items() if k != "receipt_hash"}
    )
    write_json(reuse / relative, prior)
    result = migrate(root, tmp_path / "output", relative, "new", 1, selector([]), reuse)
    assert result["files"][0]["sha256"] == prior["files"][0]["sha256"]
    assert result["quote_repair"]["parent_receipt"] == original["receipt_hash"]
    assert result["quote_repair"]["reused_receipt"] == prior["receipt_hash"]
    assert result["quote_repair"]["changed_files"] == 1
