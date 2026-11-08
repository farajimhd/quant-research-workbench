"""Lossless sparse-history batching, deferred warmup and auditable pruning."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from types import SimpleNamespace

import numpy as np
import pytest

from research.bar_gpt.v5.phase2_shards.contract import BAR, SPECS, Context, digest
from research.bar_gpt.v5.phase2_shards.producer import Worker
from research.bar_gpt.v5.phase2_shards.storage import Catalog, restore
from research.bar_gpt.v5.shards.sql import clock


def observed(rows):
    result = np.zeros(len(rows), dtype=BAR)
    result["values"], result["valid"] = 1, 1
    result["end_us"] = [clock(row["source_date"], 16) for row in rows]
    return result


def test_sparse_history_coalesces_without_changing_any_bar():
    rows = [
        dict(source_date=str(date(2020, 1, 1) + timedelta(days=i)), event_count=10)
        for i in range(601)
    ]
    worker = Worker.__new__(Worker)
    worker.units, worker.rows = [[rows[-1]]], rows
    worker.gate = SimpleNamespace(
        fine_warmup_events=250000,
        coarse_warmup_events=32000000,
        fine_warmup_sessions=120,
    )
    calls = []

    def fetch(part, coarse):
        calls.append((len(part), coarse))
        return [observed(part) for _ in SPECS]

    worker.fetch = fetch
    actual = worker.warmup()
    for array, (_, width, _) in zip(actual, SPECS):
        np.testing.assert_array_equal(array, observed(rows[:-1])[-width:])
    assert sum(not coarse for _, coarse in calls) == 5  # Formerly 544 requests.
    assert all(count <= 120 for count, coarse in calls if not coarse)


def test_empty_ranges_defer_warmup_until_first_observation(tmp_path):
    worker = Worker.__new__(Worker)
    worker.root = tmp_path
    worker.stream = dict(key="stream", ticker="EXAMPLE", year=2023, liquid=0)
    worker.plan, worker.revision = dict(hash="plan"), "source"
    worker.gate = SimpleNamespace()
    worker.units = [
        [dict(source_date=f"2023-01-0{d}", event_count=100)] for d in (3, 4)
    ]
    worker.raw_trade_bound = lambda: dict(raw_trade_events=1000)
    empty = lambda: [np.empty(0, dtype=BAR) for _ in SPECS]
    calls = []
    worker.warmup = lambda: (calls.append(True) or empty())

    def fetch(rows, coarse):
        result = empty()
        if rows[0]["source_date"].endswith("04") and not coarse:
            result[0] = observed(rows)
        return result

    worker.fetch = fetch
    with ThreadPoolExecutor(1) as io:
        worker.io = io
        worker.step(0).result()
        assert calls == [] and not restore(tmp_path, worker.stream, 0).initialized
        worker.step(1).result()
        assert calls == [True] and restore(tmp_path, worker.stream, 1).initialized
    # Legacy checkpoints always performed initial warmup, even if it was empty.
    path = tmp_path / "streams/stream/0001/context.npz"
    with np.load(path) as saved:
        old = {key: saved[key].copy() for key in saved.files if key != "initialized"}
    np.savez(path, **old)
    assert restore(tmp_path, worker.stream, 1).initialized


def test_raw_event_pruning_cannot_remove_a_possible_full_block(tmp_path):
    catalog = Catalog(tmp_path)
    proof = dict(stream="tiny", raw_trade_events=255)
    proof["hash"] = digest(proof)
    catalog.remember_check(proof, skipped=True)
    assert catalog.stats()["skipped_streams"] == 1
    catalog.remember_check(proof, skipped=True)
    assert catalog.stats()["skipped_streams"] == 1
    proof = dict(stream="possible", raw_trade_events=256)
    proof["hash"] = digest(proof)
    with pytest.raises(ValueError, match="may contain"):
        catalog.remember_check(proof, skipped=True)
    catalog.db.close()


def test_generation_logs_are_coalesced_and_flushed(tmp_path, monkeypatch):
    from research.bar_gpt.v5.phase2_shards import telemetry

    sent = []
    fake = SimpleNamespace(
        config=SimpleNamespace(update=lambda *args, **kwargs: None),
        url="https://example.test/run",
        log=sent.append,
        save=lambda *args, **kwargs: None,
        finish=lambda: None,
    )
    monkeypatch.setattr(telemetry, "init_wandb", lambda **kwargs: fake)
    monkeypatch.setattr(telemetry, "discover_env_files", lambda *_: [])
    logger = telemetry.Telemetry(tmp_path, {})
    logger.update(dict(state="running", units=12, blocks=[]))
    logger.finish()
    assert sent[-1]["generation/units"] == 12
    assert (
        json.loads(
            (tmp_path / "generation-metrics.jsonl").read_text().splitlines()[-1]
        )["generation/units"]
        == 12
    )


def test_buffered_publication_keeps_exact_npy_bytes_and_source_hash(tmp_path):
    """Structured payloads remain readable and bit-identical to the legacy format."""
    import hashlib
    from research.bar_gpt.v5.phase2_shards.storage import save_array

    rows = observed([dict(source_date="2024-01-02")])
    expected = tmp_path / "legacy.npy"
    with expected.open("wb") as handle:
        np.save(handle, rows, allow_pickle=False)
    receipt = save_array(tmp_path, "buffered.npy", rows)
    payload = (tmp_path / receipt["name"]).read_bytes()
    assert payload == expected.read_bytes()
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    np.testing.assert_array_equal(np.load(tmp_path / receipt["name"]), rows)


def test_unchanged_raw_trade_proof_does_not_repeat_a_write_transaction(tmp_path):
    """Repeated range receipts must not fsync the same yearly proof again."""
    catalog = Catalog(tmp_path)
    proof = dict(stream="existing", raw_trade_events=500)
    proof["hash"] = digest(proof)
    catalog.remember_check(proof)
    statements = []
    catalog.db.set_trace_callback(statements.append)
    catalog.remember_check(proof)
    assert not any(s.startswith(("INSERT", "BEGIN", "COMMIT")) for s in statements)
    changed = dict(stream="existing", raw_trade_events=501)
    changed["hash"] = digest(changed)
    with pytest.raises(ValueError, match="changed"):
        catalog.remember_check(changed)
    catalog.db.close()


def test_receipt_batch_is_one_commit_and_rolls_back_counters(tmp_path, monkeypatch):
    """A failed batch preserves ledger/counter parity and existing selection IDs."""

    def receipt(key, liquid, count):
        row = dict(
            stream=key, unit=0, bytes=100, timing={}, liquid=liquid, splits=[0] * count
        )
        return dict(row, hash=digest(row))

    catalog = Catalog(tmp_path)
    statements = []
    catalog.db.set_trace_callback(statements.append)
    catalog.register_many([receipt("liquid", 1, 3), receipt("sparse", 0, 1)])
    assert statements.count("COMMIT") == 1
    baseline = catalog.stats()
    assert sum(row["selected"] for row in baseline["blocks"]) == 4

    def fail():
        raise RuntimeError("Simulated selection failure")

    monkeypatch.setattr(catalog, "select", fail)
    with pytest.raises(RuntimeError, match="Simulated"):
        catalog.register_many([receipt("new", 1, 3)])
    assert catalog.stats() == baseline
    assert catalog.db.execute("SELECT count(*) FROM units").fetchone()[0] == 2
    catalog.db.close()


def test_deferred_metadata_is_loaded_only_in_the_lane_task(tmp_path, monkeypatch):
    """Coordinator admission must not run Polars inventory loading synchronously."""
    from research.bar_gpt.v5.phase2_shards import producer

    calls = []
    monkeypatch.setattr(producer, "bounds", lambda *args: calls.append(True) or [])
    worker = Worker(
        tmp_path,
        {"revision": "test"},
        {"key": "done", "year": 2025},
        None,
        None,
        None,
        2_000_000,
        defer_metadata=True,
    )
    assert not calls and worker.units is None
    assert isinstance(worker.step(0), producer.EndOfStream)
    assert calls == [True]


def test_bulk_selection_preserves_row_order_and_purges(tmp_path):
    """Native window assignment must retain the original exact per-split 3:1 rule."""
    catalog = Catalog(tmp_path)
    receipts = []
    for key, liquid, splits in (
        ("first", 1, [0, 1, 0, 2, 1, 0, 1]),
        ("second", 0, [1, 0, 2]),
        ("third", 1, [0, 1]),
    ):
        receipt = dict(
            stream=key, unit=0, bytes=100, timing={}, liquid=liquid, splits=splits
        )
        receipts.append(dict(receipt, hash=digest(receipt)))
    catalog.register_many(receipts)
    rows = catalog.db.execute(
        "SELECT stream,ordinal,liquid,split FROM blocks ORDER BY rowid"
    ).fetchall()
    expected = []
    for split in (0, 1):
        liquid = [
            (key, ordinal)
            for key, ordinal, group, label in rows
            if label == split and group == 1
        ]
        sparse = [
            (key, ordinal)
            for key, ordinal, group, label in rows
            if label == split and group == 0
        ]
        groups = min(len(liquid) // 3, len(sparse))
        expected.extend(liquid[: groups * 3] + sparse[:groups])
    assert (
        catalog.db.execute(
            "SELECT stream,ordinal FROM blocks WHERE selection IS NOT NULL ORDER BY selection"
        ).fetchall()
        == expected
    )
    assert (
        catalog.db.execute(
            "SELECT count(selection) FROM blocks WHERE split=2"
        ).fetchone()[0]
        == 0
    )
    catalog.db.close()


def test_isolated_ledger_acknowledges_commits_and_preserves_failed_batches(tmp_path):
    """Exercise the actual spawned owner, including propagation of a sealed-input failure."""
    from research.bar_gpt.v5.phase2_shards.ledger_process import LedgerProcess

    ledger = LedgerProcess(tmp_path)
    row = dict(stream="committed", unit=0, bytes=100, timing={}, liquid=1, splits=[0])
    receipt = dict(row, hash=digest(row))
    try:
        ledger.register_many([receipt])
        assert ledger.stats()["units"] == 1
        assert ledger.stats()["ledger_settings"]["synchronous"] == 2
        assert ledger.stats()["ledger_settings"]["cache_size"] == -2097152
        assert ledger.lane_status("committed") == (None, False, 0)
        with pytest.raises(RuntimeError, match="Receipt seal mismatch"):
            ledger.register_many([dict(receipt, hash="invalid")])
        assert ledger.stats()["units"] == 1
    finally:
        ledger.close()
    assert not ledger.process.is_alive()
    reopened = Catalog(tmp_path)
    assert reopened.stats()["units"] == 1
    reopened.db.close()


def test_bulk_selection_vm_work_does_not_grow_quadratically(tmp_path):
    """Doubling a balanced batch must not rescan every chosen row per update."""
    work = []
    for scale in (1, 2):
        root = tmp_path / str(scale)
        root.mkdir()
        catalog = Catalog(root)
        ticks = [0]

        def progress():
            ticks[0] += 1
            return 0

        catalog.db.set_progress_handler(progress, 1000)
        receipts = []
        for key, liquid, count in (
            ("liquid", 1, 3000 * scale),
            ("sparse", 0, 1000 * scale),
        ):
            row = dict(
                stream=key,
                unit=0,
                bytes=100,
                timing={},
                liquid=liquid,
                splits=[0] * count,
            )
            receipts.append(dict(row, hash=digest(row)))
        catalog.register_many(receipts)
        assert sum(row["selected"] for row in catalog.stats()["blocks"]) == 4000 * scale
        work.append(ticks[0])
        catalog.db.close()
    assert work[1] < work[0] * 3
