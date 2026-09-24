"""Inactive normalized snapshot contract for V7 grouped-resistance state v1.

Only the `resistance_zones.observe` substate is represented. It is not a
complete assignment-state contract and is not installed in the live route.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.typed_assignment_input import validate_grouped_resistance_level
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
_BASE = (("unified_level_id", "String"), ("lower", "Float64"),
         ("upper", "Float64"), ("price", "Float64"), ("side", "Int8"),
         ("role", "String"), ("confirmed_at_ms", "Float64"),
         ("book_version", "String"), ("input_policy", "String"),
         ("seed_input_policy", "String"))
TABLES = (
    TableContract("trading_assignment_resistance_zone_snapshot_v1",
                  _KEY + (("market_price", "Float64"), ("observed_at", "Float64"),
                          ("grouping_threshold", "Float64"),
                          ("grouping_gap_samples", "UInt32"), ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, revision, snapshot_id"),
    TableContract("trading_assignment_resistance_zone_level_v1",
                  _KEY + (("family", "LowCardinality(String)"), ("level_key", "String"),
                          ("ordinal", "UInt32"))
                  + _BASE + (("encountered", "Nullable(UInt8)"),
                             ("seen_below", "Nullable(UInt8)"),
                             ("zone_grouping_threshold", "Nullable(Float64)"),
                             ("broken_at", "Nullable(Float64)"), ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, revision, snapshot_id, family, ordinal"),
    TableContract("trading_assignment_resistance_zone_member_v1",
                  _KEY + (("family", "LowCardinality(String)"), ("level_key", "String"),
                          ("ordinal", "UInt32"), ("member_id", "String"),
                          ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, revision, snapshot_id, family, level_key, ordinal"),
    TableContract("trading_assignment_resistance_zone_broken_v1",
                  _KEY + (("ordinal", "UInt32"), ("level_key", "String"),
                          ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal"),
)
_PARENT_KEYS = frozenset({"session", "known", "broken", "physical", "break_rows",
                          "price", "at", "grouping_threshold", "grouping_gap_samples"})
_GROUP_KEYS = frozenset({"members", "encountered", "seen_below", "grouping_threshold", "broken_at"})
_FAMILIES = ("physical", "known", "break_rows")


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{label} must be finite numeric")
    return float(value)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode("utf-8")).hexdigest()}


def _verify(row: dict[str, Any]) -> None:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    if _seal(content)["content_hash"] != row.get("content_hash"):
        raise ValueError("zone typed row content hash mismatch")


def project_grouped_resistance_state(
    state: Mapping[str, Any], *, assignment_id: str, revision: int, snapshot_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Project one exact zone substate; reject every unmodeled nested field."""
    if not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("zone assignment identity/revision is invalid")
    UUID(snapshot_id)
    if not isinstance(state, Mapping) or set(state) != _PARENT_KEYS:
        raise ValueError("zone state has missing or unmodeled fields")
    session = state["session"]
    if not isinstance(session, str) or len(session) != 10:
        raise ValueError("zone session is invalid")
    common = dict(assignment_id=assignment_id, revision=revision,
                  snapshot_id=snapshot_id, session=session)
    parent = {**common, "market_price": _number(state["price"], "zone price"),
              "observed_at": _number(state["at"], "zone at"),
              "grouping_threshold": _number(state["grouping_threshold"], "zone threshold"),
              "grouping_gap_samples": state["grouping_gap_samples"]}
    if type(parent["grouping_gap_samples"]) is not int or parent["grouping_gap_samples"] < 0:
        raise ValueError("zone grouping gap samples are invalid")
    levels: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for family in _FAMILIES:
        items = state[family]
        if not isinstance(items, Mapping):
            raise ValueError(f"zone {family} must be a mapping")
        for ordinal, (key, raw) in enumerate(items.items()):
            if not isinstance(key, str) or not isinstance(raw, Mapping):
                raise ValueError("zone level key/row is invalid")
            extras = set(raw) - {name for name, _ in _BASE}
            if family == "physical" and extras or family != "physical" and extras - _GROUP_KEYS:
                raise ValueError("zone level has unmodeled fields")
            base = validate_grouped_resistance_level(
                {name: raw[name] for name, _ in _BASE if name in raw}
            )
            if set(base) != {name for name, _ in _BASE} or key != base["unified_level_id"]:
                raise ValueError("zone level source columns/key are incomplete or inconsistent")
            # Canonicalize to the declared ClickHouse Float64 types before
            # hashing so an integer-valued source survives cold readback.
            for numeric in ("lower", "upper", "price", "confirmed_at_ms"):
                base[numeric] = float(base[numeric])
            group = family != "physical"
            if group and not _GROUP_KEYS.difference({"broken_at"}) <= set(raw):
                raise ValueError("zone grouped level is incomplete")
            member_ids = raw.get("members", ())
            if group and (not isinstance(member_ids, list) or not member_ids
                          or any(not isinstance(item, str) or not item for item in member_ids)):
                raise ValueError("zone members are invalid")
            if group and any(type(raw[name]) is not bool for name in ("encountered", "seen_below")):
                raise ValueError("zone flags are invalid")
            levels.append({**common, "family": family, "level_key": key,
                           "ordinal": ordinal, **base,
                           "encountered": int(raw["encountered"]) if group else None,
                           "seen_below": int(raw["seen_below"]) if group else None,
                           "zone_grouping_threshold": _number(raw["grouping_threshold"], "group threshold") if group else None,
                           "broken_at": _number(raw["broken_at"], "broken at") if "broken_at" in raw else None})
            members.extend({**common, "family": family, "level_key": key,
                            "ordinal": index, "member_id": member}
                           for index, member in enumerate(member_ids))
    broken = state["broken"]
    if not isinstance(broken, list) or len(set(broken)) != len(broken):
        raise ValueError("zone broken order is invalid")
    if any(key not in state["break_rows"] for key in broken) or set(state["break_rows"]) != set(broken):
        raise ValueError("zone broken rows differ from ordered IDs")
    return {"snapshot": [_seal(parent)], "level": [_seal(row) for row in levels],
            "member": [_seal(row) for row in members],
            "broken": [_seal({**common, "ordinal": index, "level_key": key})
                       for index, key in enumerate(broken)]}


def restore_grouped_resistance_state(rows: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Cold-read shape reconstruction, then exact reprojection validation."""
    if set(rows) != {"snapshot", "level", "member", "broken"} or len(rows["snapshot"]) != 1:
        raise ValueError("zone snapshot family is incomplete")
    for family in rows.values():
        for row in family:
            _verify(row)
    parent = rows["snapshot"][0]
    common = {key: parent[key] for key, _ in _KEY}
    state: dict[str, Any] = {
        "session": parent["session"], "known": {}, "broken": [], "physical": {},
        "break_rows": {}, "price": parent["market_price"], "at": parent["observed_at"],
        "grouping_threshold": parent["grouping_threshold"],
        "grouping_gap_samples": parent["grouping_gap_samples"],
    }
    member_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in rows["member"]:
        member_index.setdefault((item["family"], item["level_key"]), []).append(item)
    family_rank = {family: index for index, family in enumerate(_FAMILIES)}
    ordered_levels = sorted(rows["level"],
                            key=lambda item: (family_rank.get(item["family"], len(_FAMILIES)),
                                              item["ordinal"]))
    for family in _FAMILIES:
        ordinals = [item["ordinal"] for item in ordered_levels if item["family"] == family]
        if ordinals != list(range(len(ordinals))):
            raise ValueError("zone level family order is invalid")
    for item in ordered_levels:
        if any(item[key] != value for key, value in common.items()):
            raise ValueError("zone level fence differs")
        family, key = item["family"], item["level_key"]
        if family not in _FAMILIES or key in state[family]:
            raise ValueError("zone level family/key is invalid")
        row = {name: item[name] for name, _ in _BASE}
        if family != "physical":
            ordered = sorted(member_index.pop((family, key), []), key=lambda value: value["ordinal"])
            if [value["ordinal"] for value in ordered] != list(range(len(ordered))):
                raise ValueError("zone member order is invalid")
            row.update(members=[value["member_id"] for value in ordered],
                       encountered=bool(item["encountered"]), seen_below=bool(item["seen_below"]),
                       grouping_threshold=item["zone_grouping_threshold"])
            if item["broken_at"] is not None:
                row["broken_at"] = item["broken_at"]
        state[family][key] = row
    if member_index:
        raise ValueError("orphan zone members")
    ordered = sorted(rows["broken"], key=lambda item: item["ordinal"])
    if [item["ordinal"] for item in ordered] != list(range(len(ordered))):
        raise ValueError("zone broken order is invalid")
    state["broken"] = [item["level_key"] for item in ordered]
    canonical_rows = {
        "snapshot": rows["snapshot"], "level": ordered_levels,
        "member": sorted(rows["member"],
                         key=lambda item: (family_rank.get(item["family"], len(_FAMILIES)),
                                           item["level_key"], item["ordinal"])),
        "broken": ordered,
    }
    projected = project_grouped_resistance_state(
        state, assignment_id=common["assignment_id"],
        revision=common["revision"], snapshot_id=common["snapshot_id"],
    )
    projected["member"] = sorted(projected["member"],
                                 key=lambda item: (family_rank[item["family"]],
                                                   item["level_key"], item["ordinal"]))
    if projected != canonical_rows:
        raise ValueError("zone readback differs from projected typed rows")
    return state
