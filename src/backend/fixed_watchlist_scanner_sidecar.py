"""Operator-installed, read-only Backtest contract for QMD scanner boundaries.

The QMD History producer must publish this sidecar; Backtest cannot build it.
This module defines its scalar schema, validates a producer snapshot, and
verifies cold reads. It never executes DDL or INSERT.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping
import re

from src.trading_runtime.journal_contract import canonical_json


SCANNER_SCHEMA = "canvas_historical_qmd_snapshot_v9"
SCORE_REVISION = "qmd-liquidity-ranking-v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_QUALITY = {"ready", "unavailable", "crossed", "locked", "stale"}
_REASONS = {
    "no_executed_session_liquidity", "session_dollar_volume_below_1000000",
    "session_share_volume_below_100000", "session_trade_count_below_1000",
    "trade_rate_60s_below_0_5", "executable_nbbo_unavailable",
    "spread_above_50_bps",
}
_MEASURES = (
    "last_price", "bid", "ask", "bid_size", "ask_size",
    "day_dollar_volume", "day_volume", "trade_rate_10s",
    "trade_rate_60s", "spread", "liquidity_score",
)


def sidecar_ddl() -> tuple[str, str]:
    """DDL for an operator to review/install on live_market_ssd; never run here."""
    boundary = """CREATE TABLE IF NOT EXISTS arte.qmd_scanner_boundary_v1 (
      boundary_id FixedString(64), boundary_at DateTime64(6,'UTC'),
      source_revision_token String, source_plan_hash String,
      source_revision_hash FixedString(64),
      scanner_schema_version String, scanner_score_revision String,
      market_plan_token FixedString(64), event_count UInt64,
      market_row_count UInt32, source_market_row_hash FixedString(64),
      market_row_hash FixedString(64),
      content_hash FixedString(64)) ENGINE=MergeTree
      PARTITION BY toYYYYMM(boundary_at)
      ORDER BY (boundary_id) SETTINGS storage_policy='live_market_ssd'"""
    symbol = """CREATE TABLE IF NOT EXISTS arte.qmd_scanner_symbol_v1 (
      boundary_id FixedString(64), boundary_at DateTime64(6,'UTC'),
      ticker String, last_event_at Nullable(DateTime64(6,'UTC')),
      event_age_ms Nullable(UInt64), quality_state LowCardinality(String),
      last_price Decimal(38,18), bid Decimal(38,18), ask Decimal(38,18),
      bid_size Decimal(38,18), ask_size Decimal(38,18),
      day_dollar_volume Decimal(38,18), day_volume Decimal(38,18),
      day_trade_count UInt64, trade_rate_10s Decimal(38,18),
      trade_rate_60s Decimal(38,18), spread Decimal(38,18),
      liquidity_score Decimal(38,18), liquidity_rank UInt32,
      liquidity_eligible UInt8,
      no_executed_liquidity UInt8, below_dollar_min UInt8,
      below_share_min UInt8, below_trade_count_min UInt8,
      below_rate_min UInt8, no_executable_nbbo UInt8,
      spread_above_max UInt8, content_hash FixedString(64)) ENGINE=MergeTree
      PARTITION BY toYYYYMM(boundary_at)
      ORDER BY (boundary_id,ticker) SETTINGS storage_policy='live_market_ssd'"""
    return boundary, symbol


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _clock(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Scanner boundary clock must be typed ISO text")
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("Scanner boundary clock is naive")
    return at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _number(value: Any) -> str:
    if type(value) not in (int, float, Decimal):
        raise ValueError("Scanner measure has invalid type")
    try:
        number = Decimal(str(value))
        quantized = number.quantize(Decimal("0.000000000000000001"))
    except InvalidOperation as exc:
        raise ValueError("Scanner measure exceeds scalar precision") from exc
    if not number.is_finite() or number != quantized or abs(number) >= Decimal(10) ** 20:
        raise ValueError("Scanner measure is not lossless Decimal(38,18)")
    return format(quantized, "f")


def _stored_clock(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Stored scanner clock is invalid")
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)  # ClickHouse DateTime64 JSON is UTC without offset.
    return at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _stored_number(value: Any) -> str:
    try:
        return _number(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("Stored scanner measure is invalid") from exc


def prepare_scanner_boundary(
    snapshot: Mapping[str, Any], *, market_plan_token: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Accept only a complete, producer-certified full-market snapshot.

    The QMD History producer owns the score revision and full-population
    market-row certificate. Older snapshots without either fail closed.
    """
    if _HEX.fullmatch(market_plan_token) is None:
        raise ValueError("Scanner sidecar requires a pinned market plan token")
    revision = snapshot.get("source_revision")
    if (not isinstance(revision, Mapping)
            or revision.get("complete_for_history") is not True
            or revision.get("request_complete") is not True
            or not isinstance(revision.get("token"), str)
            or not revision["token"]
            or not isinstance(revision.get("source_plan_hash"), str)
            or not revision["source_plan_hash"]
            or snapshot.get("schema_version") != SCANNER_SCHEMA
            or snapshot.get("scanner_score_revision") != SCORE_REVISION
            or not isinstance(snapshot.get("market_rows"), list)
            or type(snapshot.get("market_row_count")) is not int
            or _HEX.fullmatch(str(snapshot.get("market_rows_sha256"))) is None
            or type(snapshot.get("event_count")) is not int
            or snapshot["event_count"] < 0):
        raise ValueError("QMD scanner lacks certified score revision or full-scope rows")
    at = _clock(snapshot.get("as_of"))
    if datetime.fromisoformat(at).microsecond % 100_000:
        raise ValueError("QMD scanner sidecar requires a completed 100ms boundary")
    boundary_id = _hash({"at": at, "revision": revision["token"],
                         "market_plan_token": market_plan_token,
                         "score_revision": SCORE_REVISION})
    # QMD signs the source rows with its canonical serde_json encoder. The
    # sidecar preserves that producer hash and separately seals normalized rows;
    # Python must not substitute its subtly different float JSON encoder.
    if len(snapshot["market_rows"]) != snapshot["market_row_count"]:
        raise ValueError("QMD scanner source population certificate differs")
    rows: list[dict[str, Any]] = []
    for source in snapshot["market_rows"]:
        if not isinstance(source, Mapping):
            raise ValueError("QMD scanner market row is not typed")
        ticker = source.get("ticker")
        reasons = source.get("liquidity_eligibility_reasons")
        quality = source.get("quality_state")
        quality_reason = f"market_state_{quality}" if quality != "ready" else None
        if (not isinstance(ticker, str)
                or re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", ticker) is None
                or source.get("quality_state") not in _QUALITY
                or not isinstance(reasons, list)
                or len(set(reasons)) != len(reasons)
                or set(reasons) - (_REASONS | ({quality_reason} if quality_reason else set()))
                or (quality_reason is not None and quality_reason not in reasons
                    and "no_executed_session_liquidity" not in reasons)
                or type(source.get("liquidity_eligible")) is not bool
                or type(source.get("day_trade_count")) is not int
                or source["day_trade_count"] < 0
                or type(source.get("liquidity_rank")) is not int
                or source["liquidity_rank"] < 1):
            raise ValueError("QMD scanner market row has unsupported state")
        last = source.get("last_event_ts")
        age = source.get("event_age_ms")
        if ((last is None) != (age is None)
                or (age is not None and (type(age) is not int or age < 0))):
            raise ValueError("QMD scanner freshness state is inconsistent")
        row = {
            "boundary_id": boundary_id, "boundary_at": at,
            "ticker": ticker, "last_event_at": _clock(last) if last else None,
            "event_age_ms": age, "quality_state": source["quality_state"],
            **{key: _number(source.get(key)) for key in _MEASURES},
            "day_trade_count": source["day_trade_count"],
            "liquidity_rank": source["liquidity_rank"],
            "liquidity_eligible": int(source["liquidity_eligible"]),
            **{column: int(reason in reasons) for column, reason in (
                ("no_executed_liquidity", "no_executed_session_liquidity"),
                ("below_dollar_min", "session_dollar_volume_below_1000000"),
                ("below_share_min", "session_share_volume_below_100000"),
                ("below_trade_count_min", "session_trade_count_below_1000"),
                ("below_rate_min", "trade_rate_60s_below_0_5"),
                ("no_executable_nbbo", "executable_nbbo_unavailable"),
                ("spread_above_max", "spread_above_50_bps"),
            )},
        }
        row["content_hash"] = _hash(row)
        rows.append(row)
    rows.sort(key=lambda row: row["ticker"])
    if (len(rows) != snapshot["market_row_count"]
            or len({row["ticker"] for row in rows}) != len(rows)
            or len({row["liquidity_rank"] for row in rows}) != len(rows)
            ):
        raise ValueError("QMD scanner full-scope population certificate differs")
    boundary = {
        "boundary_id": boundary_id, "boundary_at": at,
        "source_revision_token": revision["token"],
        "source_plan_hash": revision["source_plan_hash"],
        "source_revision_hash": _hash(revision),
        "scanner_schema_version": SCANNER_SCHEMA,
        "scanner_score_revision": SCORE_REVISION,
        "market_plan_token": market_plan_token,
        "event_count": snapshot["event_count"],
        "market_row_count": len(rows),
        "source_market_row_hash": snapshot["market_rows_sha256"],
        "market_row_hash": _hash(rows),
    }
    boundary["content_hash"] = _hash(boundary)
    return boundary, tuple(rows)


def operator_publication_plan(
    snapshot: Mapping[str, Any], *, market_plan_token: str,
) -> tuple[tuple[str, tuple[dict[str, Any], ...]], ...]:
    """Prepare ordered, sealed rows for operator review; never execute writes.

    The boundary is last so a partial symbol publication cannot masquerade as
    a completed snapshot. The operator publisher must check table placement,
    handle ambiguous insert acknowledgments, and verify cold readback.
    """
    boundary, rows = prepare_scanner_boundary(
        snapshot, market_plan_token=market_plan_token,
    )
    return (
        ("arte.qmd_scanner_symbol_v1", rows),
        ("arte.qmd_scanner_boundary_v1", (boundary,)),
    )


def load_scanner_boundary(
    client: Any, boundary_id: str, *, market_plan_token: str,
    source_revision_token: str, boundary_at: datetime,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Diagnostic CH-only read; not Backtest admission without Keeper proof."""
    if (_HEX.fullmatch(boundary_id) is None
            or _HEX.fullmatch(market_plan_token) is None
            or not source_revision_token or boundary_at.tzinfo is None):
        raise ValueError("Scanner boundary identity is invalid")
    boundaries = list(client.iter_json_each_row(
        "SELECT * FROM arte.qmd_scanner_boundary_v1 WHERE boundary_id='"
        + boundary_id + "' FORMAT JSONEachRow"))
    rows = list(client.iter_json_each_row(
        "SELECT * FROM arte.qmd_scanner_symbol_v1 WHERE boundary_id='"
        + boundary_id + "' ORDER BY ticker FORMAT JSONEachRow"))
    if len(boundaries) != 1 or any(row.get("boundary_id") != boundary_id for row in rows):
        raise RuntimeError("Scanner sidecar boundary is missing or ambiguous")
    boundary = dict(boundaries[0])
    boundary["boundary_at"] = _stored_clock(boundary.get("boundary_at"))
    rows = [dict(row) for row in rows]
    for row in rows:
        row["boundary_at"] = _stored_clock(row.get("boundary_at"))
        if row.get("last_event_at") is not None:
            row["last_event_at"] = _stored_clock(row["last_event_at"])
        for key in _MEASURES:
            row[key] = _stored_number(row.get(key))
    if (boundary.get("boundary_id") != boundary_id
            or boundary.get("market_plan_token") != market_plan_token
            or boundary.get("source_revision_token") != source_revision_token
            or boundary["boundary_at"] != boundary_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
            or boundary.get("scanner_schema_version") != SCANNER_SCHEMA
            or boundary.get("scanner_score_revision") != SCORE_REVISION
            or _hash({key: value for key, value in boundary.items() if key != "content_hash"})
            != boundary.get("content_hash")
            or any(_hash({key: value for key, value in row.items() if key != "content_hash"})
                   != row.get("content_hash") for row in rows)
            or len(rows) != int(boundary["market_row_count"])
            or len({row["ticker"] for row in rows}) != len(rows)
            or _hash(rows) != boundary["market_row_hash"]):
        raise RuntimeError("Scanner sidecar content or full-scope coverage differs")
    return boundary, tuple(rows)
