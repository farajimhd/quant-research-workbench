"""Bounded research-only jobs reading the pinned canonical historical source."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone, timedelta
from math import isfinite
from threading import Lock
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict

from src.backend.qmd_gateway_client import qmd_history_base_url, qmd_product_request, QmdProductRequest
from src.market_engine.hindsight import PriceMacdLabels
from src.market_engine.historical_source import QmdHistoricalEventSource, _source_revision

router = APIRouter(prefix="/api/research/hindsight", tags=["hindsight research"])
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindsight-research")
_lock = Lock()
_jobs: dict[str, dict] = {}
# One bounded, immutable-after-completion candidate set. Every reuse probes the
# canonical revision; changing source data invalidates it.
_candidate_cache: dict = {}
_NY = ZoneInfo("America/New_York")


class HindsightRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticker: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")
    session_date: date
    lookback_seconds: float = Field(default=2, ge=0, le=30, allow_inf_nan=False)


def load_macd_intervals(ticker: str, start: datetime, end: datetime, progress, deadline: float,
                        *, window_hours: int = 1, workers: int = 4) -> tuple[list, list]:
    if window_hours not in (1, 2, 4, 8) or not 1 <= workers <= 4:
        raise ValueError("MACD pages must be 1/2/4/8 hours with 1..4 readers")
    intervals: list[tuple[float, float, str]] = []
    provenance = []
    cursor = start
    windows = []
    while cursor < end:
        windows.append(cursor)
        cursor = min(cursor + timedelta(hours=window_hours), end)

    def read_window(cursor):
        if monotonic() >= deadline:
            raise RuntimeError("MACD intervals exceeded the research time budget; no partial result published")
        chunk_end = min(cursor + timedelta(hours=window_hours), end)
        payload = qmd_product_request(QmdProductRequest(
            "chart", authority="history", mode="backtest", ticker=ticker, timeframe="1s",
            start=cursor.isoformat(), end=chunk_end.isoformat(), as_of=chunk_end.isoformat(),
            indicator_columns=("bar_start", "bar_end", "macd_line", "macd_signal"),
            stage="bars", include_structure=False, include_market_signals=False,
            limit=50_000, timeout_seconds=min(90, max(1, deadline - monotonic())))).payload
        # Eight hours cannot exceed 28,800 distinct 1s bars. Never accept truncation
        # or an incomplete indicator authority, even if quote reading succeeded.
        evidence = payload.get("indicator_provenance") or {}
        if payload.get("has_more") or not payload.get("indicators_available") or evidence.get("complete") is not True:
            raise RuntimeError("Canonical 1s MACD window is incomplete; no result published")
        return cursor, chunk_end, payload, evidence

    progress(stage="macd")
    opened = None
    open_direction = None
    # Independent bounded pages; consume chronologically so MACD state crosses
    # hour boundaries and seconds without trades without artificial closures.
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hindsight-macd") as readers:
        for cursor, chunk_end, payload, evidence in readers.map(read_window, windows):
            provenance.append(evidence)
            rows = sorted(payload.get("indicators", []), key=lambda row: row["bar_end"])
            for row in rows:
                timestamp = datetime.fromisoformat(row["bar_end"].replace("Z", "+00:00"))
                if not cursor < timestamp <= chunk_end:
                    raise RuntimeError("Canonical MACD bar falls outside its requested closed-bar window")
                t = timestamp.timestamp()
                line, signal = row.get("macd_line"), row.get("macd_signal")
                valid = line is not None and signal is not None and isfinite(line) and isfinite(signal)
                direction = ('long' if line > signal else 'short' if line < signal else None) if valid else None
                if direction != open_direction:
                    if opened is not None and opened < t:
                        intervals.append((opened, t, open_direction))
                    opened = t if direction else None
                    open_direction = direction
            progress(stage="macd", through=chunk_end.isoformat())
    if opened is not None and opened < end.timestamp():
        intervals.append((opened, end.timestamp(), open_direction))
    return intervals, provenance


async def calculate(request: HindsightRequest, progress=lambda **kwargs: None) -> dict:
    start = datetime.combine(request.session_date, time(4), _NY)
    end = datetime.combine(request.session_date, time(20), _NY)
    if end > datetime.now(timezone.utc):
        raise ValueError("Hindsight requires a completed 04:00–20:00 New York session")
    source = QmdHistoricalEventSource(qmd_history_base_url(), start=start, end=end,
                                    tickers=[request.ticker.upper()], batch_size=100_000,
                                    event_kinds=("trade",))
    optimizer = PriceMacdLabels()
    started = monotonic()
    cache_key = tuple(sorted(request.model_dump(mode="json", exclude={"lookback_seconds"}).items()))
    cached = _candidate_cache.get("value")
    cache_hit = False
    if cached and cached[0] == cache_key:
        current_revision = _source_revision(await asyncio.to_thread(source._read_page, None, None, 1))
        if current_revision == cached[1]:
            optimizer = cached[2]
            source.source_revision = current_revision
            cache_hit = True
    if not cache_hit:
        async for rows in source.stream_rows():
            for row in rows:
                optimizer.observe_payload(row)
            progress(stage="events", trades=optimizer.trades,
                     rejected_prices=sum(optimizer.reasons.values()), elapsed_seconds=monotonic() - started)
            if monotonic() - started > 600:
                raise RuntimeError("Session exceeds the 10-minute research budget; no partial result published")
        if len(optimizer.times) <= 3_000_000 and (source.source_revision or {}).get("complete_for_history") is True:
            _candidate_cache["value"] = (cache_key, source.source_revision, optimizer)
    # A certified session with no valid prices has zero labels, not
    # a data failure. Coverage failures already raise in the source reader.
    events_seconds = monotonic() - started
    intervals, macd_provenance = await asyncio.to_thread(load_macd_intervals, request.ticker.upper(), start, end, progress, started + 600)
    macd_seconds = monotonic() - started - events_seconds
    result = optimizer.result(intervals, request.lookback_seconds)
    selection_seconds = monotonic() - started - events_seconds - macd_seconds
    if monotonic() - started > 600:
        raise RuntimeError("Session exceeds the 10-minute research budget; no partial result published")
    return {**result,
            "parameters": request.model_dump(mode="json"), "macd_provenance": macd_provenance,
            "timing": {"events_seconds": events_seconds, "macd_seconds": macd_seconds, "selection_seconds": selection_seconds, "candidate_cache_hit": cache_hit},
            "ticker": request.ticker.upper(), "session_date": str(request.session_date),
            "start": start.isoformat(), "end": end.isoformat(),
            "algorithm": "price-macd-bidirectional-swings-v1", "hindsight_only": True,
            "objective": "Independent long low-to-high and short high-to-low price labels within completed-1s MACD intervals",
            "price_basis": "Canonical eligible trade prices; gross moves with no spread, liquidity, cost or execution-policy constraints",
            "source_revision": source.source_revision, "elapsed_seconds": monotonic() - started}


def _run(job_id: str, request: HindsightRequest):
    def update(**kwargs):
        with _lock:
            _jobs[job_id].update(kwargs)
    update(status="running")
    try:
        result = asyncio.run(calculate(request, update))
        update(status="completed", result=result)
    except Exception as exc:
        update(status="failed", error=str(exc))


@router.post("")
def start_hindsight(request: HindsightRequest):
    end = datetime.combine(request.session_date, time(20), _NY)
    if end > datetime.now(timezone.utc):
        raise HTTPException(422, "Select a completed historical session")
    key = tuple(sorted(request.model_dump(mode="json").items()))
    with _lock:
        # Only deduplicate active requests. Re-running revalidates source revision.
        for job in _jobs.values():
            if job["key"] == key and job["status"] in {"queued", "running"}:
                return {k: v for k, v in job.items() if k != "key"}
        if sum(j["status"] in {"queued", "running"} for j in _jobs.values()) >= 3:
            raise HTTPException(429, "Hindsight queue is full; retry when another session completes")
        while len(_jobs) >= 8:
            completed = next((k for k, j in _jobs.items() if j["status"] in {"completed", "failed"}), None)
            if completed is None:
                raise HTTPException(429, "Hindsight queue is full")
            del _jobs[completed]
        job_id = str(uuid4())
        job = {"id": job_id, "key": key, "status": "queued", "quotes": 0}
        _jobs[job_id] = job
        response = {k: v for k, v in job.items() if k != "key"}
        _pool.submit(_run, job_id, request)
        return response


@router.get("/{job_id}")
def hindsight_status(job_id: str):
    with _lock:
        if job_id not in _jobs:
            raise HTTPException(404, "Hindsight job expired; generate it again")
        return {k: v for k, v in _jobs[job_id].items() if k != "key"}
