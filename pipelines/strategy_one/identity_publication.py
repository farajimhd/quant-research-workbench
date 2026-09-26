"""Coverage-last publication of dated broker identities for Strategy 1.

Only this producer owns INSERT authority. Missing, ambiguous, or late q_live
identity is a hard failure; Backtest never repairs it. Uncertain child writes
are not retried under the same attempt UUID.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pipelines.market_sip.events.market_day_sql import literal
from src.backend.backtest_market_data import CertifiedMarketDayPlan
from src.backend.backtest_strategy_one_identity import (
    certify_identity_plan, identity_content_hash,
)
from src.trading_runtime.strategy_one_identity_schema import (
    COVERAGE_TABLE, IDENTITY_TABLE,
)


_FIELDS = ("ticker", "symbol_id", "listing_id", "security_id",
           "ibkr_conid", "source_run_id", "source_inserted_at")


def dated_source_rows(client: Any, market: CertifiedMarketDayPlan) -> list[dict[str, Any]]:
    """Select exactly one pre-cutoff tradable source row per market ticker."""
    if not isinstance(market, CertifiedMarketDayPlan) or len(market.sessions) != 1:
        raise ValueError("Identity producer needs one certified market session")
    day = market.sessions[0]
    cutoff = datetime.combine(date.fromisoformat(day), time(4),
                              ZoneInfo("America/New_York")).astimezone(timezone.utc)
    source = [json.loads(line) for line in client.execute(
        "SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,"
        "source_run_id,source_inserted_at FROM "
        "(SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,"
        "source_run_id,inserted_at AS source_inserted_at "
        "FROM q_live.feature_tradable_universe_v1 FINAL "
        f"WHERE universe_date=toDate({literal(day)}) AND is_tradable=1) "
        "ORDER BY ticker FORMAT JSONEachRow").splitlines() if line.strip()]
    expected = set(market.tickers)
    rows: dict[str, dict[str, Any]] = {}
    for raw in source:
        ticker = str(raw.get("ticker") or "")
        if ticker not in expected:
            continue
        raw_conid = raw.get("ibkr_conid")
        if (ticker in rows or not isinstance(raw_conid, str)
                or not raw_conid.isdecimal() or int(raw_conid) <= 0
                or any(not isinstance(raw.get(key), str) or not raw[key]
                       for key in ("symbol_id", "listing_id", "security_id",
                                   "source_run_id"))):
            raise RuntimeError(f"Ambiguous or incomplete dated identity: {ticker}")
        stamp = str(raw.get("source_inserted_at") or "")
        try:
            available = datetime.fromisoformat(stamp.replace(" ", "T"))
        except ValueError as exc:
            raise RuntimeError(f"Invalid dated identity time: {ticker}") from exc
        if available.tzinfo is not None or available.replace(tzinfo=timezone.utc) > cutoff:
            raise RuntimeError(f"Dated identity arrived after market cutoff: {ticker}")
        rows[ticker] = {**{key: raw[key] for key in _FIELDS},
                        "ibkr_conid": int(raw_conid)}
    if set(rows) != expected:
        missing = sorted(expected - rows.keys())
        raise RuntimeError(f"Dated identity population missing {len(missing)} tickers: "
                           + ", ".join(missing[:8]))
    return [rows[ticker] for ticker in market.tickers]


def _read_attempt(client: Any, market: CertifiedMarketDayPlan,
                  attempt: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(
        f"SELECT {','.join(_FIELDS)} FROM {IDENTITY_TABLE} "
        f"WHERE source_build_id={literal(market.build_id)} "
        f"AND session_date=toDate({literal(market.sessions[0])}) "
        f"AND identity_attempt_id=toUUID({literal(attempt)}) "
        "ORDER BY ticker FORMAT JSONEachRow").splitlines() if line.strip()]


def publish_identity(client: Any, market: CertifiedMarketDayPlan) -> str:
    """Publish once, or verify and skip the already committed exact source."""
    expected = dated_source_rows(client, market)
    digest = identity_content_hash(expected)
    existing = client.execute(
        f"SELECT count() FROM {COVERAGE_TABLE} "
        f"WHERE source_build_id={literal(market.build_id)} "
        f"AND session_date=toDate({literal(market.sessions[0])}) "
        "FORMAT TabSeparated").strip()
    if existing not in {"0", "1"}:
        raise RuntimeError("Identity coverage inventory is ambiguous")
    if existing == "1":
        sealed = certify_identity_plan(market, client=client)
        if sealed.content_hash != digest:
            raise RuntimeError("Dated identity source changed after publication")
        return "skipped"
    attempt = str(uuid4())
    for start in range(0, len(expected), 500):
        chunk = expected[start:start + 500]
        values = ",".join("(" + ",".join((
            literal(market.build_id), f"toDate({literal(market.sessions[0])})",
            f"toUUID({literal(attempt)})", literal(row["ticker"]),
            literal(row["symbol_id"]), literal(row["listing_id"]),
            literal(row["security_id"]), str(row["ibkr_conid"]),
            literal(row["source_run_id"]),
            f"toDateTime64({literal(row['source_inserted_at'])},3,'UTC')",
        )) + ")" for row in chunk)
        client.execute(f"INSERT INTO {IDENTITY_TABLE} "
                       "(source_build_id,session_date,identity_attempt_id,"
                       f"{','.join(_FIELDS)}) VALUES {values}")
    observed = _read_attempt(client, market, attempt)
    if (len(observed) != len(expected)
            or identity_content_hash(observed) != digest
            or tuple(row["ticker"] for row in observed) != market.tickers):
        raise RuntimeError("Identity child insert differs from dated source")
    client.execute(f"INSERT INTO {COVERAGE_TABLE} "
                   "(source_build_id,session_date,identity_attempt_id,"
                   "universe_date,ticker_count,content_hash,certified_at) "
                   f"SELECT {literal(market.build_id)},"
                   f"toDate({literal(market.sessions[0])}),"
                   f"toUUID({literal(attempt)}),"
                   f"toDate({literal(market.sessions[0])}),"
                   f"toUInt32({len(expected)}),{literal(digest)},now64(6,'UTC')")
    if certify_identity_plan(market, client=client).attempt_id != attempt:
        raise RuntimeError("Identity coverage publication did not certify")
    return "published"
