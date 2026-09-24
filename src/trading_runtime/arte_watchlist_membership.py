"""Typed Watchlist interval contract and read-only point-in-time reconstruction.

An ingestion owner must certify an empty or fully materialized starting state,
the configuration revision, and a contiguous coverage watermark. Existing
change events alone do not provide those guarantees. No SQLite fallback exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from src.trading_runtime.journal_contract import canonical_json


INTERVAL_TABLE = "trading_watchlist_membership_interval_v1"
COVERAGE_TABLE = "trading_watchlist_membership_coverage_v1"
PAGE_SIZE = 1000

INTERVAL_DDL = f"""CREATE TABLE arte.{INTERVAL_TABLE} (
    session_date Date, watchlist_id String, configuration_hash FixedString(64),
    source_revision String, ticker String, effective_at DateTime64(6, 'UTC'),
    available_at DateTime64(6, 'UTC'), expires_at Nullable(DateTime64(6, 'UTC')),
    ended_at Nullable(DateTime64(6, 'UTC')), source_event_id String,
    reason String, content_hash FixedString(64)
) ENGINE = MergeTree PARTITION BY toYYYYMM(session_date)
  ORDER BY (session_date, watchlist_id, configuration_hash,
    source_revision, ticker, effective_at, source_event_id)
  SETTINGS storage_policy='live_market_ssd'"""

COVERAGE_DDL = f"""CREATE TABLE arte.{COVERAGE_TABLE} (
    session_date Date, watchlist_id String, configuration_hash FixedString(64),
    source_revision String, source_cursor String, interval_count UInt64,
    interval_hash FixedString(64), coverage_start DateTime64(6, 'UTC'),
    complete_through DateTime64(6, 'UTC'), certified_at DateTime64(6, 'UTC'),
    content_hash FixedString(64)
) ENGINE = MergeTree PARTITION BY toYYYYMM(session_date)
  ORDER BY (session_date, watchlist_id, configuration_hash,
    source_revision) SETTINGS storage_policy='live_market_ssd'"""

_INTERVAL_FIELDS = frozenset({"session_date", "watchlist_id", "configuration_hash",
    "source_revision", "ticker", "effective_at", "available_at", "expires_at",
    "ended_at", "source_event_id", "reason", "content_hash"})
_COVERAGE_FIELDS = frozenset({"session_date", "watchlist_id", "configuration_hash",
    "source_revision", "source_cursor", "interval_count", "interval_hash",
    "coverage_start", "complete_through", "certified_at",
    "content_hash"})
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Watchlist timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # ClickHouse DateTime64 JSONEachRow omits the UTC zone suffix.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sealed(row: Mapping[str, Any], fields: frozenset[str]) -> dict[str, Any]:
    if set(row) != fields:
        raise RuntimeError("Watchlist typed row has missing or extra fields")
    normalized = dict(row)
    for key in fields & {"effective_at", "available_at", "expires_at", "ended_at",
                         "coverage_start", "complete_through", "certified_at"}:
        if normalized[key] is not None:
            normalized[key] = _time(normalized[key]).isoformat(timespec="microseconds")
    digest = sha256(canonical_json({key: value for key, value in normalized.items()
                                    if key != "content_hash"}).encode("utf-8")).hexdigest()
    if digest != normalized["content_hash"]:
        raise RuntimeError("Watchlist typed row differs from its content hash")
    return normalized


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _query(client: Any, table: str, where: str) -> tuple[dict[str, Any], ...]:
    response = client.execute(
        f"SELECT * FROM arte.{table} WHERE {where} LIMIT 2 FORMAT JSONEachRow")
    rows = tuple(json.loads(line) for line in response.splitlines() if line.strip())
    fields = _COVERAGE_FIELDS if table == COVERAGE_TABLE else _INTERVAL_FIELDS
    return tuple(_sealed(row, fields) for row in rows)


def _interval_pages(client: Any, where: str):
    prior: tuple[str, str, str] | None = None
    prior_hash: str | None = None
    while True:
        page_where = where
        if prior is not None:
            ticker, effective_at, event_id = prior
            clock = effective_at.replace("T", " ").replace("+00:00", "")
            page_where += (
                " AND (ticker,effective_at,source_event_id) >= "
                f"({_literal(ticker)},toDateTime64({_literal(clock)},6,'UTC'),"
                f"{_literal(event_id)})")
        response = client.execute(
            f"SELECT * FROM arte.{INTERVAL_TABLE} WHERE {page_where} "
            "ORDER BY ticker,effective_at,source_event_id "
            f"LIMIT {PAGE_SIZE + (1 if prior is not None else 0)} FORMAT JSONEachRow")
        raw = tuple(json.loads(line) for line in response.splitlines() if line.strip())
        if len(raw) > PAGE_SIZE + (1 if prior is not None else 0):
            raise RuntimeError("Watchlist interval page exceeds its bound")
        page = tuple(_sealed(row, _INTERVAL_FIELDS) for row in raw)
        if prior is not None:
            if (not page or (page[0]["ticker"], page[0]["effective_at"],
                             page[0]["source_event_id"]) != prior
                    or page[0]["content_hash"] != prior_hash):
                raise RuntimeError("Watchlist interval keyset changed during recovery")
            page = page[1:]
        if not page:
            return
        keys = tuple((row["ticker"], row["effective_at"], row["source_event_id"])
                     for row in page)
        if (list(keys) != sorted(keys) or len(set(keys)) != len(keys)
                or prior is not None and keys[0] <= prior):
            raise RuntimeError("Watchlist interval keyset is duplicated or unordered")
        yield from page
        prior = keys[-1]
        prior_hash = page[-1]["content_hash"]
        if len(page) < PAGE_SIZE:
            return


@dataclass(frozen=True, slots=True)
class WatchlistMembership:
    ticker: str
    effective_at: datetime
    expires_at: datetime | None
    ended_at: datetime | None
    source_event_id: str
    reason: str


def load_watchlist_membership_as_of(
    client: Any, *, session_date: date, watchlist_id: str,
    configuration_hash: str, source_revision: str, as_of: datetime,
) -> tuple[WatchlistMembership, ...]:
    """Require certified coverage and reconstruct active half-open intervals."""
    if (type(session_date) is not date or not watchlist_id or not source_revision
            or not isinstance(configuration_hash, str) or not _HASH.fullmatch(configuration_hash)
            or not isinstance(as_of, datetime) or as_of.tzinfo is None):
        raise ValueError("Watchlist as-of request lacks typed causal identity")
    cutoff = as_of.astimezone(timezone.utc)
    if cutoff.astimezone(ZoneInfo("America/New_York")).date() != session_date:
        raise ValueError("Watchlist as-of time differs from its market session date")
    identity = {"session_date": session_date.isoformat(), "watchlist_id": watchlist_id,
                "configuration_hash": configuration_hash, "source_revision": source_revision}
    where = " AND ".join(f"{key}={_literal(value)}" for key, value in identity.items())
    coverage = _query(client, COVERAGE_TABLE, where)
    if len(coverage) != 1:
        raise RuntimeError("Watchlist as-of recovery lacks one coverage watermark")
    cover = coverage[0]
    if (any(cover[key] != value for key, value in identity.items())
            or not _time(cover["coverage_start"]) <= cutoff <= _time(cover["complete_through"]) <= _time(cover["certified_at"])):
        raise RuntimeError("Watchlist as-of time is outside certified coverage")
    active: dict[str, WatchlistMembership] = {}
    seen: set[tuple[str, str]] = set()
    prior_end: dict[str, datetime | None] = {}
    interval_digest = sha256()
    interval_digest.update(b"[")
    interval_count = 0
    for row in _interval_pages(client, where):
        if (any(row[key] != value for key, value in identity.items())
                or not isinstance(row["ticker"], str) or not row["ticker"]
                or row["ticker"] != row["ticker"].upper()
                or not isinstance(row["source_event_id"], str) or not row["source_event_id"]
                or not isinstance(row["reason"], str)):
            raise RuntimeError("Watchlist interval identity differs from certified scope")
        start, available = _time(row["effective_at"]), _time(row["available_at"])
        expiry = _time(row["expires_at"]) if row["expires_at"] is not None else None
        end = _time(row["ended_at"]) if row["ended_at"] is not None else None
        if (start < _time(cover["coverage_start"]) or start > _time(cover["complete_through"])
                or available > start or (expiry is not None and expiry <= start)
                or (end is not None and end <= start)
                or (row["ticker"], row["source_event_id"]) in seen):
            raise RuntimeError("Watchlist interval chronology or identity is invalid")
        seen.add((row["ticker"], row["source_event_id"]))
        previous_end = prior_end.get(row["ticker"])
        if row["ticker"] in prior_end and (previous_end is None or start < previous_end):
            raise RuntimeError("Watchlist membership intervals overlap")
        bounds = [value for value in (expiry, end) if value is not None]
        prior_end[row["ticker"]] = min(bounds) if bounds else None
        if interval_count:
            interval_digest.update(b",")
        interval_digest.update(canonical_json((row["ticker"], row["effective_at"],
                            row["source_event_id"], row["content_hash"])).encode("utf-8"))
        interval_count += 1
        if start <= cutoff and (expiry is None or cutoff < expiry) and (end is None or cutoff < end):
            if row["ticker"] in active:
                raise RuntimeError("Watchlist as-of membership has overlapping intervals")
            active[row["ticker"]] = WatchlistMembership(
                row["ticker"], start, expiry, end, row["source_event_id"], row["reason"])
    interval_digest.update(b"]")
    if (type(cover["interval_count"]) is not int or cover["interval_count"] != interval_count
            or cover["interval_hash"] != interval_digest.hexdigest() or not cover["source_cursor"]):
        raise RuntimeError("Watchlist coverage differs from complete interval set or source cursor")
    return tuple(active[ticker] for ticker in sorted(active))
