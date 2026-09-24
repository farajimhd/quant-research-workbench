from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_squeeze_progress_state import (
    BROKEN_LEVEL_TABLE, MACD_TABLE, MANIFEST_TABLE, PROGRESS_TABLE,
    project_squeeze_progress_state, restore_squeeze_progress_state,
)
from src.trading_runtime.early_squeeze_momentum import record_session_breaks


IDENTITY = dict(run_id="run-1", assignment_id="assignment-1",
                state_revision=10, session="2026-09-24")


def _source():
    return dict(
        macd={
            "episode_1s": dict(open=True, episode_id=100.0, observed_at=101.0,
                               line=0.4, signal=0.3),
            "gate_100ms": dict(observed_at=101.1, open=True, line=0.42, signal=0.31),
            "completed_1s_base": dict(at=101.0, line=0.4, signal=0.3, slow=10.0),
            "completed_1s": dict(at=101.0, line=0.4, signal=0.3, slow=10.0),
            "completed_5s": dict(at=100.0, line=0.35, signal=0.2),
        },
        session_targets=dict(broken_levels=["r1", "r2", "r3"], target_multiplier=8),
        entry_progress=dict(broken_levels=["r1"], target_multiplier=8,
                            submitted_multiplier=5, target_session_step=1),
    )


def test_macd_and_progress_exact_roundtrip_and_named_schema():
    source = _source()
    rows = project_squeeze_progress_state(**source, **IDENTITY)
    assert restore_squeeze_progress_state(rows) == source
    for table in (MANIFEST_TABLE, MACD_TABLE, PROGRESS_TABLE, BROKEN_LEVEL_TABLE):
        ddl = table.ddl()
        assert "PARTITION BY toYYYYMM(session)" in ddl
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert not any(token in ddl for token in (" Array(", " Map(", " JSON", " payload "))


def test_absence_and_explicit_empty_progress_distinct():
    for session, entry in ((None, None), ({}, {}),
                           ({"broken_levels": []}, {"broken_levels": []})):
        source = dict(macd={}, session_targets=session, entry_progress=entry)
        assert restore_squeeze_progress_state(
            project_squeeze_progress_state(**source, **IDENTITY)) == source


@pytest.mark.parametrize("mode", ["drop-macd", "drop-level", "reorder", "tamper", "unknown"])
def test_incomplete_or_tampered_rows_fail_closed(mode):
    rows = project_squeeze_progress_state(**_source(), **IDENTITY)
    broken = deepcopy(rows)
    if mode == "drop-macd":
        broken["macd"].pop()
    elif mode == "drop-level":
        broken["broken_levels"].pop()
    elif mode == "reorder":
        broken["broken_levels"][0], broken["broken_levels"][1] = (
            broken["broken_levels"][1], broken["broken_levels"][0])
    elif mode == "tamper":
        broken["macd"][0]["line"] = 1.0
    else:
        broken["progress"]["unmodeled"] = 1
    with pytest.raises(ValueError):
        restore_squeeze_progress_state(broken)


def test_unknown_producer_field_and_nonfinite_value_fail_closed():
    source = _source()
    source["macd"]["episode_1s"]["unknown"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_squeeze_progress_state(**source, **IDENTITY)
    source = _source()
    source["entry_progress"]["target_multiplier"] = float("nan")
    with pytest.raises(ValueError, match="unsigned integer"):
        project_squeeze_progress_state(**source, **IDENTITY)


def test_actual_session_break_producer_shape_roundtrip():
    progress = {}
    record_session_breaks(progress, [
        {"level": {"unified_level_id": key}} for key in ("r1", "r2", "r3")
    ])
    source = dict(macd={}, session_targets=progress, entry_progress=None)
    assert restore_squeeze_progress_state(
        project_squeeze_progress_state(**source, **IDENTITY)) == source
