"""Normalized, immutable Strategy 1 protection checkpoints.

These draft rows are an app-owned recovery product, not market data. A future
publication worker must write children, states, then the snapshot seal under a
journal/Keeper fence; the strategy and Backtest market reader never INSERT.
No JSON or opaque state column is part of the ClickHouse contract. This module
does not install tables or open the fixed-run launch gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Mapping
from uuid import NAMESPACE_URL, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.strategy_one_position import (
    AcceptedResistance, ProtectionState,
)


TABLES = (
    TableContract(
        "trading_strategy_one_protection_snapshot_v1",
        (("snapshot_id", "UUID"), ("run_id", "String"),
         ("snapshot_month", "Date"), ("session_date", "Date"),
         ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
         ("position_count", "UInt32"), ("resistance_count", "UInt32"),
         ("states_hash", "FixedString(64)"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
    ),
    TableContract(
        "trading_strategy_one_protection_state_v1",
        (("state_id", "UUID"), ("snapshot_id", "UUID"),
         ("run_id", "String"), ("snapshot_month", "Date"),
         ("checkpoint_sequence", "UInt64"), ("account_id", "String"),
         ("ticker", "LowCardinality(String)"), ("position_id", "String"),
         ("boundary_ms", "UInt32"), ("stop", "Decimal(38, 18)"),
         ("target", "Decimal(38, 18)"), ("accepted_count", "UInt32"),
         ("earned_groups", "UInt32"), ("applied_groups", "UInt32"),
         ("pending_count", "UInt8"), ("state_hash", "FixedString(64)")),
        "toYYYYMM(snapshot_month)",
        "run_id, checkpoint_sequence, account_id, ticker, position_id",
    ),
    TableContract(
        "trading_strategy_one_protection_resistance_v1",
        (("state_id", "UUID"), ("snapshot_id", "UUID"),
         ("run_id", "String"), ("snapshot_month", "Date"),
         ("checkpoint_sequence", "UInt64"), ("account_id", "String"),
         ("ticker", "LowCardinality(String)"), ("position_id", "String"),
         ("ordinal", "UInt32"), ("unified_level_id", "String"),
         ("group_role", "LowCardinality(String)"),
         ("lower", "Nullable(Decimal(38, 18))"),
         ("upper", "Nullable(Decimal(38, 18))")),
        "toYYYYMM(snapshot_month)",
        "run_id, checkpoint_sequence, account_id, ticker, position_id, ordinal",
    ),
)


@dataclass(frozen=True, slots=True)
class ProtectionSnapshotRows:
    snapshot: dict
    states: tuple[dict, ...]
    resistances: tuple[dict, ...]


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")).hexdigest()


def _price(value: object) -> str:
    try:
        decimal = Decimal(str(value))
        exact = decimal.quantize(Decimal("0.000000000000000001"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Strategy 1 checkpoint price is not Decimal(38,18)") from exc
    if (not decimal.is_finite() or decimal <= 0 or decimal != exact
            or len(exact.as_tuple().digits) > 38):
        raise ValueError("Strategy 1 checkpoint price is not a positive exact Decimal(38,18)")
    return format(exact, "f")


def _validate_state(state: ProtectionState, *, boundary_ms: int) -> None:
    if (not isinstance(state, ProtectionState)
            or type(state.boundary_ms) is not int
            or not 0 < state.boundary_ms <= boundary_ms
            or state.boundary_ms % 100
            or not 0 < state.stop < state.target
            or not isinstance(state.accepted_ids, frozenset)
            or not isinstance(state.pending_group, tuple)
            or not isinstance(state.earned_group, tuple)
            or type(state.earned_groups) is not int
            or type(state.applied_groups) is not int
            or not 0 <= state.applied_groups <= state.earned_groups
            or len(state.pending_group) >= 3
            or len(state.earned_group) != (3 if state.earned_groups else 0)
            or len(state.accepted_ids) !=
            3 * state.earned_groups + len(state.pending_group)
            or any(not isinstance(identity, str) or not identity
                   for identity in state.accepted_ids)):
        raise ValueError("Strategy 1 protection checkpoint state is inconsistent")
    latest = (*state.earned_group, *state.pending_group)
    ids = [row.unified_level_id for row in latest]
    if (any(not isinstance(row, AcceptedResistance) or not row.unified_level_id
            for row in latest) or len(ids) != len(set(ids))
            or not set(ids) <= state.accepted_ids):
        raise ValueError("Strategy 1 protection checkpoint level identities differ")
    _price(state.stop)
    _price(state.target)
    for row in latest:
        if not 0 < row.lower <= row.upper:
            raise ValueError("Strategy 1 protection checkpoint geometry is invalid")
        _price(row.lower)
        _price(row.upper)


def project_protection_snapshot(*, run_id: str, session_date: date,
                                checkpoint_sequence: int, boundary_ms: int,
                                positions: Mapping[tuple[str, str, str], ProtectionState],
                                ) -> ProtectionSnapshotRows:
    """Encode all active positions at one committed market boundary.

    An empty snapshot is meaningful: its seal proves there were no active
    Strategy 1 positions. Each key is (account_id, ticker, position_id).
    """
    if (not run_id or not isinstance(session_date, date)
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or type(boundary_ms) is not int or not 0 < boundary_ms <= 57_600_000
            or boundary_ms % 100 or not isinstance(positions, Mapping)):
        raise ValueError("Strategy 1 snapshot needs a pinned completed cursor")
    snapshot_id = str(uuid5(NAMESPACE_URL,
        f"strategy-one-protection-v1:{run_id}:{checkpoint_sequence}"))
    month = session_date.replace(day=1).isoformat()
    states = []
    resistances = []
    for identity, state in sorted(positions.items()):
        if (not isinstance(identity, tuple) or len(identity) != 3
                or any(not isinstance(part, str) or not part for part in identity)):
            raise ValueError("Strategy 1 position identity is incomplete")
        account_id, ticker, position_id = identity
        _validate_state(state, boundary_ms=boundary_ms)
        state_id = str(uuid5(NAMESPACE_URL,
            f"{snapshot_id}:{account_id}:{ticker}:{position_id}"))
        common = dict(state_id=state_id, snapshot_id=snapshot_id,
                      run_id=run_id, snapshot_month=month,
                      checkpoint_sequence=checkpoint_sequence,
                      account_id=account_id, ticker=ticker,
                      position_id=position_id)
        latest_ids = {row.unified_level_id for row in
                      (*state.earned_group, *state.pending_group)}
        children = []
        for level_id in sorted(state.accepted_ids - latest_ids):
            children.append((level_id, "prior", None, None))
        for row in state.earned_group:
            children.append((row.unified_level_id, "earned",
                             _price(row.lower), _price(row.upper)))
        for row in state.pending_group:
            children.append((row.unified_level_id, "pending",
                             _price(row.lower), _price(row.upper)))
        child_rows = tuple({**common, "ordinal": ordinal,
                            "unified_level_id": level_id,
                            "group_role": role, "lower": lower, "upper": upper}
                           for ordinal, (level_id, role, lower, upper)
                           in enumerate(children))
        payload = {**common, "boundary_ms": state.boundary_ms,
                   "stop": _price(state.stop), "target": _price(state.target),
                   "accepted_count": len(state.accepted_ids),
                   "earned_groups": state.earned_groups,
                   "applied_groups": state.applied_groups,
                   "pending_count": len(state.pending_group)}
        states.append({**payload, "state_hash": _digest(
            [payload, child_rows])})
        resistances.extend(child_rows)
    snapshot = dict(snapshot_id=snapshot_id, run_id=run_id,
                    snapshot_month=month, session_date=session_date.isoformat(),
                    checkpoint_sequence=checkpoint_sequence,
                    boundary_ms=boundary_ms, position_count=len(states),
                    resistance_count=len(resistances),
                    states_hash=_digest([row["state_hash"] for row in states]))
    snapshot["content_hash"] = _digest(snapshot)
    return ProtectionSnapshotRows(snapshot, tuple(states), tuple(resistances))


def restore_protection_snapshot(rows: ProtectionSnapshotRows,
                                ) -> dict[tuple[str, str, str], ProtectionState]:
    """Cold-verify the seal and every typed child before reconstructing state."""
    if not isinstance(rows, ProtectionSnapshotRows):
        raise ValueError("Strategy 1 recovery needs typed snapshot rows")
    seal = rows.snapshot
    if (seal.get("content_hash") != _digest({key: value for key, value in
                                            seal.items() if key != "content_hash"})
            or seal.get("snapshot_month") !=
            date.fromisoformat(str(seal.get("session_date"))).replace(day=1).isoformat()
            or seal.get("snapshot_id") != str(uuid5(
                NAMESPACE_URL, f"strategy-one-protection-v1:{seal.get('run_id')}:"
                               f"{seal.get('checkpoint_sequence')}"))):
        raise ValueError("Strategy 1 protection snapshot identity differs")
    states = tuple(sorted(rows.states, key=lambda row: (
        row["account_id"], row["ticker"], row["position_id"])))
    if (len(rows.states) != seal.get("position_count")
            or len(rows.resistances) != seal.get("resistance_count")
            or _digest([row.get("state_hash") for row in states])
            != seal.get("states_hash")):
        raise ValueError("Strategy 1 protection snapshot seal differs")
    restored = {}
    children_by_state: dict[str, list[dict]] = {}
    for child in rows.resistances:
        children_by_state.setdefault(child["state_id"], []).append(child)
    for row in states:
        identity = (row["account_id"], row["ticker"], row["position_id"])
        if identity in restored or row["snapshot_id"] != seal["snapshot_id"]:
            raise ValueError("Strategy 1 protection state identity differs")
        children = tuple(sorted(children_by_state.get(row["state_id"], ()),
                                key=lambda child: child["ordinal"]))
        payload = {key: value for key, value in row.items() if key != "state_hash"}
        if (_digest([payload, children]) != row["state_hash"]
                or [child["ordinal"] for child in children] != list(range(len(children)))
                or len(children) != row["accepted_count"]
                or row["run_id"] != seal["run_id"]
                or row["snapshot_month"] != seal["snapshot_month"]
                or row["checkpoint_sequence"] != seal["checkpoint_sequence"]
                or row["state_id"] != str(uuid5(NAMESPACE_URL,
                    f"{seal['snapshot_id']}:{identity[0]}:{identity[1]}:{identity[2]}"))):
            raise ValueError("Strategy 1 protection state or children differ")
        if any(child["snapshot_id"] != seal["snapshot_id"]
               or child["run_id"] != seal["run_id"]
               or child["checkpoint_sequence"] != seal["checkpoint_sequence"]
               or (child["account_id"], child["ticker"], child["position_id"])
               != identity for child in children):
            raise ValueError("Strategy 1 protection resistance scope differs")
        earned = tuple(AcceptedResistance(child["unified_level_id"],
                      float(child["lower"]), float(child["upper"]))
                       for child in children if child["group_role"] == "earned")
        pending = tuple(AcceptedResistance(child["unified_level_id"],
                       float(child["lower"]), float(child["upper"]))
                        for child in children if child["group_role"] == "pending")
        if any(child["group_role"] not in {"prior", "earned", "pending"}
               or (child["lower"] is None) != (child["group_role"] == "prior")
               or (child["upper"] is None) != (child["group_role"] == "prior")
               for child in children):
            raise ValueError("Strategy 1 protection resistance role differs")
        roles = [child["group_role"] for child in children]
        if roles != sorted(roles, key={"prior": 0, "earned": 1,
                                      "pending": 2}.get):
            raise ValueError("Strategy 1 protection resistance group order differs")
        state = ProtectionState(int(row["boundary_ms"]), float(row["stop"]),
                                float(row["target"]),
                                frozenset(child["unified_level_id"] for child in children),
                                pending, earned, int(row["earned_groups"]),
                                int(row["applied_groups"]))
        _validate_state(state, boundary_ms=int(seal["boundary_ms"]))
        restored[identity] = state
    if set(children_by_state) - {
            row["state_id"] for row in rows.states}:
        raise ValueError("Strategy 1 protection has an orphan resistance")
    return restored
