from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_profit_targets import (
    TABLES, project_profit_targets, restore_profit_targets,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(values, present=True):
    return project_profit_targets(
        values, present=present, assignment_id=KEY["assignment_id"],
        revision=KEY["revision"], snapshot_id=KEY["snapshot_id"],
        session=KEY["session"])


def test_target_prices_keep_order_and_numeric_source_kind():
    values = [103.5, 105, 107.25]
    rows = _project(values)
    assert restore_profit_targets(rows) == (True, values)
    assert rows["prices"][1]["price_int"] == 105
    assert rows["prices"][1]["price_float"] is None
    assert restore_profit_targets(_project([])) == (True, [])
    assert restore_profit_targets(_project(None, present=False)) == (False, [])
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_target_prices_composite_and_state_cold_roundtrip():
    source = {**_state(), "structural_profit_targets": [103.5, 105, 107.25]}
    projected = project_modeled_assignment_state(source, **KEY)
    assert restore_modeled_assignment_state(projected, **KEY) == source
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    assert all(table in STATE_TABLES for table in TABLES)
    flat, commit = project_state_snapshot(source, **KEY)
    class Storage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name else flat[table])
    assert recover_state_snapshot(Storage(), **KEY,
                                  expected_commit_hash=commit["content_hash"]) == source


@pytest.mark.parametrize("values", [
    [0.0], [-1], [True], [float("nan")], [float("inf")],
    ["103"], tuple([103.0]), list(range(257)),
])
def test_target_prices_fail_closed_on_unmodeled_values(values):
    with pytest.raises(ValueError):
        _project(values)


def test_target_prices_detect_reorder_duplicate_and_ambiguous_numeric_kind():
    rows = _project([103.5, 105.0])
    altered = deepcopy(rows)
    altered["prices"].reverse()
    with pytest.raises(ValueError):
        restore_profit_targets(altered)
    altered = deepcopy(rows)
    altered["prices"].append(deepcopy(altered["prices"][0]))
    with pytest.raises(ValueError):
        restore_profit_targets(altered)
    altered = deepcopy(rows)
    altered["prices"][0]["price_int"] = 103
    with pytest.raises(ValueError):
        restore_profit_targets(altered)
