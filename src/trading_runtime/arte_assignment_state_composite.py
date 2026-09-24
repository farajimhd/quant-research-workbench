"""Inactive, fail-closed composition of exactly modeled assignment-state slices.

This is an admission boundary, not a complete live assignment state journal.
Only the named keys below can be recovered; callers must not strip a live state
to make it pass this boundary.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.trading_runtime.arte_campaign_control_projection import (
    _FLAGS, _IDENTITY, project_campaign_control_state, restore_campaign_control_state,
)
from src.trading_runtime.arte_grouped_resistance_projection import (
    project_grouped_resistance_state, restore_grouped_resistance_state,
)
from src.trading_runtime.arte_long_momentum_squeeze_purchase_state import (
    project_squeeze_purchase_state, restore_squeeze_purchase_state,
)
from src.trading_runtime.arte_long_momentum_squeeze_clock_state import (
    _FIELDS as _CLOCK_FIELDS, project_squeeze_breakout_clock,
    restore_squeeze_breakout_clock,
)
from src.trading_runtime.arte_long_momentum_squeeze_progress_state import (
    project_squeeze_progress_state, restore_squeeze_progress_state,
)
from src.trading_runtime.arte_long_momentum_squeeze_v7_evidence import (
    project_v7_evidence_set, restore_v7_evidence_set,
)


_CAMPAIGN_KEYS = frozenset(_IDENTITY) | frozenset(_FLAGS) | {"campaign_policy"}
_MACD_KEYS = {
    "macd_1s": "episode_1s", "macd_100ms": "gate_100ms",
    **{f"successor_completed_macd_{frame}": f"completed_{frame}"
       for frame in ("1s_base", "1s", "5s", "10s", "30s")},
}
_ENTRY_LEVEL_KEYS = frozenset({"anchor", "recovery_trigger_anchor"})
_BREAKOUT_LEVEL_KEYS = frozenset({"latest_broken_resistance"})
_SQUEEZE_ENTRY_KEYS = frozenset({"successor_added_levels", "broken_levels",
                                 "target_multiplier", "submitted_multiplier",
                                 "target_session_step"}) | _ENTRY_LEVEL_KEYS
_SQUEEZE_BREAKOUT_KEYS = (frozenset({"momentum_requests", "midpoint_add_requests",
                                    "session_targets", "frozen_gap"})
                          | frozenset(_CLOCK_FIELDS) | _BREAKOUT_LEVEL_KEYS
                          | frozenset(_MACD_KEYS))
_TOP_LEVEL = _CAMPAIGN_KEYS | {"vwap_ladder_market", "squeeze_entry", "squeeze_breakout"}


def _partition(state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(state, Mapping) or set(state) - _TOP_LEVEL:
        raise ValueError("assignment state has unmodeled top-level fields")
    for key, allowed in (("squeeze_entry", _SQUEEZE_ENTRY_KEYS),
                         ("squeeze_breakout", _SQUEEZE_BREAKOUT_KEYS)):
        if key in state and (not isinstance(state[key], Mapping) or set(state[key]) - allowed):
            raise ValueError(f"{key} has unmodeled nested fields")
    breakout = state.get("squeeze_breakout", {})
    if "frozen_gap" in breakout and (not isinstance(breakout["frozen_gap"], Mapping)
                                     or set(breakout["frozen_gap"]) != {"levels"}):
        raise ValueError("frozen_gap has unmodeled nested fields")
    campaign = {key: state[key] for key in _CAMPAIGN_KEYS if key in state}
    squeeze = {key: state[key] for key in ("squeeze_entry", "squeeze_breakout") if key in state}
    return campaign, squeeze


def project_modeled_assignment_state(
    state: Mapping[str, Any], *, run_id: str, assignment_id: str,
    revision: int, snapshot_id: str, session: str,
) -> dict[str, Any]:
    """Project only closed state slices; never silently omit unknown state."""
    campaign, squeeze = _partition(state)
    rows = {
        "campaign": project_campaign_control_state(
            campaign, assignment_id=assignment_id, revision=revision,
            snapshot_id=snapshot_id, session=session),
        "grouped_resistance": None,
        "squeeze_purchase": project_squeeze_purchase_state(
            {key: {nested: value for nested, value in values.items()
                   if nested in ({"successor_added_levels"} if key == "squeeze_entry"
                                 else {"momentum_requests", "midpoint_add_requests"})}
             for key, values in squeeze.items()}, run_id=run_id, assignment_id=assignment_id,
            state_revision=revision, session=session),
    }
    entry = squeeze.get("squeeze_entry", {})
    breakout = squeeze.get("squeeze_breakout", {})
    common = dict(run_id=run_id, assignment_id=assignment_id,
                  state_revision=revision, snapshot_session=session)
    rows["squeeze_clock"] = project_squeeze_breakout_clock(
        {key: breakout[key] for key in _CLOCK_FIELDS if key in breakout}, **common)
    rows["squeeze_progress"] = project_squeeze_progress_state(
        macd={kind: breakout[key] for key, kind in _MACD_KEYS.items() if key in breakout},
        session_targets=breakout.get("session_targets"),
        entry_progress={key: entry[key] for key in ("broken_levels", "target_multiplier",
                                                   "submitted_multiplier", "target_session_step")
                        if key in entry} if "squeeze_entry" in squeeze else None,
        run_id=run_id, assignment_id=assignment_id, state_revision=revision,
        session=session)
    evidence = {}
    for owner, keys in (("squeeze_entry", _ENTRY_LEVEL_KEYS),
                        ("squeeze_breakout", _BREAKOUT_LEVEL_KEYS)):
        source = squeeze.get(owner, {})
        for key in sorted(keys):
            if key in source:
                path = f"{owner}.{key}"
                evidence[path] = project_v7_evidence_set(
                    "level", [source[key]], source_path=path, **common)
    if "frozen_gap" in breakout:
        path = "squeeze_breakout.frozen_gap.levels"
        evidence[path] = project_v7_evidence_set(
            "level", breakout["frozen_gap"]["levels"], source_path=path, **common)
    rows["squeeze_v7_evidence"] = evidence
    if "vwap_ladder_market" in state:
        rows["grouped_resistance"] = project_grouped_resistance_state(
            state["vwap_ladder_market"], assignment_id=assignment_id,
            revision=revision, snapshot_id=snapshot_id)
    return rows


def restore_modeled_assignment_state(
    rows: Mapping[str, Any], *, run_id: str, assignment_id: str,
    revision: int, snapshot_id: str, session: str,
) -> dict[str, Any]:
    """Cold restore with cross-family identity and exact reprojection checks."""
    if not isinstance(rows, Mapping) or set(rows) != {
        "campaign", "grouped_resistance", "squeeze_purchase", "squeeze_clock",
        "squeeze_progress", "squeeze_v7_evidence",
    }:
        raise ValueError("assignment state families are incomplete or unmodeled")
    result = restore_campaign_control_state(rows["campaign"])
    result.update(restore_squeeze_purchase_state(rows["squeeze_purchase"]))
    clock = restore_squeeze_breakout_clock(rows["squeeze_clock"])
    progress = restore_squeeze_progress_state(rows["squeeze_progress"])
    if clock or progress["macd"] or progress["session_targets"] is not None:
        if "squeeze_breakout" not in result:
            raise ValueError("orphan squeeze breakout rows")
        result["squeeze_breakout"].update(clock)
    if progress["macd"]:
        breakout = result["squeeze_breakout"]
        breakout.update({key: progress["macd"][kind] for key, kind in _MACD_KEYS.items()
                         if kind in progress["macd"]})
    if progress["session_targets"] is not None:
        result["squeeze_breakout"]["session_targets"] = progress["session_targets"]
    if progress["entry_progress"] is not None:
        if "squeeze_entry" not in result:
            raise ValueError("orphan squeeze entry progress")
        result["squeeze_entry"].update(progress["entry_progress"])
    evidence_rows = rows["squeeze_v7_evidence"]
    if not isinstance(evidence_rows, Mapping):
        raise ValueError("invalid squeeze V7 evidence families")
    allowed_paths = ({f"squeeze_entry.{key}" for key in _ENTRY_LEVEL_KEYS}
                     | {f"squeeze_breakout.{key}" for key in _BREAKOUT_LEVEL_KEYS}
                     | {"squeeze_breakout.frozen_gap.levels"})
    if set(evidence_rows) - allowed_paths:
        raise ValueError("unmodeled squeeze V7 evidence path")
    for path, projected in evidence_rows.items():
        values = restore_v7_evidence_set(projected)
        owner, key = path.split(".", 1)
        if owner not in result:
            raise ValueError("orphan squeeze V7 evidence")
        if key == "frozen_gap.levels":
            result[owner]["frozen_gap"] = {"levels": values}
        elif len(values) == 1:
            result[owner][key] = values[0]
        else:
            raise ValueError("singleton V7 evidence has wrong cardinality")
    if rows["grouped_resistance"] is not None:
        result["vwap_ladder_market"] = restore_grouped_resistance_state(
            rows["grouped_resistance"])
    expected = project_modeled_assignment_state(
        result, run_id=run_id, assignment_id=assignment_id,
        revision=revision, snapshot_id=snapshot_id, session=session)
    if expected != rows:
        raise ValueError("assignment state identity or readback differs")
    return result
