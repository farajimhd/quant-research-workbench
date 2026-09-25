"""Closed, staged fixed-Backtest terminal snapshot scalar projection.

No persistence or runtime wiring lives here. Float64 is the source's numeric
contract: finite Python floats are written as Float64 and read back bit-exact.
"""
from __future__ import annotations

from hashlib import sha256
import math
import struct
from typing import Any, Mapping


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
