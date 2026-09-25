"""Inactive exact composition of the complete current VWAP ladder entry state.

The retest-selected swing is stored only with its retest witness, not twice.
This does not activate typed persistence or certify the rest of assignment state.
"""
from __future__ import annotations

from typing import Any, Mapping

from src.trading_runtime.arte_assignment_vwap_entry_scalars import (
    INITIAL, OPTIONAL, project_entry_scalars, restore_entry_scalars,
)
from src.trading_runtime.arte_assignment_vwap_entry_ids import (
    project_entry_id_lists, restore_entry_id_lists,
)
from src.trading_runtime.arte_assignment_vwap_swing import (
    project_entry_swing, restore_entry_swing,
)
from src.trading_runtime.arte_assignment_vwap_retest_anchor import (
    project_entry_retest_anchor, restore_entry_retest_anchor,
)
from src.trading_runtime.arte_assignment_vwap_entry_breakout import (
    project_entry_breakout_setup, restore_entry_breakout_setup,
)
from src.trading_runtime.arte_assignment_vwap_pullback_move import (
    project_entry_pullback_move, restore_entry_pullback_move,
)


_NESTED = frozenset({"swing", "retest_anchor", "breakout_setup", "pullback_move"})
_IDS = frozenset({"broken", "cross_known"})
_REQUIRED = INITIAL | _IDS | _NESTED
_ALLOWED = _REQUIRED | OPTIONAL
ROW_KEYS = frozenset({
    "scalars", "ids_manifest", "broken", "cross_known", "swing", "swing_bounce",
    "retest", "retest_members", "retest_swing", "retest_bounce",
    "breakout_setup", "breakout_parent", "breakout_members", "pullback",
})


def _validate_linkage(value: Mapping[str, Any]) -> None:
    kind = value["entry_kind"]
    swing = value["swing"]
    retest = value["retest_anchor"]
    breakout = value["breakout_setup"]
    pullback = value["pullback_move"]
    if retest is not None and swing != retest.get("swing"):
        raise ValueError("VWAP entry retest and top-level swing differ")
    if kind == "post_move_breakout":
        if breakout is None or any(item is not None for item in (swing, retest, pullback)):
            raise ValueError("VWAP breakout entry evidence differs")
    elif kind == "post_move_pullback":
        if retest is None or swing is None or breakout is not None:
            raise ValueError("VWAP pullback entry evidence differs")
    elif kind == "initial":
        if swing is None or breakout is not None or pullback is not None:
            raise ValueError("VWAP initial entry evidence differs")


def project_vwap_entry(value: Mapping[str, Any], *, assignment_id: str,
                       revision: int, snapshot_id: str,
                       session: str) -> dict[str, Any]:
    if (not isinstance(value, Mapping) or _REQUIRED - set(value)
            or set(value) - _ALLOWED):
        raise ValueError("VWAP entry has missing or unmodeled fields")
    _validate_linkage(value)
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session)
    scalars = project_entry_scalars(
        {key: value[key] for key in INITIAL | OPTIONAL if key in value}, **identity)
    ids = project_entry_id_lists({key: value[key] for key in _IDS}, **identity)
    swing = (project_entry_swing(value["swing"], **identity)
             if value["swing"] is not None and value["retest_anchor"] is None else None)
    retest = (project_entry_retest_anchor(value["retest_anchor"], **identity)
              if value["retest_anchor"] is not None else None)
    breakout = (project_entry_breakout_setup(value["breakout_setup"], **identity)
                if value["breakout_setup"] is not None else None)
    pullback = (project_entry_pullback_move(value["pullback_move"], **identity)
                if value["pullback_move"] is not None else None)
    return {
        "scalars": scalars, "ids_manifest": ids["manifest"],
        "broken": ids["broken"], "cross_known": ids["cross_known"],
        "swing": swing["swing"] if swing else None,
        "swing_bounce": swing["bounce"] if swing else None,
        "retest": retest["retest"] if retest else None,
        "retest_members": retest["members"] if retest else [],
        "retest_swing": retest["swing"] if retest else None,
        "retest_bounce": retest["bounce"] if retest else None,
        "breakout_setup": breakout["setup"] if breakout else None,
        "breakout_parent": breakout["parent"] if breakout else [],
        "breakout_members": breakout["members"] if breakout else [],
        "pullback": pullback,
    }


def restore_vwap_entry(rows: Mapping[str, Any], *, assignment_id: str,
                       revision: int, snapshot_id: str,
                       session: str) -> dict[str, Any]:
    if not isinstance(rows, Mapping) or set(rows) != ROW_KEYS:
        raise ValueError("VWAP entry row families are incomplete or unmodeled")
    scalars = restore_entry_scalars(rows["scalars"])
    if scalars is None:
        raise ValueError("VWAP entry scalar parent is absent")
    ids = restore_entry_id_lists({"manifest": rows["ids_manifest"],
                                  "broken": rows["broken"],
                                  "cross_known": rows["cross_known"]})
    if set(ids) != _IDS:
        raise ValueError("VWAP entry ordered ID families are incomplete")
    retest = None
    if rows["retest"] is not None:
        retest = restore_entry_retest_anchor({
            "retest": rows["retest"], "members": rows["retest_members"],
            "swing": rows["retest_swing"], "bounce": rows["retest_bounce"]})
    elif (rows["retest_members"] or rows["retest_swing"] is not None
          or rows["retest_bounce"] is not None):
        raise ValueError("orphan VWAP retest child rows")
    if retest is not None:
        if rows["swing"] is not None or rows["swing_bounce"] is not None:
            raise ValueError("VWAP retest swing was redundantly published")
        swing = retest["swing"]
    elif rows["swing"] is not None:
        swing = restore_entry_swing({"swing": rows["swing"],
                                     "bounce": rows["swing_bounce"]})
    elif rows["swing_bounce"] is not None:
        raise ValueError("orphan VWAP swing bounce row")
    else:
        swing = None
    breakout = None
    if rows["breakout_setup"] is not None:
        breakout = restore_entry_breakout_setup({
            "setup": rows["breakout_setup"],
            "parent": rows["breakout_parent"],
            "members": rows["breakout_members"]})
        if breakout is None:
            raise ValueError("VWAP breakout setup manifest was empty")
    elif rows["breakout_parent"] or rows["breakout_members"]:
        raise ValueError("orphan VWAP breakout child rows")
    pullback = (restore_entry_pullback_move(rows["pullback"])
                if rows["pullback"] is not None else None)
    if pullback is None and rows["pullback"] is not None:
        raise ValueError("VWAP pullback row was marked absent")
    value = {**scalars, **ids, "swing": swing, "retest_anchor": retest,
             "breakout_setup": breakout, "pullback_move": pullback}
    expected = project_vwap_entry(
        value, assignment_id=assignment_id, revision=revision,
        snapshot_id=snapshot_id, session=session)
    if expected != rows:
        raise ValueError("VWAP entry identity or exact readback differs")
    return value
