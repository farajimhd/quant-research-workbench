"""Typed checkpoint of broker states actually observed by Strategy 1 OMS.

The broker match snapshot may be ahead of OMS at a completed boundary. Never
seed OMS deduplication from broker state: doing so can suppress a pending
transition. These normalized rows preserve the OMS-observed canonical order
fingerprints. They are inert until a commit-last publisher and cold attestation
wire them into the running checkpoint contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


ROOT = TableContract(
    "trading_strategy_one_oms_observation_snapshot_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("session_date", "Date"),
     ("checkpoint_sequence", "UInt64"), ("boundary_ms", "UInt32"),
     ("observation_count", "UInt32"),
     ("observation_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)", "run_id, checkpoint_sequence, snapshot_id",
)
OBSERVATION = TableContract(
    "trading_strategy_one_oms_observation_v1",
    (("snapshot_id", "UUID"), ("run_id", "String"),
     ("snapshot_month", "Date"), ("checkpoint_sequence", "UInt64"),
     ("group_id", "String"), ("broker_order_id", "String"),
     ("lifecycle_state", "String"), ("broker_status", "String"),
     ("filled_quantity", "Decimal(38, 18)"),
     ("remaining_quantity", "Decimal(38, 18)"),
     ("average_fill_price", "Decimal(38, 18)"),
     ("limit_price", "Decimal(38, 18)"),
     ("stop_price", "Decimal(38, 18)"),
     ("warning", "String"), ("rejection_code", "String"),
     ("rejection_reason", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_month)",
    "run_id, checkpoint_sequence, group_id, broker_order_id",
)
TABLES = (ROOT, OBSERVATION)


@dataclass(frozen=True, slots=True)
class OmsObservationSnapshotRows:
    root: Mapping[str, Any]
    observations: tuple[Mapping[str, Any], ...]


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sealed(value: Mapping[str, Any]) -> dict[str, Any]:
    content = dict(value)
    return {**content, "content_hash": _digest(content)}


def _number(value: Any) -> str:
    if type(value) not in (int, float, Decimal) or (
            type(value) is float and not isfinite(value)):
        raise ValueError("OMS observation has a nonfinite numeric field")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) >= Decimal(10) ** 20:
            raise ValueError("OMS observation exceeds its decimal contract")
        with localcontext() as context:
            context.prec = 38
            return str(number.quantize(Decimal("0.000000000000000001")))
    except InvalidOperation as exc:
        raise ValueError("OMS observation exceeds its decimal scale") from exc


def project_oms_observation_snapshot(*, run_id: str, session_date: date,
                                     checkpoint_sequence: int,
                                     boundary_ms: int,
                                     groups: Mapping[str, Any]
                                     ) -> OmsObservationSnapshotRows:
    """Freeze only canonical OMS-observed states, never current broker states."""
    if (not run_id or not isinstance(session_date, date)
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1
            or type(boundary_ms) is not int or not 0 <= boundary_ms <= 57_600_000
            or boundary_ms % 100 or not isinstance(groups, Mapping)):
        raise ValueError("OMS observation snapshot needs one completed boundary")
    snapshot_id = str(uuid5(NAMESPACE_URL,
                            f"{run_id}:{checkpoint_sequence}:oms-observation-v1"))
    common = {"snapshot_id": snapshot_id, "run_id": run_id,
              "snapshot_month": session_date.replace(day=1).isoformat(),
              "checkpoint_sequence": checkpoint_sequence}
    observations: list[dict[str, Any]] = []
    seen_orders: set[str] = set()
    for group_id, group in sorted(groups.items()):
        broker_ids = tuple(group.broker_order_ids)
        observed = group.broker_order_state_fingerprints
        if (not isinstance(group_id, str) or not group_id
                or len(set(broker_ids)) != len(broker_ids)
                or set(observed) - set(broker_ids)):
            raise ValueError("OMS observation has an unbound broker order")
        for broker_order_id, fingerprint in sorted(observed.items()):
            if (not isinstance(broker_order_id, str) or not broker_order_id
                    or broker_order_id in seen_orders
                    or not isinstance(fingerprint, tuple)
                    or len(fingerprint) != 10
                    or type(fingerprint[0]) is not str or not fingerprint[0]
                    or any(type(fingerprint[index]) is not str
                           for index in (1, 7, 8, 9))):
                raise ValueError("OMS observation differs from its canonical order")
            seen_orders.add(broker_order_id)
            observations.append(_sealed({
                **common, "group_id": group_id,
                "broker_order_id": broker_order_id,
                "lifecycle_state": fingerprint[0],
                "broker_status": fingerprint[1],
                "filled_quantity": _number(fingerprint[2]),
                "remaining_quantity": _number(fingerprint[3]),
                "average_fill_price": _number(fingerprint[4]),
                "limit_price": _number(fingerprint[5]),
                "stop_price": _number(fingerprint[6]),
                "warning": fingerprint[7],
                "rejection_code": fingerprint[8],
                "rejection_reason": fingerprint[9],
            }))
    ordered = tuple(sorted(observations,
                           key=lambda row: (row["group_id"], row["broker_order_id"])))
    members = tuple((row["group_id"], row["broker_order_id"], row["content_hash"])
                    for row in ordered)
    root = _sealed({**common, "session_date": session_date.isoformat(),
                    "boundary_ms": boundary_ms,
                    "observation_count": len(ordered),
                    "observation_hash": _digest(members)})
    return OmsObservationSnapshotRows(root, ordered)


def verify_oms_observation_snapshot(
    rows: OmsObservationSnapshotRows,
) -> OmsObservationSnapshotRows:
    """Verify identity, row hashes and completeness before any actor install."""
    if not isinstance(rows, OmsObservationSnapshotRows):
        raise TypeError("OMS observation snapshot has no typed rows")
    root = rows.root
    if (set(root) != {name for name, _ in ROOT.columns}
            or _digest({key: value for key, value in root.items()
                        if key != "content_hash"}) != root.get("content_hash")):
        raise RuntimeError("OMS observation root hash differs")
    members = []
    seen = set()
    for row in rows.observations:
        key = (row.get("group_id"), row.get("broker_order_id"))
        if (set(row) != {name for name, _ in OBSERVATION.columns}
                or key in seen or not all(key)
                or any(row.get(field) != root.get(field) for field in (
                    "snapshot_id", "run_id", "snapshot_month",
                    "checkpoint_sequence"))
                or _digest({name: value for name, value in row.items()
                            if name != "content_hash"}) != row.get("content_hash")):
            raise RuntimeError("OMS observation child differs from its root")
        seen.add(key)
        members.append((*key, row["content_hash"]))
    if (len(rows.observations) != int(root["observation_count"])
            or _digest(tuple(sorted(members))) != root["observation_hash"]):
        raise RuntimeError("OMS observation set differs from its root")
    return rows
