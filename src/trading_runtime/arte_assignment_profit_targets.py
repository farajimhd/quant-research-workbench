"""Inactive ordered typed structural profit-target prices.

This models only the top-level target-price list, not the dynamic V7 frontier,
ratchet acceptance evidence, or order-intent state. Integer and Float64 source
values remain distinct without a JSON or generic value column.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
MANIFEST_TABLE = TableContract(
    "trading_assignment_profit_target_manifest_v1",
    _KEY + (("present", "Bool"), ("row_count", "UInt16"),
            ("row_set_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
PRICE_TABLE = TableContract(
    "trading_assignment_profit_target_price_v1",
    _KEY + (("ordinal", "UInt16"), ("price_float", "Nullable(Float64)"),
            ("price_int", "Nullable(Int64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
TABLES = (MANIFEST_TABLE, PRICE_TABLE)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _identity(assignment_id: str, revision: int, snapshot_id: str,
              session: str) -> dict[str, Any]:
    if (type(assignment_id) is not str or not assignment_id
            or type(revision) is not int or revision < 1):
        raise ValueError("profit-target identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("profit-target snapshot is invalid") from exc
    return dict(assignment_id=assignment_id, revision=revision,
                snapshot_id=snapshot_id, session=session)


def project_profit_targets(values: Any, *, present: bool, assignment_id: str,
                           revision: int, snapshot_id: str,
                           session: str) -> dict[str, Any]:
    identity = _identity(assignment_id, revision, snapshot_id, session)
    if type(present) is not bool or (not present and values is not None):
        raise ValueError("profit-target presence differs")
    if present and (type(values) is not list or len(values) > 256):
        raise ValueError("profit targets must be a bounded list")
    prices = [] if values is None else values
    rows = []
    for ordinal, price in enumerate(prices):
        if type(price) is float and isfinite(price) and price > 0:
            value = dict(price_float=price, price_int=None)
        elif type(price) is int and 0 < price < 2 ** 63:
            value = dict(price_float=None, price_int=price)
        else:
            raise ValueError("profit target price must be positive finite Float64 or Int64")
        rows.append(_seal({**identity, "ordinal": ordinal, **value}))
    manifest = _seal({**identity, "present": present, "row_count": len(rows),
                      "row_set_hash": _hash(rows)})
    return dict(manifest=manifest, prices=rows)


def restore_profit_targets(rows: Mapping[str, Any]) -> tuple[bool, list[float | int]]:
    if not isinstance(rows, Mapping) or set(rows) != {"manifest", "prices"}:
        raise ValueError("profit-target rows are incomplete")
    manifest, prices = rows["manifest"], rows["prices"]
    if (not isinstance(manifest, Mapping) or
            set(manifest) != {name for name, _ in MANIFEST_TABLE.columns}
            or not isinstance(prices, list) or
            any(not isinstance(row, Mapping) or set(row) != {
                name for name, _ in PRICE_TABLE.columns} for row in prices)):
        raise ValueError("profit-target row columns differ")
    if (type(manifest["present"]) is not bool or
            (not manifest["present"] and prices) or
            manifest["row_count"] != len(prices) or
            manifest["row_set_hash"] != _hash(prices) or
            [row["ordinal"] for row in prices] != list(range(len(prices)))):
        raise ValueError("profit-target row set or ordering differs")
    values = []
    for row in prices:
        if (row["price_float"] is None) == (row["price_int"] is None):
            raise ValueError("profit target numeric kind is ambiguous")
        values.append(row["price_float"] if row["price_float"] is not None
                      else row["price_int"])
    expected = project_profit_targets(
        values if manifest["present"] else None, present=manifest["present"],
        assignment_id=manifest["assignment_id"], revision=manifest["revision"],
        snapshot_id=manifest["snapshot_id"], session=manifest["session"])
    if expected != rows:
        raise ValueError("profit-target content differs")
    return manifest["present"], values
