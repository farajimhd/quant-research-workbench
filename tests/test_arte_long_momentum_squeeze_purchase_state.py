from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_squeeze_purchase_state import (
    ADDED_LEVEL_TABLE, KEY_TABLE, LEDGER_TABLE, REQUEST_TABLE,
    project_squeeze_purchase_state, restore_squeeze_purchase_state,
)


IDENTITY = dict(run_id="run-1", assignment_id="assignment-1",
                state_revision=8, session="2026-09-24")


def _sample():
    request = dict(lifecycle=100.5, keys=["level-a", "level-b"],
                   episode_id=101.0, filled=True, terminal=True)
    return {
        "squeeze_entry": {"successor_added_levels": ["level-a"]},
        "squeeze_breakout": {
            "momentum_requests": {"intent-1": deepcopy(request)},
            "midpoint_add_requests": {"intent-1": deepcopy(request)},
        },
    }


def test_purchase_ledger_exact_roundtrip_and_named_schema():
    source = _sample()
    rows = project_squeeze_purchase_state(source, **IDENTITY)
    assert restore_squeeze_purchase_state(rows) == source
    for table in (LEDGER_TABLE, REQUEST_TABLE, KEY_TABLE, ADDED_LEVEL_TABLE):
        ddl = table.ddl()
        assert "PARTITION BY toYYYYMM(session)" in ddl
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert not any(token in ddl for token in (" Array(", " Map(", " JSON", " payload "))


def test_empty_presence_and_absence_are_distinct():
    for source in ({}, {"squeeze_entry": {}, "squeeze_breakout": {}},
                   {"squeeze_entry": {"successor_added_levels": []},
                    "squeeze_breakout": {"momentum_requests": {}, "midpoint_add_requests": {}}}):
        rows = project_squeeze_purchase_state(source, **IDENTITY)
        assert restore_squeeze_purchase_state(rows) == source


@pytest.mark.parametrize("mode", ["drop-key", "drop-request", "reorder-key", "tamper-flag",
                                  "mixed-identity", "extra-column"])
def test_partial_or_tampered_rows_fail_closed(mode):
    rows = project_squeeze_purchase_state(_sample(), **IDENTITY)
    broken = deepcopy(rows)
    if mode == "drop-key":
        broken["request_keys"].pop()
    elif mode == "drop-request":
        broken["requests"].pop()
    elif mode == "reorder-key":
        broken["request_keys"][0], broken["request_keys"][1] = (
            broken["request_keys"][1], broken["request_keys"][0])
    elif mode == "tamper-flag":
        broken["requests"][0]["filled"] = False
    elif mode == "mixed-identity":
        broken["successor_added_levels"][0]["assignment_id"] = "other"
    else:
        broken["ledger"]["unmodeled"] = 1
    with pytest.raises(ValueError):
        restore_squeeze_purchase_state(broken)


def test_unmodeled_request_and_duplicate_level_fail_closed():
    source = _sample()
    source["squeeze_breakout"]["momentum_requests"]["intent-1"]["unknown"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_squeeze_purchase_state(source, **IDENTITY)
    source = _sample()
    source["squeeze_entry"]["successor_added_levels"].append("level-a")
    with pytest.raises(ValueError, match="unique"):
        project_squeeze_purchase_state(source, **IDENTITY)
