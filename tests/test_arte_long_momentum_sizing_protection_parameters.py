from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_sizing_protection_parameters import (
    RISK_MULTIPLE_TABLE, TABLES, project_sizing_protection_parameters,
    restore_sizing_protection_parameters,
)
from src.trading_runtime.strategy_engine import (
    HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION,
    resolve_long_momentum_parameters,
)


IDENTITY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
                snapshot_id="00000000-0000-0000-0000-000000000001", session="2026-09-24")


@pytest.mark.parametrize("revision", [*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION])
def test_all_builtin_revisions_exact_roundtrip(revision):
    resolved = resolve_long_momentum_parameters(revision=revision)
    original = {key: resolved[key] for key in ("sizing", "protection")}
    rows = project_sizing_protection_parameters(**original, **IDENTITY, strategy_revision=revision)
    assert restore_sizing_protection_parameters(rows) == original
    for table in (*TABLES.values(), RISK_MULTIPLE_TABLE):
        ddl = table.ddl()
        assert "PARTITION BY toYYYYMM(session)" in ddl
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert not any(token in ddl for token in (" Array(", " Map(", " JSON", " payload "))


@pytest.mark.parametrize("path", ["sizing", "stop", "trailing", "profit_ladder", "luld_profit_target"])
def test_unknown_source_field_rejected(path):
    resolved = resolve_long_momentum_parameters(revision=47)
    if path == "sizing":
        resolved[path]["unmodeled"] = 1
    else:
        resolved["protection"][path]["unmodeled"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_sizing_protection_parameters(resolved["sizing"], resolved["protection"],
                                             **IDENTITY, strategy_revision=47)


def test_missing_reordered_and_tampered_children_rejected():
    resolved = resolve_long_momentum_parameters(revision=47)
    rows = project_sizing_protection_parameters(resolved["sizing"], resolved["protection"],
                                                **IDENTITY, strategy_revision=47)
    for change in ("drop", "reorder", "tamper"):
        broken = deepcopy(rows)
        if change == "drop":
            broken["risk_multiples"].pop()
        elif change == "reorder":
            broken["risk_multiples"][0], broken["risk_multiples"][1] = (
                broken["risk_multiples"][1], broken["risk_multiples"][0])
        else:
            broken["stop"]["maximum_risk_pct"] = 99.0
        with pytest.raises(ValueError):
            restore_sizing_protection_parameters(broken)


def test_type_and_unmodeled_restored_column_rejected():
    resolved = resolve_long_momentum_parameters(revision=47)
    resolved["sizing"]["initial_quantity"] = True
    with pytest.raises(ValueError, match="finite numeric"):
        project_sizing_protection_parameters(resolved["sizing"], resolved["protection"],
                                             **IDENTITY, strategy_revision=47)
    resolved["sizing"]["initial_quantity"] = 100.0
    rows = project_sizing_protection_parameters(resolved["sizing"], resolved["protection"],
                                                **IDENTITY, strategy_revision=47)
    rows["trailing"]["extra"] = 1
    with pytest.raises(ValueError, match="columns"):
        restore_sizing_protection_parameters(rows)
