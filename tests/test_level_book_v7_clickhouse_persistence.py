import research.level_book.v7.clickhouse_persistence as persistence
import scripts.migrate_level_book_v7_to_clickhouse as migration
from research.level_book.v7.clickhouse_persistence import (
    DDL, compact_checkpoints, datetime64_ns, epoch_ns,
)


def level(level_id="a", role="resistance", price=10.0):
    fit = dict(status="estimated", count=3, center=price, scale=.1, lower=price-.2,
        upper=price+.2, resolution=.01, coverage=.8, degrees_of_freedom=4, scale_at_floor=False)
    return dict(id=level_id, role=role, role_segments=[dict(start=1, role=role)], origin_session="2026-01-01",
        price=price, lower=price-.2, upper=price+.2, association_radius=.5, fit=fit,
        observations=[1, 2, 3], qualified=True, historical=True)


def book(session, stamp, levels, checkpoint):
    return dict(ticker="TEST", session=session, available_at=stamp, retrospective=True, levels=levels,
        input_hash="input-"+session, checkpoint_hash=checkpoint, prior_checkpoint_hash="parent")


def test_nanosecond_timestamp_format_is_exact():
    assert epoch_ns("1780000000.123456789") == 1780000000123456789
    assert datetime64_ns(1780000000123456789).endswith(".123456789")


def test_retrospective_states_coalesce_and_role_change_closes_prior():
    checkpoints = [
        book("2026-01-01", 100, [level()], "one"),
        book("2026-01-02", 200, [level()], "two"),
        book("2026-01-03", 300, [level(role="support")], "three"),
    ]
    intervals, coverage, terminal = compact_checkpoints(checkpoints, "f" * 64)
    assert len(intervals) == 2
    assert intervals[0]["role"] == "resistance"
    assert intervals[0]["valid_from"].endswith("00:01:40.000000000")
    assert intervals[0]["valid_to"].endswith("00:05:00.000000000")
    assert intervals[1]["role"] == "support" and intervals[1]["valid_to"] is None
    assert len(coverage) == 3 and terminal["checkpoint_hash"] == "three"


def test_unchanged_projection_is_not_rehashed(monkeypatch):
    calls = 0
    original = persistence.projection_hash
    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)
    monkeypatch.setattr(persistence, "projection_hash", counted)
    intervals, _, _ = compact_checkpoints([
        book("2026-01-01", 100, [level()], "one"),
        book("2026-01-02", 200, [level()], "two"),
        book("2026-01-03", 300, [level(role="support")], "three"),
    ], "f" * 64)
    assert len(intervals) == 2
    assert calls == 2


def test_disappearing_level_gets_valid_to_without_removal_row():
    intervals, _, _ = compact_checkpoints([
        book("2026-01-01", 100, [level()], "one"),
        book("2026-01-02", 200, [], "two"),
    ], "f" * 64)
    assert len(intervals) == 1 and intervals[0]["valid_to"] is not None


def test_schema_uses_arte_nanoseconds_and_no_book_version_column():
    ddl = "\n".join(DDL)
    assert "arte.structural_levels_v7" in ddl
    assert "DateTime64(9, 'UTC')" in ddl
    assert "book_version" not in ddl
    assert "storage_policy = 'live_market_ssd'" in ddl


def test_worker_budget_is_bounded_by_cpu_and_memory(monkeypatch):
    monkeypatch.setattr(migration.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(migration, "available_memory_bytes", lambda: 12 * migration.GIB)
    budget = migration.worker_budget(None)
    assert budget["workers"] == 9
    assert budget["maximum_workers"] == 9


def test_controller_lock_rejects_overlapping_run(tmp_path):
    lock = tmp_path / "controller.lock"
    with migration.exclusive_controller(lock):
        try:
            with migration.exclusive_controller(lock):
                raise AssertionError("overlapping controller acquired the lock")
        except ValueError as exc:
            assert "Another V7 arte migration controller is active" in str(exc)
