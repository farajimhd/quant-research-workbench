"""Manual hindsight intervals. Market data remains exclusively QMD-owned."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager, closing
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.runtime_paths import project_runtime_root, runtime_root

router = APIRouter(prefix="/api/research/labeler", tags=["chart labeler"])
NY = ZoneInfo("America/New_York")
TIMEFRAMES = {"100ms": 100, "1s": 1000, "5s": 5000, "10s": 10000, "30s": 30000, "1m": 60000, "5m": 300000, "1h": 3600000}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def millis(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    if delta.microseconds % 1000:
        raise ValueError("Timestamp must have exact millisecond precision")
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def iso(ms: int) -> str:
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_date: date
    session: Literal["regular", "extended"] = "regular"
    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9 .^/_\-]{0,31}$")
    timeframe: Literal["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"] = "1h"
    label_set: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,64}$")


def bounds(day: date, session: str) -> tuple[int, int]:
    schedule = mcal.get_calendar("NYSE").schedule(start_date=day, end_date=day)
    if schedule.empty:
        raise ValueError("Selected date is not a market session")
    if session == "regular":
        start, end = schedule.iloc[0]["market_open"], schedule.iloc[0]["market_close"]
    else:
        start, end = datetime.combine(day, time(4), NY), datetime.combine(day, time(20), NY)
    if end > datetime.now(timezone.utc):
        raise ValueError("Labeler requires a completed historical session")
    return millis(start.isoformat()), millis(end.isoformat())


@contextmanager
def database():
    if not runtime_root().is_dir():
        raise RuntimeError("Required runtime root is unavailable")
    directory = project_runtime_root() / "chart-labeler"
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "labels.sqlite3", timeout=20)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS reviews (key TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS revisions (key TEXT, revision INTEGER, body TEXT NOT NULL, PRIMARY KEY(key, revision))")
        db.execute("CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        yield db


def scope_key(scope: Scope) -> str:
    return canonical(scope.model_dump(mode="json", exclude={"timeframe"}))


def empty_review(scope: Scope) -> dict:
    return {"scope": scope.model_dump(mode="json"), "revision": 0, "status": "unreviewed", "ranges": [], "evidence_ids": []}


class Interval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    direction: Literal["LONG", "SHORT"]
    annotation_timeframe: Literal["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"]
    entry_timestamp: str
    exit_timestamp: str
    entry_price: float = Field(gt=0, allow_inf_nan=False)
    exit_price: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if millis(self.entry_timestamp) >= millis(self.exit_timestamp):
            raise ValueError("Entry must precede exit")
        return self


class SaveReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Scope
    expected_revision: int = Field(ge=0)
    status: Literal["in_progress", "completed", "no_opportunity"]
    ranges: list[Interval] = Field(max_length=1000)
    evidence_ids: list[str] = Field(max_length=512)


def validate_review(request: SaveReview, evidence: list[dict]) -> dict:
    start, end = bounds(request.scope.session_date, request.scope.session)
    ordered = sorted(request.ranges, key=lambda r: millis(r.entry_timestamp))
    if len({r.id for r in ordered}) != len(ordered):
        raise ValueError("Duplicate range identity")
    if request.status == "no_opportunity" and ordered:
        raise ValueError("Remove ranges explicitly before marking no opportunity")
    if request.status == "completed" and not ordered:
        raise ValueError("Use no opportunity for an empty completed review")
    source_tokens = {e["source_token"] for e in evidence}
    identities = {e["instrument_id"] for e in evidence}
    if len(source_tokens) != 1 or len(identities) != 1:
        raise ValueError("Review requires one consistent source revision and instrument identity")
    opens, closes = {}, {}
    for item in evidence:
        if scope_key(Scope(**item["scope"])) != scope_key(request.scope):
            raise ValueError("Evidence belongs to another review scope")
        for candle in item["candles"]:
            opens[(item["scope"]["timeframe"], candle["start_ms"])] = candle["open"]
            closes[(item["scope"]["timeframe"], candle["end_ms"])] = candle["close"]
    previous_end = start
    for interval in ordered:
        a, b = millis(interval.entry_timestamp), millis(interval.exit_timestamp)
        if not start <= a < b <= end or a < previous_end:
            raise ValueError("Ranges must be inside the session and must not overlap")
        if opens.get((interval.annotation_timeframe, a)) != interval.entry_price or closes.get((interval.annotation_timeframe, b)) != interval.exit_price:
            raise ValueError("Endpoints must match certified candle open/close boundaries and prices")
        previous_end = b
    windows = sorted((e["start_ms"], e["end_ms"]) for e in evidence)
    cursor = start
    for a, b in windows:
        if a > cursor:
            break
        cursor = max(cursor, b)
    if request.status != "in_progress" and cursor != end:
        raise ValueError("Load the entire certified session before completing a review")
    if request.status != "in_progress" and not opens:
        raise ValueError("An empty chart cannot certify a negative review")
    return {"scope": request.scope.model_dump(mode="json"), "status": request.status,
            "ranges": [r.model_dump() for r in ordered], "evidence_ids": sorted(set(request.evidence_ids)),
            "source_token": next(iter(source_tokens)), "instrument_id": next(iter(identities)),
            "coverage_start": iso(start), "coverage_end": iso(end), "annotation_mode": "hindsight",
            "updated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/review")
def get_review(session_date: date, ticker: str, session: Literal["regular", "extended"] = "regular", timeframe: str = "1h", label_set: str = "default"):
    scope = Scope(session_date=session_date, ticker=ticker, session=session, timeframe=timeframe, label_set=label_set)
    with database() as db:
        row = db.execute("SELECT body FROM reviews WHERE key=?", (scope_key(scope),)).fetchone()
    return json.loads(row[0]) if row else empty_review(scope)


@router.put("/review")
def save_review(request: SaveReview):
    try:
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            key = scope_key(request.scope)
            current = db.execute("SELECT revision, body FROM reviews WHERE key=?", (key,)).fetchone()
            if (current[0] if current else 0) != request.expected_revision:
                if current and current[0] == request.expected_revision + 1:
                    previous = json.loads(current[1])
                    if (previous["status"] == request.status and previous["ranges"] == [r.model_dump() for r in sorted(request.ranges, key=lambda r: millis(r.entry_timestamp))]
                            and previous["evidence_ids"] == sorted(set(request.evidence_ids))):
                        return previous
                raise HTTPException(409, "Review changed in another editor. Reload before editing.")
            evidence = []
            for identity in set(request.evidence_ids):
                row = db.execute("SELECT body FROM evidence WHERE id=?", (identity,)).fetchone()
                if not row:
                    raise ValueError("Unknown chart evidence; reload the chart")
                evidence.append(json.loads(row[0]))
            body = validate_review(request, evidence)
            body["revision"] = request.expected_revision + 1
            encoded = canonical(body)
            db.execute("INSERT INTO revisions VALUES (?, ?, ?)", (key, body["revision"], encoded))
            db.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?)", (key, body["revision"], encoded))
        return body
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/universe")
def universe(session_date: date, session: Literal["regular", "extended"] = "regular", timeframe: str = "1h", label_set: str = "default"):
    from src.backend.historical_scanner_service import historical_scanner_reference_projection
    from src.backend.qmd_gateway_client import qmd_history_get_json
    try:
        start, end = bounds(session_date, session)
        as_of = datetime.fromisoformat(iso(end).replace("Z", "+00:00"))
        reference = historical_scanner_reference_projection(as_of)
        if not reference:
            raise RuntimeError("Historical tradable reference universe is unavailable")
        snapshot = qmd_history_get_json("/snapshot/scanner-market", {"start": iso(start), "end": iso(end), "as_of": iso(end)}, timeout=180)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("rows"), list):
            raise RuntimeError("Historical session market summary is unavailable")
        market_rows = snapshot["rows"]
        if len(market_rows) >= 20000:
            raise RuntimeError("Historical session summary reached the upstream row cap; no truncated universe published")
        metadata = {"source": "qmd_history_scanner_market", "start": iso(start), "end": iso(end),
                    "source_revision": snapshot.get("source_revision"), "change_basis": "session_first_to_last_trade", "volume_basis": "selected_session"}
        market = {r["symbol"]: r for r in market_rows}
        rows = []
        with database() as db:
            for ticker, facts in sorted(reference.items()):
                scope = Scope(session_date=session_date, session=session, ticker=ticker, timeframe=timeframe, label_set=label_set)
                saved = db.execute("SELECT body FROM reviews WHERE key=?", (scope_key(scope),)).fetchone()
                review = json.loads(saved[0]) if saved else empty_review(scope)
                market_row = market.get(ticker, {})
                rows.append({**market_row, **facts, "ticker": ticker,
                             "change_pct": market_row.get("change_pct"),
                             "review_status": review["status"], "range_count": len(review["ranges"])})
        return {"rows": rows, "start": iso(start), "end": iso(end), "as_of": iso(end), "market_provenance": metadata}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/chart")
def chart(session_date: date, ticker: str, session: Literal["regular", "extended"] = "regular", timeframe: str = "1h", label_set: str = "default", window: int = Query(default=0, ge=0, le=31)):
    from src.backend.qmd_gateway_client import QmdProductRequest, qmd_product_request, qmd_historical_source_revision
    from src.backend.historical_scanner_service import historical_scanner_reference_projection
    try:
        scope = Scope(session_date=session_date, ticker=ticker, session=session, timeframe=timeframe, label_set=label_set)
        start, end = bounds(session_date, session)
        window_ms = end - start if TIMEFRAMES[timeframe] >= 60000 else 1800000
        a, b = start + window * window_ms, min(end, start + (window + 1) * window_ms)
        if a >= end:
            raise ValueError("Window is outside the selected session")
        revision = qmd_historical_source_revision(start=iso(start), end=iso(end), tickers=[ticker])
        facts = historical_scanner_reference_projection(datetime.fromisoformat(iso(end).replace("Z", "+00:00")), tickers=(ticker,)).get(ticker, {})
        identity = facts.get("listing_id") or facts.get("symbol_id")
        if not identity:
            raise RuntimeError("Historical instrument identity is unavailable")
        result = qmd_product_request(QmdProductRequest("chart", authority="history", mode="backtest", ticker=ticker,
            timeframe=timeframe, start=iso(a), end=iso(b), as_of=iso(b), stage="bars", limit=20000,
            include_structure=False, include_market_signals=False, timeout_seconds=180))
        payload = result.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list) or payload.get("has_more") or result.complete is False:
            raise RuntimeError("Incomplete chart window; no evidence published")
        chart_revision = payload.get("source_revision") or (payload.get("cache") or {}).get("source_revision") or {}
        if not isinstance(chart_revision, dict) or chart_revision.get("request_complete") is not True or chart_revision.get("complete_for_history") is not True:
            raise RuntimeError("Chart response lacks certified historical source coverage")
        candles = []
        for bar in payload.get("bars", []):
            s = millis(bar["bar_start"])
            e = millis(bar["bar_end"])
            if not s < e or e - s != TIMEFRAMES[timeframe]:
                raise RuntimeError("Invalid candle boundary in canonical chart")
            if e <= a or s >= b:
                raise RuntimeError("Candle does not intersect requested window")
            if bar.get("is_closed") is False:
                raise RuntimeError("Unclosed historical candle")
            if float(bar.get("volume") or 0) <= 0:
                continue
            candles.append({"start_ms": s, "end_ms": e, **{k: bar[k] for k in ("open", "high", "low", "close", "volume")}})
        candles.sort(key=lambda c: c["start_ms"])
        if len({c["start_ms"] for c in candles}) != len(candles):
            raise RuntimeError("Duplicate canonical candle boundaries")
        after = qmd_historical_source_revision(start=iso(start), end=iso(end), tickers=[ticker])
        if revision["token"] != after["token"]:
            raise RuntimeError("Source changed during chart load; reload session")
        evidence = {"scope": scope.model_dump(mode="json"), "source_token": revision["token"], "instrument_id": str(identity),
                    "start_ms": a, "end_ms": b, "candles": candles, "provenance": revision,
                    "chart_provenance": {k: v for k, v in (payload.get("cache") or {}).items() if k != "hit"}, "excluded_empty_candles": len(payload["bars"]) - len(candles)}
        evidence_id = digest(evidence)
        with database() as db:
            db.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?)", (evidence_id, canonical(evidence)))
        return {**evidence, "evidence_id": evidence_id, "next_window": window + 1 if b < end else None,
                "window_count": (end - start + window_ms - 1) // window_ms}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def export_review(review: dict) -> dict:
    """Exact boundary events and exposure segments; no resampling or dense candle labels."""
    if review["status"] not in {"completed", "no_opportunity"}:
        raise ValueError("Only completed reviews can be exported")
    cursor = millis(review["coverage_start"])
    segments, events = [], []
    for interval in review["ranges"]:
        a, b = millis(interval["entry_timestamp"]), millis(interval["exit_timestamp"])
        if cursor < a:
            segments.append({"start": iso(cursor), "end": iso(a), "state": "WAIT"})
        segments.append({"start": iso(a), "end": iso(b), "state": interval["direction"]})
        events.extend([{"timestamp": iso(a), "event": "ENTER_" + interval["direction"], "range_id": interval["id"]},
                       {"timestamp": iso(b), "event": "EXIT_" + interval["direction"], "range_id": interval["id"]}])
        cursor = b
    if cursor < millis(review["coverage_end"]):
        segments.append({"start": iso(cursor), "end": review["coverage_end"], "state": "WAIT"})
    return {**review, "events": events, "segments": segments, "interval_convention": "[start,end)",
            "feature_cutoff": "strictly_before_event", "availability_mask_required": True}


@router.get("/export")
def export_labels(label_set: str = "default"):
    with database() as db:
        reviews = [json.loads(r[0]) for r in db.execute("SELECT body FROM reviews ORDER BY key")]
    selected = [r for r in reviews if r["scope"]["label_set"] == label_set]
    completed = [export_review(r) for r in selected if r["status"] in {"completed", "no_opportunity"}]
    return {"schema_version": 1, "annotation_mode": "hindsight", "label_set": label_set,
            "reviews": completed, "excluded_unfinished_reviews": len(selected) - len(completed), "content_hash": digest(completed)}
