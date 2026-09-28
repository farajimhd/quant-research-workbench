"""Producer-owned, point-in-time entry context for saved Strategy 1 episodes.

Backtest does not call this module. The producer reads pinned ARTE market bars,
dated QMD reference evidence, and a certified prior-session RVOL baseline. Its
output contract is scalar and tabular; no checkpoint, JSON, or blob is stored.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, assert_select_only,
    market_day_boundary,
)
from src.backend.session_relative_volume import validate_baseline


TABLE = "arte.strategy_one_entry_context_v1"
VERSION = 1
_NY = ZoneInfo("America/New_York")

CREATE_TABLE = f"""CREATE TABLE IF NOT EXISTS {TABLE} (
    session_date Date,
    run_id UUID,
    episode_id UUID,
    ticker LowCardinality(String),
    symbol_id String,
    conid UInt64,
    entry_at_utc DateTime64(6, 'UTC'),
    market_build_id String,
    market_plan_token FixedString(64),
    bars_attempt_id UUID,
    reference_float_date Nullable(Date),
    reference_float_source LowCardinality(String),
    reference_float_evidence_hash String,
    float_shares Nullable(Float64),
    reference_shares_date Nullable(Date),
    reference_shares_source LowCardinality(String),
    reference_shares_evidence_hash String,
    shares_outstanding Nullable(Float64),
    baseline_content_hash String,
    baseline_source_token String,
    baseline_volume Nullable(Float64),
    session_volume Float64,
    entry_rvol Nullable(Float64),
    last_minute_trade_count UInt64,
    last_minute_volume Float64,
    calculation_version UInt16,
    content_hash FixedString(64),
    inserted_at DateTime64(6, 'UTC') DEFAULT now64(6)
) ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(session_date)
ORDER BY (session_date,run_id,episode_id)
SETTINGS storage_policy='live_market_ssd'"""


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def completed_second_window(session: date, entry_at: datetime) -> tuple[int, int, int]:
    """Return inclusive midnight-based second buckets and baseline index.

    A second completing exactly when an entry opens is available; the forming
    second containing an intra-second entry is never used.
    """
    if entry_at.tzinfo is None:
        raise ValueError("Entry timestamp must be timezone-aware")
    start = market_day_boundary(session, 0)
    entry = entry_at.astimezone(_NY)
    elapsed_us = round((entry - start).total_seconds() * 1_000_000)
    if not 0 <= elapsed_us <= 57_600_000_000:
        raise ValueError("Entry is outside its certified market session")
    second = elapsed_us // 1_000_000
    upper = SESSION_OPEN_OFFSET_MS // 1_000 + second - 1
    lower = max(SESSION_OPEN_OFFSET_MS // 1_000, upper - 59)
    return lower, upper, second


def pinned_entry_volume(client: Any, plan: CertifiedMarketDayPlan, *,
                        session: date, ticker: str,
                        entry_at: datetime) -> dict[str, float | int]:
    """Read completed 1s bars only from the run's certified bars attempt."""
    units = [unit for unit in plan.units if unit.session_date == session.isoformat()
             and unit.ticker == ticker and unit.stage == "bars"]
    if len(units) != 1 or 1_000 not in plan.required_resolutions_ms:
        raise RuntimeError("Entry volume has no unique pinned 1s bars attempt")
    lower, upper, _ = completed_second_window(session, entry_at)
    if upper < SESSION_OPEN_OFFSET_MS // 1_000:
        return {"session_volume": 0.0, "last_minute_volume": 0.0,
                "last_minute_trade_count": 0}
    query = assert_select_only(
        "SELECT sum(volume) AS session_volume,"
        f"sumIf(volume,bucket_index>={lower}) AS last_minute_volume,"
        f"sumIf(trade_count,bucket_index>={lower}) AS last_minute_trade_count "
        "FROM arte.bars_v1 "
        f"WHERE build_id={_literal(plan.build_id)} "
        f"AND session_date=toDate({_literal(session.isoformat())}) "
        f"AND ticker={_literal(ticker)} "
        f"AND attempt_id=toUUID({_literal(units[0].attempt_id)}) "
        f"AND resolution_ms=1000 AND bucket_index>={SESSION_OPEN_OFFSET_MS // 1_000} "
        f"AND bucket_index<={upper} FORMAT JSONEachRow"
    )
    rows = [json.loads(line) for line in client.execute(query).splitlines()
            if line.strip()]
    if len(rows) != 1:
        raise RuntimeError("Pinned entry volume aggregation was not unique")
    row = rows[0]
    return {"session_volume": float(row["session_volume"] or 0),
            "last_minute_volume": float(row["last_minute_volume"] or 0),
            "last_minute_trade_count": int(row["last_minute_trade_count"] or 0)}


def certified_entry_rvol(baseline: dict[str, Any], *, session: date,
                         ticker: str, entry_at: datetime,
                         session_volume: float) -> tuple[float | None, float | None]:
    """Use QMD's exact aligned prior-20-session denominator at entry."""
    validate_baseline(baseline, ticker, session.isoformat())
    _, _, second = completed_second_window(session, entry_at)
    denominator = baseline["profiles"][ticker][second]
    if denominator is None:
        return None, None
    volume = float(denominator)
    if volume <= 0 or session_volume < 0:
        raise ValueError("RVOL has invalid causal volume or denominator")
    return session_volume / volume, volume


def asof_reference(client: Any, *, session: date, ticker: str,
                   conid: int, entry_at: datetime) -> dict[str, Any]:
    """Resolve dated share supply by symbol identity, never current ticker alone."""
    if entry_at.tzinfo is None or conid <= 0:
        raise ValueError("Reference requires an aware entry and positive conid")
    cutoff = entry_at.astimezone(UTC).isoformat(timespec="milliseconds")
    day = _literal(session.isoformat())
    where = (f"ticker={_literal(ticker)} AND ibkr_conid={_literal(str(conid))} "
             f"AND universe_date<=toDate({day}) "
             f"AND inserted_at<=parseDateTime64BestEffort({_literal(cutoff)})")
    identity_query = assert_select_only(
        "SELECT symbol_id,ibkr_conid,universe_date FROM "
        "q_live.feature_tradable_universe_v1 "
        f"WHERE {where} ORDER BY universe_date DESC,inserted_at DESC "
        "LIMIT 2 FORMAT JSONEachRow")
    identities = [json.loads(line) for line in client.execute(identity_query).splitlines()
                  if line.strip()]
    if not identities or str(identities[0]["ibkr_conid"] or "") != str(conid):
        raise RuntimeError("Entry has no matching point-in-time symbol/conid identity")
    symbol_id = str(identities[0]["symbol_id"])
    if len(identities) == 2 and identities[0]["universe_date"] == identities[1]["universe_date"] \
            and identities[0]["symbol_id"] != identities[1]["symbol_id"]:
        raise RuntimeError(f"{ticker} entry date has ambiguous reference symbol identity")
    result: dict[str, Any] = {"symbol_id": symbol_id}
    for target, column in (("float", "free_float"),
                           ("shares", "shares_outstanding")):
        query = assert_select_only(
            f"SELECT effective_date,source_system,source_content_sha256,"
            f"{column} AS value FROM "
            "q_live.market_security_float_v1 "
            f"WHERE symbol_id={_literal(symbol_id)} "
            f"AND effective_date<=toDate({day}) AND {column} IS NOT NULL "
            f"AND inserted_at<=parseDateTime64BestEffort({_literal(cutoff)}) "
            "ORDER BY effective_date DESC,inserted_at DESC LIMIT 1 FORMAT JSONEachRow")
        rows = [json.loads(line) for line in client.execute(query).splitlines()
                if line.strip()]
        result[f"{target}_value"] = float(rows[0]["value"]) if rows else None
        result[f"{target}_date"] = rows[0]["effective_date"] if rows else None
        result[f"{target}_source"] = rows[0]["source_system"] if rows else "unavailable"
        result[f"{target}_evidence_hash"] = rows[0]["source_content_sha256"] if rows else ""
    return result


def build_entry_context_rows(*, run_id: str, report: dict[str, Any],
                             session: date, plan: CertifiedMarketDayPlan,
                             market_client: Any, reference_client: Any,
                             baseline_provider: Any) -> list[dict[str, Any]]:
    """Project closed episodes; the result is ready for one tabular INSERT batch.

    The provider is invoked once per ticker and never from the execution loop.
    A baseline miss is retained as a nullable RVOL, not a fabricated zero.
    """
    baselines: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for episode in report.get("episodes", []):
        episode_id = str(episode["episode_id"])
        if episode_id in seen:
            raise RuntimeError("Performance report repeats an episode identity")
        seen.add(episode_id)
        instrument = episode["instrument"]
        ticker = str(instrument["symbol"]).strip().upper()
        conid = int(instrument["conid"])
        entry_at = datetime.fromisoformat(str(episode["opened_at"]))
        if ticker not in plan.tickers or entry_at.tzinfo is None:
            raise RuntimeError("Entry is outside the sealed run population")
        entry_at_utc = entry_at.astimezone(UTC)
        units = [unit for unit in plan.units if unit.session_date == session.isoformat()
                 and unit.ticker == ticker and unit.stage == "bars"]
        if len(units) != 1:
            raise RuntimeError("Entry has no unique pinned bars attempt")
        market = pinned_entry_volume(market_client, plan, session=session,
                                     ticker=ticker, entry_at=entry_at)
        reference = asof_reference(reference_client, session=session,
                                   ticker=ticker, conid=conid, entry_at=entry_at)
        if ticker not in baselines:
            baselines[ticker] = baseline_provider(session.isoformat(), ticker)
        baseline = baselines[ticker]
        rvol, denominator = certified_entry_rvol(
            baseline, session=session, ticker=ticker, entry_at=entry_at,
            session_volume=float(market["session_volume"]))
        row = {
            "session_date": session.isoformat(), "run_id": run_id,
            "episode_id": episode_id, "ticker": ticker,
            "symbol_id": reference["symbol_id"], "conid": conid,
            "entry_at_utc": entry_at_utc.isoformat(timespec="microseconds"),
            "market_build_id": plan.build_id, "market_plan_token": plan.token,
            "bars_attempt_id": units[0].attempt_id,
            "reference_float_date": reference["float_date"],
            "reference_float_source": reference["float_source"],
            "reference_float_evidence_hash": reference["float_evidence_hash"],
            "float_shares": reference["float_value"],
            "reference_shares_date": reference["shares_date"],
            "reference_shares_source": reference["shares_source"],
            "reference_shares_evidence_hash": reference["shares_evidence_hash"],
            "shares_outstanding": reference["shares_value"],
            "baseline_content_hash": baseline["content_hash"],
            "baseline_source_token": baseline["source_revision"]["token"],
            "baseline_volume": denominator,
            "session_volume": market["session_volume"],
            "entry_rvol": rvol,
            "last_minute_trade_count": market["last_minute_trade_count"],
            "last_minute_volume": market["last_minute_volume"],
            "calculation_version": VERSION,
        }
        row["content_hash"] = sha256(json.dumps(
            row, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        results.append(row)
    return results


def attach_saved_entry_context(client: Any, page: dict[str, Any], *,
                               market_plan_token: str) -> dict[str, Any]:
    """Join optional producer rows to verified episodes by immutable identity.

    An absent product leaves the five UI fields nullable. An existing but
    mismatched product fails closed; the saved-page reader never builds it.
    """
    exists = client.execute(
        "SELECT count() FROM system.tables WHERE database='arte' "
        "AND name='strategy_one_entry_context_v1' FORMAT TabSeparatedRaw").strip()
    if exists == "0":
        return page
    if exists != "1":
        raise RuntimeError("Entry context table existence is ambiguous")
    run_id = str(page["run_id"])
    episodes = page["report"]["episodes"]
    expected = {str(row["episode_id"]): row for row in episodes}
    if len(expected) != len(episodes):
        raise RuntimeError("Verified performance repeats an episode identity")
    query = assert_select_only(
        "SELECT toString(episode_id) AS episode_id,ticker,conid,entry_at_utc,"
        "market_plan_token,calculation_version,shares_outstanding,entry_rvol,"
        "float_shares,last_minute_trade_count,last_minute_volume "
        f"FROM {TABLE} FINAL WHERE run_id=toUUID({_literal(run_id)}) "
        "FORMAT JSONEachRow")
    found: set[str] = set()
    for line in client.execute(query).splitlines():
        if not line.strip():
            continue
        context = json.loads(line)
        episode_id = str(context["episode_id"])
        episode = expected.get(episode_id)
        if episode is None or episode_id in found:
            raise RuntimeError("Entry context has an unknown or repeated episode")
        found.add(episode_id)
        opened = datetime.fromisoformat(str(episode["opened_at"])).astimezone(UTC)
        stored = datetime.fromisoformat(str(context["entry_at_utc"])).replace(tzinfo=UTC)
        if (context["market_plan_token"] != market_plan_token
                or int(context["calculation_version"]) != VERSION
                or context["ticker"] != episode["instrument"]["symbol"]
                or int(context["conid"]) != int(episode["instrument"]["conid"])
                or stored != opened):
            raise RuntimeError("Entry context differs from the verified episode or market plan")
        episode.update({
            "entry_shares_outstanding": context["shares_outstanding"],
            "entry_rvol": context["entry_rvol"],
            "entry_float_shares": context["float_shares"],
            "entry_last_minute_trade_count": context["last_minute_trade_count"],
            "entry_last_minute_volume": context["last_minute_volume"],
        })
    return page
