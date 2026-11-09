"""Checks for paired quarantine, condition namespaces and immutable quote repair."""

import io
from types import SimpleNamespace

import numpy as np
import pytest

from .quote_quality_v1 import condition_sql, neighbor_spikes, sanitize
from .quote_repair_v1 import (
    GuardOnly,
    phase_one_payload,
    phase_two_payload,
    publish_file,
)
from .quote_rebuild_v1 import QUOTE
from .shards.phase1_contract import SAMPLE_DTYPE
from .phase2_shards.contract import BAR


def good(shape=(64,)):
    """Positive regular two-sided bars, with realistic counts and display sizes."""
    values = np.full((*shape, 18), 10, dtype=np.float32)
    values[..., (4, 10, 16)] = 100
    return values, np.ones_like(values, dtype=np.uint8)


@pytest.mark.parametrize(
    "column,value,reason",
    [
        (13, 170000, "extreme_trade_distance"),
        (10, 0, "nonpositive_size"),
        (6, -1, "nonpositive_price"),
        (9, 11, "ohlc_geometry"),
        (17, 1.5, "invalid_count"),
        (12, float("inf"), "nonfinite"),
    ],
)
def test_quarantine_preserves_trades_and_is_idempotent(column, value, reason):
    values, valid = good()
    values[30, column] = value
    clean, masks, counts = sanitize(values, valid)
    assert counts[reason] == 1 and counts["quarantined"] == 1
    assert not clean[30, 6:].any() and not masks[30, 6:].any()
    np.testing.assert_array_equal(clean[:, :6], values[:, :6])
    assert sanitize(clean, masks)[2]["quarantined"] == 0
    assert values[30, column] == value


def test_masked_quote_and_split_invariance():
    values, valid = good()
    values[30, 6:] = 0
    valid[30, 6:] = 0
    assert sanitize(values, valid)[2]["quarantined"] == 0
    values[20, 13] = 100000
    scaled = values.copy()
    scaled[..., (0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15)] *= 1000
    scaled[..., (4, 10, 16)] /= 1000
    np.testing.assert_array_equal(
        sanitize(values, valid)[1], sanitize(scaled, valid)[1]
    )


def test_condition_namespace_keeps_valid_corrections():
    rules = [
        dict(source_family="quote_conditions", token_id=48, modifier_int=81),
        dict(source_family="quote_conditions", token_id=22, modifier_int=20),
        dict(source_family="trade_conditions", token_id=81, modifier_int=81),
    ]
    expression = condition_sql(rules)
    assert "has([22, 48],t)" in expression
    assert "hasAny(" in expression and ",[22])" in expression
    assert "condition_token_5" not in expression
    with pytest.raises(ValueError):
        condition_sql([])


def test_neighbor_spike_is_diagnostic_and_not_a_future_filter():
    values, valid = good()
    # A legitimate market jump is large temporally; contemporaneous trade prices
    # confirm it. Future neighbor evidence alone must not quarantine the bar.
    values[30, 0:4] = 110
    values[30, 6:10] = 110
    values[30, 12:16] = 110
    assert neighbor_spikes(values, valid).sum() == 0
    assert sanitize(values, valid)[2]["quarantined"] == 0


def test_cpac_eleven_fold_ask_is_rejected_without_future_input():
    values, valid = good()
    values[30, 12:16] = [110, 117.7, 10, 10]
    clean, masks, counts = sanitize(values, valid)
    assert counts["extreme_trade_distance"] == 1 and counts["quarantined"] == 1
    assert not masks[30, 6:].any()
    np.testing.assert_array_equal(clean[:, :6], values[:, :6])


@pytest.mark.parametrize(
    "trade,bid,ask",
    [
        (
            [22.1719, 25.675, 22.02, 25.16],
            [15, 25.4, 15, 22.8],
            [246.96, 246.96, 22.4, 25.2],
        ),
        (
            [0.08, 0.1889, 0.08, 0.1889],
            [0.05, 0.0731, 0.05, 0.07],
            [1.21, 1.21, 0.19, 0.1985],
        ),
    ],
)
def test_lark_and_airtw_opening_spreads_are_quarantined(trade, bid, ask):
    """Real residual examples recover by close but have dislocated paired opens."""
    values, valid = good()
    values[30, :4], values[30, 6:10], values[30, 12:16] = trade, bid, ask
    clean, masks, counts = sanitize(values, valid)
    assert (
        counts["extreme_trade_distance"] == 0
    )  # Original envelope alone missed these.
    assert counts["extreme_open_spread"] == 1
    assert counts["quarantined"] == 1 and not masks[30, 6:].any()
    np.testing.assert_array_equal(clean[:, :6], values[:, :6])


def test_guard_only_uses_existing_bars_without_queries(tmp_path):
    """A completed source reconstruction needs only the stricter causal bar gate."""
    original = np.zeros(1, dtype=SAMPLE_DTYPE)
    original["values"], original["valid"] = good((1, 64))
    original["end_us"] = np.arange(1, 65)[None, :] * 1000000
    original["timeframe_us"] = 1000000
    original["values"][0, 30, 12:16] = [150, 150, 10, 10]
    # Wider trade highs make the old envelope pass, but the paired open is 15x.
    original["values"][0, 30, 1] = 20
    path = tmp_path / "samples-0.npy"
    np.save(path, original)
    coverage = np.zeros(18, dtype=np.uint64)
    payload, counts = phase_one_payload(path, "LARK", GuardOnly(), coverage=coverage)
    revised = np.load(io.BytesIO(payload), allow_pickle=False)
    assert counts["extreme_open_spread"] == 1 and counts["rebuilt_bars"] == 0
    np.testing.assert_array_equal(
        revised["values"][..., :6], original["values"][..., :6]
    )
    np.testing.assert_array_equal(revised["end_us"], original["end_us"])
    np.testing.assert_array_equal(coverage, revised["valid"].sum(axis=(0, 1)))


def test_guard_receipt_preserves_transitive_ancestry_and_resumes(tmp_path, monkeypatch):
    """A second guard pass retains the original source seal without new queries."""
    import hashlib
    from . import quote_repair_v1 as module
    from .shards.contract import digest, write_json

    source, output = tmp_path / "v2", tmp_path / "v3"
    relative = __import__("pathlib").Path("jobs/job/coarse/receipt.json")
    directory = source / relative.parent
    directory.mkdir(parents=True)
    data = np.zeros(1, dtype=SAMPLE_DTYPE)
    data["values"], data["valid"] = good((1, 64))
    data["end_us"] = np.arange(1, 65)[None, :] * 1000000
    data["timeframe_us"] = 1000000
    data["values"][0, 30, 1] = 20
    data["values"][0, 30, 12:16] = [150, 150, 10, 10]
    path = directory / "samples-0.npy"
    np.save(path, data)
    content = path.read_bytes()
    receipt = dict(
        ticker="LARK",
        identity="prior-id",
        binding="v2",
        files=[
            dict(
                name=path.name,
                bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        ],
        quote_repair=dict(
            parent_receipt="original-seal",
            policy=dict(version="bargpt-paired-quote-quarantine-v2"),
        ),
    )
    receipt["receipt_hash"] = digest(receipt)
    write_json(source / relative, receipt)
    monkeypatch.setattr(module, "LOCAL", SimpleNamespace())
    revised = module.migrate_receipt(source, output, relative, "v3-binding", 1, True)
    assert revised["quote_repair"]["parent_receipt"] == receipt["receipt_hash"]
    assert revised["quote_repair"]["root_parent_receipt"] == "original-seal"
    assert revised["quote_repair"]["counts"]["rebuilt_bars"] == 0
    assert (
        module.migrate_receipt(source, output, relative, "v3-binding", 1, True)
        == revised
    )
    module.verify_seal(revised, "receipt_hash")
    assert path.read_bytes() == content


class FakeRebuilder:
    """Deterministic canonical replacement, independent of the old corrupted high."""

    actions = []

    def get(self, ticker, frame, ends):
        result = np.zeros(len(ends), dtype=QUOTE)
        result["rebuild"] = 1
        values, valid = good((len(ends),))
        result["values"], result["valid"] = values[:, 6:], valid[:, 6:]
        return result


def test_phase_one_rebuild_restores_high_instead_of_copying_close(tmp_path):
    data = np.zeros(1, dtype=SAMPLE_DTYPE)
    data["values"], data["valid"] = good((1, 64))
    data["values"][0, 30, 13] = 170000
    data["end_us"] = 1700000000000000 + np.arange(64) * 1000000
    data["timeframe_us"] = 1000000
    path = tmp_path / "old.npy"
    np.save(path, data)
    payload, counts = phase_one_payload(path, "AAPX", FakeRebuilder())
    revised = np.load(io.BytesIO(payload), allow_pickle=False)
    assert revised["values"][0, 30, 13] == 10 and counts["changed_bars"] == 1
    for field in ("end_us", "timeframe_us", "split"):
        np.testing.assert_array_equal(data[field], revised[field])
    np.testing.assert_array_equal(data["values"][..., :6], revised["values"][..., :6])
    assert np.load(path)["values"][0, 30, 13] == 170000
    coverage = np.zeros(18, dtype=np.uint64)
    buffered, buffered_counts = phase_one_payload(
        tmp_path / "not-opened.npy",
        "AAPX",
        FakeRebuilder(),
        serialized=path.read_bytes(),
        coverage=coverage,
    )
    assert buffered == payload and buffered_counts == counts
    np.testing.assert_array_equal(coverage, revised["valid"].sum(axis=(0, 1)))


def test_packed_repair_preserves_all_non_quote_bytes(tmp_path):
    data = np.zeros(3, dtype=BAR)
    data["values"], data["valid"] = good((3,))
    data["end_us"] = 1700000000000000 + np.arange(3) * 1000000
    data["values"][1, 13] = 170000
    original = b"a" * 64 + data.tobytes() + b"z" * 64
    path = tmp_path / "payload.bin"
    path.write_bytes(original)
    descriptor = dict(kind="bar", offset=64, shape=[3], frame=0)
    payload, counts = phase_two_payload(path, [descriptor], "AAPX", FakeRebuilder())
    assert payload[:64] == b"a" * 64 and payload[-64:] == b"z" * 64
    actual = np.frombuffer(payload, dtype=BAR, count=3, offset=64)
    np.testing.assert_array_equal(actual["values"][:, :6], data["values"][:, :6])
    np.testing.assert_array_equal(actual["end_us"], data["end_us"])
    assert counts["changed_bars"] == 1 and path.read_bytes() == original


def test_transient_quote_transport_retries_identical_read(monkeypatch):
    from collections import Counter
    from . import quote_rebuild_v1 as module

    calls = []

    def flaky(client, sql, identity, **kwargs):
        calls.append((sql, identity, kwargs))
        if len(calls) < 3:
            raise ConnectionResetError("idle socket")
        return "final aggregates"

    monkeypatch.setattr(module.source, "arrow", flaky)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    worker = SimpleNamespace(client=object(), metrics=Counter())
    assert (
        module.Rebuilder.query(worker, "SELECT final", "same-query")
        == "final aggregates"
    )
    assert calls[0] == calls[1] == calls[2]
    assert worker.metrics["transport_retries"] == 2


def test_concurrent_session_audit_is_coalesced_and_committed(tmp_path):
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    import time
    from .quote_rebuild_v1 import Rebuilder

    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE intervals(binding TEXT,ticker TEXT,day TEXT,payload TEXT,PRIMARY KEY(binding,ticker,day))"
        )
    requests = []
    intervals = [
        dict(
            begin_us=1,
            end_us=100001,
            changed_messages=1,
            zero_size_messages=1,
            condition_messages=0,
        )
    ]

    def run(_):
        db = sqlite3.connect(path)

        def query(*args, **kwargs):
            requests.append(1)
            time.sleep(0.02)
            return SimpleNamespace(to_dicts=lambda: intervals)

        worker = SimpleNamespace(
            db=db,
            binding="frozen",
            bounds={
                "TEST": [
                    dict(
                        source_date="2024-01-02",
                        first_ordinal=0,
                        next_ordinal=100,
                        event_count=100,
                    )
                ]
            },
            authority={
                "rules": [
                    dict(source_family="quote_conditions", token_id=3, modifier_int=1)
                ]
            },
            metrics=Counter(),
            query=query,
        )
        try:
            return Rebuilder.intervals(worker, "TEST", "2024-01-02")
        finally:
            db.close()

    with ThreadPoolExecutor(8) as pool:
        assert list(pool.map(run, range(8))) == [intervals] * 8
    assert len(requests) == 1


def test_session_batching_never_truncates_dense_sessions_or_crosses_budget():
    from .quote_rebuild_v1 import session_batches

    rows = [
        dict(source_date=day, event_count=count)
        for day, count in [
            ("2024-01-01", 100),
            ("2024-01-02", 200),
            ("2024-01-11", 2_500_000),
            ("2024-01-20", 100),
        ]
    ]
    batches = session_batches(rows)
    assert [len(b) for b in batches] == [2, 1, 1]
    assert [r for batch in batches for r in batch] == rows
    assert (
        len(
            session_batches(
                [
                    dict(source_date="2024-01-01", event_count=1),
                    dict(source_date="2024-01-15", event_count=1),
                ]
            )
        )
        == 2
    )


def test_payload_io_restores_native_thread_priority_on_failure():
    import os
    import ctypes
    from .quote_repair_io_v1 import payload_io

    if os.name != "nt":
        pytest.skip("Native Windows priority contract")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetThreadPriority.argtypes = (ctypes.c_void_p,)
    kernel.GetThreadPriority.restype = ctypes.c_int
    before = kernel.GetThreadPriority(ctypes.c_void_p(-2))
    with pytest.raises(RuntimeError):
        with payload_io():
            assert kernel.GetThreadPriority(ctypes.c_void_p(-2)) != 0x7FFFFFFF
            raise RuntimeError("simulated payload read failure")
    assert kernel.GetThreadPriority(ctypes.c_void_p(-2)) == before


def test_buffered_publication_still_verifies_source_and_target(tmp_path):
    import hashlib

    source, target = tmp_path / "source.bin", tmp_path / "target.bin"
    original = b"original source"
    source.write_bytes(original)
    item = dict(
        name=source.name,
        bytes=len(original),
        sha256=hashlib.sha256(original).hexdigest(),
    )
    with pytest.raises(ValueError, match="Source payload changed"):
        publish_file(source, target, item, b"corrected", verified_source=b"wrong")
    assert not target.exists()
    saved, changed = publish_file(
        source, target, item, b"corrected", verified_source=original
    )
    assert (
        changed
        and target.read_bytes() == b"corrected"
        and source.read_bytes() == original
    )
    assert saved["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
