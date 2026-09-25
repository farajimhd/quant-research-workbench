"""Closed, staged fixed-Backtest terminal snapshot scalar projection.

No persistence or runtime wiring lives here. Float64 is the source's numeric
contract: finite Python floats are written as Float64 and read back bit-exact.
"""
from __future__ import annotations

from hashlib import sha256
import math
import struct
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.journal_contract import JournalRecord


ACCOUNT_METRICS = (
    ("netliquidation", "net_liquidation"),
    ("totalcashvalue", "total_cash_value"),
    ("buyingpower", "buying_power"),
    ("grosspositionvalue", "gross_position_value"),
    ("availablefunds", "available_funds"),
    ("excessliquidity", "excess_liquidity"),
)
POSITION_FIELDS = (
    ("position", "quantity"), ("mktPrice", "market_price"),
    ("mktValue", "market_value"), ("avgCost", "average_cost"),
    ("avgPrice", "average_price"), ("realizedPnl", "realized_pnl"),
    ("unrealizedPnl", "unrealized_pnl"),
)


def finite_float64(value: Any) -> float:
    """Reject coercions and non-finite source evidence; preserve signed zero."""
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("Terminal snapshot numeric evidence must be finite Float64")
    return value


def assert_float64_readback(expected: float, observed: Any) -> None:
    """Guard the exact binary64 bits after a future client readback."""
    finite_float64(expected)
    finite_float64(observed)
    if struct.pack(">d", expected) != struct.pack(">d", observed):
        raise ValueError("Terminal snapshot Float64 readback changed source bits")


def project_account_scalars(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != {key for key, _ in ACCOUNT_METRICS}:
        raise ValueError("Terminal account summary has missing or extra fields")
    currency: str | None = None
    timestamp: int | None = None
    result: dict[str, Any] = {}
    for source, column in ACCOUNT_METRICS:
        item = payload[source]
        if not isinstance(item, Mapping) or set(item) != {"amount", "currency", "timestamp"}:
            raise ValueError("Terminal account metric has missing or extra fields")
        if (not isinstance(item["currency"], str) or not item["currency"]
                or type(item["timestamp"]) is not int or item["timestamp"] < 0):
            raise ValueError("Terminal account currency or timestamp is invalid")
        if currency is not None and (currency != item["currency"] or timestamp != item["timestamp"]):
            raise ValueError("Terminal account metrics disagree on source identity")
        currency, timestamp = item["currency"], item["timestamp"]
        result[column] = finite_float64(item["amount"])
    return {"currency": currency, "source_timestamp_ms": timestamp, **result}


def project_position_scalars(payload: Mapping[str, Any], *, account_id: str) -> dict[str, Any]:
    names = {"acctId", "conid", "contractDesc", "currency", "assetClass"} | {
        source for source, _ in POSITION_FIELDS}
    if (not isinstance(payload, Mapping) or set(payload) != names
            or payload["acctId"] != account_id
            or type(payload["conid"]) is not int or not 0 <= payload["conid"] < 2**64
            or any(not isinstance(payload[key], str) or not payload[key]
                   for key in ("contractDesc", "currency", "assetClass"))):
        raise ValueError("Terminal position has missing, extra, or conflicting fields")
    return {
        "conid": payload["conid"], "ticker": payload["contractDesc"],
        "currency": payload["currency"], "asset_class": payload["assetClass"],
        **{column: finite_float64(payload[source]) for source, column in POSITION_FIELDS},
    }


def position_set_sha256(rows: tuple[Mapping[str, Any], ...]) -> str:
    """Hash the full normalized population, independent of input row order."""
    if len(rows) > 2**32 - 1:
        raise ValueError("Terminal position population exceeds UInt32")
    expected = {"conid", "ticker", "currency", "asset_class"} | {
        column for _, column in POSITION_FIELDS}
    if any(set(row) != expected for row in rows):
        raise ValueError("Terminal position hash needs exact normalized scalar rows")
    ordered = sorted(rows, key=lambda row: (row["conid"], row["ticker"]))
    if len({row["conid"] for row in ordered}) != len(ordered):
        raise ValueError("Terminal position population repeats a conid")
    digest = sha256(b"backtest-position-set-v2\0" + struct.pack(">I", len(rows)))
    for row in ordered:
        conid = row["conid"]
        if type(conid) is not int or not 0 <= conid < 2**64:
            raise ValueError("Terminal position conid is invalid")
        digest.update(struct.pack(">Q", conid))
        for name in ("ticker", "currency", "asset_class"):
            value = row[name]
            if not isinstance(value, str) or not value:
                raise ValueError("Terminal position text is invalid")
            encoded = value.encode("utf-8")
            digest.update(struct.pack(">I", len(encoded)))
            digest.update(encoded)
        for _, name in POSITION_FIELDS:
            digest.update(struct.pack(">d", finite_float64(row[name])))
    return digest.hexdigest()


def project_snapshot_group(
    account: JournalRecord, positions: tuple[JournalRecord, ...], *, batch_id: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Project one complete emitted account block; reject incomplete prefixes."""
    snapshot_id = str(UUID(str(account.payload.get("snapshot_id"))))
    if ((account.category, account.entity_type) != ("snapshot", "portfolio")
            or not account.account_id or account.entity_id != account.account_id
            or type(account.payload.get("expected_position_count")) is not int
            or account.payload["expected_position_count"] != len(positions)
            or not 0 <= len(positions) < 2**32
            or any(row.sequence != account.sequence + ordinal + 1
                   or row.run_id != account.run_id
                   or row.account_id != account.account_id
                   or row.event_time != account.event_time
                   or (row.category, row.entity_type) != ("snapshot", "position")
                   or row.payload.get("parent_snapshot_id") != snapshot_id
                   or row.payload.get("ordinal") != ordinal
                   for ordinal, row in enumerate(positions))):
        raise ValueError("Terminal snapshot group is incomplete or noncausal")
    expected_account = {key for key, _ in ACCOUNT_METRICS} | {
        "snapshot_id", "expected_position_count", "position_set_sha256",
    }
    account_payload = {key: value for key, value in account.payload.items()
                       if key not in {"correlation_id", "causation_id"}}
    if set(account_payload) != expected_account:
        raise ValueError("Terminal account snapshot has unmodeled fields")
    normalized = []
    for ordinal, row in enumerate(positions):
        payload = {key: value for key, value in row.payload.items()
                   if key not in {"correlation_id", "causation_id"}}
        core = {key: value for key, value in payload.items()
                if key not in {"parent_snapshot_id", "ordinal"}}
        projected = project_position_scalars(core, account_id=account.account_id)
        if row.entity_id != str(projected["conid"]):
            raise ValueError("Terminal position record identity differs from conid")
        normalized.append(projected)
    scalar_rows = tuple(normalized)
    digest = position_set_sha256(scalar_rows)
    if account.payload["position_set_sha256"] != digest:
        raise ValueError("Terminal snapshot full-position hash differs")
    month = account.event_time.strftime("%Y-%m-01")
    parent = {
        "record_id": account.record_id, "run_id": account.run_id,
        "event_month": month, "batch_id": batch_id,
        "snapshot_id": snapshot_id, "account_id": account.account_id,
        **project_account_scalars({key: account.payload[key] for key, _ in ACCOUNT_METRICS}),
        "expected_position_count": len(positions),
        "position_set_sha256": digest,
    }
    children = tuple({
        "record_id": row.record_id, "parent_snapshot_id": snapshot_id,
        "run_id": row.run_id, "event_month": month, "batch_id": batch_id,
        "account_id": row.account_id, "ordinal": ordinal, **scalar_rows[ordinal],
    } for ordinal, row in enumerate(positions))
    return parent, children


def recover_snapshot_group(
    parent: Mapping[str, Any], children: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Rebuild the exact CPAPI source fields from a verified V2 group."""
    if (type(parent.get("expected_position_count")) is not int
            or parent["expected_position_count"] != len(children)
            or sorted(row.get("ordinal") for row in children) != list(range(len(children)))
            or any(row.get("parent_snapshot_id") != parent.get("snapshot_id")
                   or row.get("account_id") != parent.get("account_id")
                   or row.get("run_id") != parent.get("run_id")
                   for row in children)):
        raise ValueError("Terminal snapshot recovery group is incomplete")
    normalized = tuple({name: row[name] for name in (
        "conid", "ticker", "currency", "asset_class",
        *(column for _, column in POSITION_FIELDS))}
        for row in sorted(children, key=lambda row: row["ordinal"]))
    if position_set_sha256(normalized) != parent.get("position_set_sha256"):
        raise ValueError("Terminal snapshot recovery hash differs")
    account = {source: {
        "amount": finite_float64(parent[column]),
        "currency": parent["currency"],
        "timestamp": parent["source_timestamp_ms"],
    } for source, column in ACCOUNT_METRICS}
    positions = tuple({
        "acctId": parent["account_id"], "conid": row["conid"],
        "contractDesc": row["ticker"], "currency": row["currency"],
        "assetClass": row["asset_class"],
        **{source: finite_float64(row[column]) for source, column in POSITION_FIELDS},
    } for row in normalized)
    if project_account_scalars(account) != {
            "currency": parent["currency"],
            "source_timestamp_ms": parent["source_timestamp_ms"],
            **{column: parent[column] for _, column in ACCOUNT_METRICS}}:
        raise ValueError("Terminal account recovery changed typed scalars")
    return account, positions
