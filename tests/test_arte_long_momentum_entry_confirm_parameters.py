from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_entry_confirm_parameters import (
    TABLES, project_entry_confirm_parameters, restore_entry_confirm_parameters,
)
from src.trading_runtime.strategy_engine import (
    HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION,
    resolve_long_momentum_parameters,
)


FAMILIES = ("liquidity_admission", "entry_momentum_confirmation", "entry_candle_confirmation")
IDENTITY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
                snapshot_id="00000000-0000-0000-0000-000000000001", session="2026-09-24")


@pytest.mark.parametrize("revision", [*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION])
def test_builtin_revision_roundtrip(revision):
    resolved = resolve_long_momentum_parameters(revision=revision)
    sections = {family: resolved[family] for family in FAMILIES}
    rows = project_entry_confirm_parameters(sections, **IDENTITY, strategy_revision=revision)
    assert restore_entry_confirm_parameters(rows) == sections
    for family, table in TABLES.items():
        ddl = table.ddl()
        assert "PARTITION BY toYYYYMM(session)" in ddl
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert not any(token in ddl for token in (" Array(", " Map(", " JSON", " payload "))
        assert set(rows[family]) == {name for name, _ in table.columns}


@pytest.mark.parametrize("family", FAMILIES)
def test_unknown_and_missing_field_fail_closed(family):
    resolved = resolve_long_momentum_parameters(revision=47)
    sections = {key: deepcopy(resolved[key]) for key in FAMILIES}
    sections[family]["unexpected"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_entry_confirm_parameters(sections, **IDENTITY, strategy_revision=47)
    del sections[family]["unexpected"]
    sections[family].pop("enabled")
    with pytest.raises(ValueError, match="missing"):
        project_entry_confirm_parameters(sections, **IDENTITY, strategy_revision=47)


def test_tamper_mixed_identity_and_extra_row_column_fail_closed():
    resolved = resolve_long_momentum_parameters(revision=47)
    sections = {key: resolved[key] for key in FAMILIES}
    rows = project_entry_confirm_parameters(sections, **IDENTITY, strategy_revision=47)
    for mode in ("tamper", "mixed", "extra"):
        broken = deepcopy(rows)
        if mode == "tamper":
            broken["liquidity_admission"]["minimum_price"] = 3.0
        elif mode == "mixed":
            broken["entry_momentum_confirmation"]["assignment_id"] = "other"
        else:
            broken["entry_candle_confirmation"]["unexpected"] = 1
        with pytest.raises(ValueError):
            restore_entry_confirm_parameters(broken)


def test_bool_is_not_accepted_as_number():
    resolved = resolve_long_momentum_parameters(revision=47)
    sections = {key: deepcopy(resolved[key]) for key in FAMILIES}
    sections["liquidity_admission"]["minimum_price"] = True
    with pytest.raises(ValueError, match="finite numeric"):
        project_entry_confirm_parameters(sections, **IDENTITY, strategy_revision=47)
