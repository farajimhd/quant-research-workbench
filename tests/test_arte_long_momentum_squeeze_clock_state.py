from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_squeeze_clock_state import (
    TABLE, project_squeeze_breakout_clock, restore_squeeze_breakout_clock,
)


IDENTITY = dict(run_id="run-1", assignment_id="assignment-1",
                state_revision=9, snapshot_session="2026-09-24")


def test_scalar_clock_exact_roundtrip_and_named_schema():
    clock = dict(session="2026-09-24", activated_at=1790270000.0,
                 activation_event_id="event-1", closed_at=1790270001.0,
                 close=10.5, hod=11.0, last_open=10.0, trade_price=10.8,
                 trade_hod=11.1, prior_hod=11.0, hod_last_trade=10.8,
                 late_mode=False, last_entry_fill_at=1790270002.0,
                 target_reentry_not_before=1790270003.0,
                 successor_reentry_last_trade=10.9)
    row = project_squeeze_breakout_clock(clock, **IDENTITY)
    assert restore_squeeze_breakout_clock(row) == clock
    ddl = TABLE.ddl()
    assert "PARTITION BY toYYYYMM(snapshot_session)" in ddl
    assert "storage_policy = 'live_market_ssd'" in ddl
    assert not any(token in ddl for token in (" Array(", " Map(", " JSON", " payload "))


def test_absent_and_explicit_null_are_distinct():
    for clock in ({}, {"session": "2026-09-24", "activation_event_id": None}):
        assert restore_squeeze_breakout_clock(project_squeeze_breakout_clock(clock, **IDENTITY)) == clock


def test_unknown_nonfinite_wrong_session_and_bool_number_rejected():
    for clock in ({"unknown": 1}, {"close": float("nan")},
                  {"session": "2026-09-23"}, {"close": True}):
        with pytest.raises(ValueError):
            project_squeeze_breakout_clock(clock, **IDENTITY)


@pytest.mark.parametrize("mode", ["tamper", "mixed", "presence", "extra"])
def test_row_tampering_rejected(mode):
    row = project_squeeze_breakout_clock({"session": "2026-09-24", "close": 10.0}, **IDENTITY)
    broken = deepcopy(row)
    if mode == "tamper":
        broken["close"] = 11.0
    elif mode == "mixed":
        broken["assignment_id"] = "other"
    elif mode == "presence":
        broken["close_present"] = False
    else:
        broken["unmodeled"] = 1
    with pytest.raises(ValueError):
        restore_squeeze_breakout_clock(broken)
