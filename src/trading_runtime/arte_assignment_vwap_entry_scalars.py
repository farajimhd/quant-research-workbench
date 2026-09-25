"""Inactive closed VWAP entry scalar/funding state, excluding nested evidence/IDs."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


INITIAL = frozenset({"entry_price", "requested_at", "late", "target_moves",
                     "add_opportunities", "episode_id", "entry_kind",
                     "prior_episode_used"})
OPTIONAL = frozenset({"cross_at", "cross_price", "slice_notional",
                      "first_fill_at", "pending_target"})
ENTRY_KINDS = frozenset({"initial", "post_move_pullback", "post_move_breakout"})
TABLE = TableContract(
    "trading_assignment_vwap_entry_scalars_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"),
     ("present", "Bool"),
     ("entry_price_float", "Nullable(Float64)"),
     ("entry_price_int", "Nullable(Int64)"),
     ("requested_at", "Nullable(Float64)"), ("late", "Nullable(Bool)"),
     ("target_moves", "Nullable(UInt16)"),
     ("add_opportunities", "Nullable(UInt16)"),
     ("episode_id", "Nullable(Float64)"), ("entry_kind", "Nullable(String)"),
     ("prior_episode_used", "Nullable(Bool)"),
     ("cross_at", "Nullable(Float64)"),
     ("cross_price_float", "Nullable(Float64)"),
     ("cross_price_int", "Nullable(Int64)"),
     ("slice_notional", "Nullable(Float64)"),
     ("first_fill_at", "Nullable(Float64)"),
     ("pending_target_present", "Bool"),
     ("pending_target_price_float", "Nullable(Float64)"),
     ("pending_target_price_int", "Nullable(Int64)"),
     ("pending_target_moves", "Nullable(UInt16)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)


def _number(value: Any, label: str) -> tuple[float | None, int | None]:
    if type(value) is float and isfinite(value):
        return value, None
    if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
        return None, value
    raise ValueError(f"VWAP entry {label} is not finite Float64 or Int64")


def _float(value: Any, label: str) -> float:
    if type(value) is not float or not isfinite(value):
        raise ValueError(f"VWAP entry {label} must be finite Float64")
    return value


def _count(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError(f"VWAP entry {label} must be UInt16")
    return value


def validate_typed_entry_scalars(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate only the named scalar subset at opt-in VWAP producer sites."""
    if (not isinstance(value, Mapping) or INITIAL - set(value)
            or set(value) - INITIAL - OPTIONAL):
        raise ValueError("VWAP entry scalars have missing or unmodeled fields")
    _number(value["entry_price"], "entry_price")
    if value["entry_price"] <= 0:
        raise ValueError("VWAP entry price must be positive")
    _float(value["requested_at"], "requested_at")
    for name in ("late", "prior_episode_used"):
        if type(value[name]) is not bool:
            raise ValueError(f"VWAP entry {name} must be Bool")
    for name in ("target_moves", "add_opportunities"):
        _count(value[name], name)
    if value["episode_id"] is not None:
        _float(value["episode_id"], "episode_id")
    if type(value["entry_kind"]) is not str or value["entry_kind"] not in ENTRY_KINDS:
        raise ValueError("VWAP entry kind is unmodeled")
    for name in ("cross_at", "slice_notional", "first_fill_at"):
        if name in value:
            _float(value[name], name)
    if "cross_price" in value:
        _number(value["cross_price"], "cross_price")
    if "pending_target" in value:
        pending = value["pending_target"]
        if not isinstance(pending, Mapping) or set(pending) != {"price", "moves"}:
            raise ValueError("VWAP pending target fields differ")
        _number(pending["price"], "pending_target.price")
        _count(pending["moves"], "pending_target.moves")
        if pending["price"] <= 0:
            raise ValueError("VWAP pending target price must be positive")
    return dict(value)


def project_entry_scalars(value: Any, *, assignment_id: str, revision: int,
                          snapshot_id: str, session: str) -> dict[str, Any]:
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("VWAP entry scalar assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("VWAP entry scalar snapshot identity is invalid") from exc
    source = validate_typed_entry_scalars(value) if value is not None else {}
    entry_float, entry_int = (_number(source["entry_price"], "entry_price")
                              if source else (None, None))
    cross_float, cross_int = (_number(source["cross_price"], "cross_price")
                              if "cross_price" in source else (None, None))
    pending = source.get("pending_target")
    pending_float, pending_int = (_number(pending["price"], "pending_target.price")
                                  if pending is not None else (None, None))
    row = dict(assignment_id=assignment_id, revision=revision,
               snapshot_id=snapshot_id, session=session, present=bool(source),
               entry_price_float=entry_float, entry_price_int=entry_int,
               requested_at=source.get("requested_at"), late=source.get("late"),
               target_moves=source.get("target_moves"),
               add_opportunities=source.get("add_opportunities"),
               episode_id=source.get("episode_id"), entry_kind=source.get("entry_kind"),
               prior_episode_used=source.get("prior_episode_used"),
               cross_at=source.get("cross_at"),
               cross_price_float=cross_float, cross_price_int=cross_int,
               slice_notional=source.get("slice_notional"),
               first_fill_at=source.get("first_fill_at"),
               pending_target_present=pending is not None,
               pending_target_price_float=pending_float,
               pending_target_price_int=pending_int,
               pending_target_moves=pending.get("moves") if pending is not None else None)
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def restore_entry_scalars(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("VWAP entry scalar columns differ")
    if type(row["present"]) is not bool or type(row["pending_target_present"]) is not bool:
        raise ValueError("VWAP entry scalar presence differs")
    source = None
    if row["present"]:
        if (row["entry_price_float"] is None) == (row["entry_price_int"] is None):
            raise ValueError("VWAP entry price numeric kind differs")
        source = dict(entry_price=(row["entry_price_float"]
                                   if row["entry_price_float"] is not None
                                   else row["entry_price_int"]),
                      requested_at=row["requested_at"], late=row["late"],
                      target_moves=row["target_moves"],
                      add_opportunities=row["add_opportunities"],
                      episode_id=row["episode_id"], entry_kind=row["entry_kind"],
                      prior_episode_used=row["prior_episode_used"])
        for name in ("cross_at", "slice_notional", "first_fill_at"):
            if row[name] is not None:
                source[name] = row[name]
        if row["cross_price_float"] is not None and row["cross_price_int"] is not None:
            raise ValueError("VWAP cross-price numeric kind differs")
        if row["cross_price_float"] is not None or row["cross_price_int"] is not None:
            source["cross_price"] = (row["cross_price_float"]
                                     if row["cross_price_float"] is not None
                                     else row["cross_price_int"])
        if row["pending_target_present"]:
            if (row["pending_target_price_float"] is None) == (row["pending_target_price_int"] is None):
                raise ValueError("VWAP pending target numeric kind differs")
            source["pending_target"] = dict(
                price=(row["pending_target_price_float"]
                       if row["pending_target_price_float"] is not None
                       else row["pending_target_price_int"]),
                moves=row["pending_target_moves"])
    expected = project_entry_scalars(
        source, assignment_id=row["assignment_id"], revision=row["revision"],
        snapshot_id=row["snapshot_id"], session=row["session"])
    if expected != row:
        raise ValueError("VWAP entry scalar content or identity differs")
    return source
