"""Assignment reads share the authoritative OMS groups without global scans."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.order_management import OrderManagementEngine


def _group(group_id: str, assignment_id: str, second: int):
    state = {"filled": 0}
    group = SimpleNamespace(
        group_id=group_id,
        account_id="DU1",
        intent=SimpleNamespace(metadata={"assignment_id": assignment_id}),
        created_at=datetime(2026, 8, 18, 12, 0, second, tzinfo=timezone.utc),
        state_for_test=state,
    )
    group.snapshot = lambda _version: (group.group_id, state["filled"])
    return group


def test_assignment_index_reads_current_groups_in_global_snapshot_order():
    engine = OrderManagementEngine.__new__(OrderManagementEngine)
    engine._groups = {}
    engine._groups_by_assignment = {}
    engine.policy = SimpleNamespace(version="test")
    later = _group("later", "assignment-1", 2)
    foreign = _group("foreign", "assignment-2", 1)
    earlier = _group("earlier", "assignment-1", 0)
    for group in (later, foreign, earlier):
        engine._remember_group(group)
    assert engine.snapshots_for_assignment("DU1", "assignment-1") == [
        ("earlier", 0), ("later", 0)]
    later.state_for_test["filled"] = 7
    assert engine.snapshots_for_assignment("DU1", "assignment-1") == [
        ("earlier", 0), ("later", 7)]
    assert engine.snapshots_for_assignment("DU1", "assignment-2") == [
        ("foreign", 0)]
    assert engine.snapshots() == [
        ("earlier", 0), ("foreign", 0), ("later", 7)]
    with pytest.raises(RuntimeError, match="registered twice"):
        engine._remember_group(later)
