from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_add_step_uses import (
    TABLES, add_step_catalog, project_add_step_uses, restore_add_step_uses,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from src.trading_runtime.strategy_engine import resolve_long_momentum_parameters
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(value, present=True, allowed=("legacy-confirmed-add",)):
    return project_add_step_uses(
        value, present=present, allowed_step_ids=allowed,
        assignment_id=KEY["assignment_id"], revision=KEY["revision"],
        snapshot_id=KEY["snapshot_id"], session=KEY["session"])


def test_catalog_is_pinned_to_typed_parameter_revision():
    old = resolve_long_momentum_parameters(revision=26)
    new = resolve_long_momentum_parameters(revision=47)
    assert add_step_catalog(old, strategy_revision=26) == ("legacy-confirmed-add",)
    assert add_step_catalog(new, strategy_revision=47) == ()
    with pytest.raises(ValueError, match="closed pinned"):
        add_step_catalog({**old, "phase_policy": {}}, strategy_revision=26)


def test_add_step_uses_exact_presence_and_catalog_roundtrip():
    absent = _project(None, present=False)
    assert restore_add_step_uses(absent, allowed_step_ids=("legacy-confirmed-add",)) == (False, {})
    empty = _project({})
    assert restore_add_step_uses(empty, allowed_step_ids=("legacy-confirmed-add",)) == (True, {})
    rows = _project({"legacy-confirmed-add": 2})
    assert restore_add_step_uses(rows, allowed_step_ids=("legacy-confirmed-add",)) == (
        True, {"legacy-confirmed-add": 2})
    assert len(TABLES) == 2
    assert all("Map" not in kind and "JSON" not in kind
               for table in TABLES for _, kind in table.columns)


def test_add_step_composite_and_state_cold_roundtrip():
    source = {**_state(), "add_step_uses": {"legacy-confirmed-add": 1}}
    allowed = ("legacy-confirmed-add",)
    rows = project_modeled_assignment_state(source, **KEY, add_step_ids=allowed)
    assert restore_modeled_assignment_state(rows, **KEY, add_step_ids=allowed) == source
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    assert all(table in STATE_TABLES for table in TABLES)
    flat, commit = project_state_snapshot(source, **KEY, add_step_ids=allowed)
    class Storage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name else flat[table])
    assert recover_state_snapshot(Storage(), **KEY,
                                  expected_commit_hash=commit["content_hash"],
                                  add_step_ids=allowed) == source
    with pytest.raises(ValueError, match="uncataloged"):
        recover_state_snapshot(Storage(), **KEY,
                               expected_commit_hash=commit["content_hash"])


@pytest.mark.parametrize("value", [
    {"foreign": 1}, {"legacy-confirmed-add": -1},
    {"legacy-confirmed-add": True}, {"legacy-confirmed-add": 1.0},
])
def test_add_step_rejects_uncataloged_or_untyped(value):
    with pytest.raises(ValueError):
        _project(value)


def test_add_step_rejects_duplicate_child_and_hash_mismatch():
    rows = _project({"legacy-confirmed-add": 1})
    corrupt = deepcopy(rows)
    corrupt["steps"].append(deepcopy(corrupt["steps"][0]))
    with pytest.raises(ValueError):
        restore_add_step_uses(corrupt, allowed_step_ids=("legacy-confirmed-add",))
