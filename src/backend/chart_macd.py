"""Calendar MACD chart source, derived only from canonical completed daily bars."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
REVISION = "chart-calendar-macd-v1"


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("MACD source timestamps must include a timezone")
    return result


def period_bounds(session: date, timeframe: str) -> tuple[datetime, datetime]:
    if timeframe == "1d":
        start, end = session, session
        return datetime.combine(start, time(4), NY), datetime.combine(end, time(20), NY)
    if timeframe == "1w":
        start = session - timedelta(days=session.weekday())
        end = start + timedelta(days=7)
    elif timeframe == "1mo":
        start = session.replace(day=1)
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    elif timeframe == "1y":
        start, end = date(session.year, 1, 1), date(session.year + 1, 1, 1)
    else:
        raise ValueError("MACD calendar timeframe must be 1d, 1w, 1mo, or 1y")
    return datetime.combine(start, time(), NY), datetime.combine(end, time(), NY)


def calendar_macd(payload: dict, timeframe: str, as_of: datetime) -> dict:
    """Seed once from the full canonical prefix, never the visible chart page.

    Daily prices are already adjusted by QMD through as_of. Aggregate those
    adjusted closes, rather than adjusting a week containing a split as a whole.
    EMA initialization matches QMD (first observed close; first signal is zero).
    """
    if payload.get("coverage_status") != "ready":
        raise ValueError(f"Canonical daily MACD source is {payload.get('coverage_status', 'unavailable')}")
    if payload.get("split_adjusted") is not True:
        raise ValueError("Canonical daily MACD requires an explicit split-adjusted price basis")
    periods: dict[datetime, tuple[datetime, float]] = {}
    seen: set[date] = set()
    previous: date | None = None
    for row in payload.get("bars", []):
        if row.get("bar_family") != "trade":
            continue
        session = date.fromisoformat(row["session_date"])
        if session in seen or (previous is not None and session <= previous):
            raise ValueError("Canonical daily MACD bars must be unique and ordered")
        seen.add(session)
        previous = session
        close = float(row["close"])
        if not isfinite(close) or close <= 0:
            raise ValueError("Invalid canonical daily MACD close")
        _, daily_end = period_bounds(session, "1d")
        if daily_end > as_of or timestamp(row["bar_end"]) > as_of or row.get("is_closed") is False:
            continue
        start, end = period_bounds(session, timeframe)
        # Current calendar period is previewed by the chart, never committed.
        if end <= as_of:
            periods[start] = (end, close)
    rows = []
    fast = slow = signal = None
    for start, (end, close) in sorted(periods.items()):
        fast = close if fast is None else 2 / 13 * close + 11 / 13 * fast
        slow = close if slow is None else 2 / 27 * close + 25 / 27 * slow
        line = fast - slow
        signal = line if signal is None else .2 * line + .8 * signal
        rows.append(dict(start=start.timestamp(), end=end.timestamp(), close=close, line=line,
                         signal=signal, fast=fast, slow=slow))
    if not rows:
        raise ValueError("No completed canonical periods are available for MACD")
    # Forming quotes may reuse this completed state until the next source close,
    # calendar rollover, or 04:00 split boundary. Never poll full daily history
    # at the intraday chart's tick rate.
    local_day = as_of.astimezone(NY).date()
    boundaries = [datetime.combine(local_day + timedelta(days=offset), time(hour), NY)
                  for offset in (0, 1) for hour in (0, 4, 20)]
    through = min(boundary.timestamp() for boundary in boundaries if boundary > as_of) - 1e-6
    return dict(rows=rows, through=through, splitAdjusted=True,
                adjustments=payload.get("split_adjustments", []), basisAsOf=as_of.timestamp(),
                provenance=dict(revision=REVISION, source=payload.get("source"),
                                seed="first canonical period since 1970-01-01",
                                seed_at=rows[0]["start"], daily_rows=len(seen), periods=len(rows)))


def load_calendar_macd(symbol: str, timeframe: str, as_of: str) -> dict:
    from src.backend.trading_runtime_service import _historical_gateway_get

    cursor = timestamp(as_of)
    if cursor > datetime.now(timezone.utc):
        raise ValueError("MACD as_of must not be in the future")
    # The canonical daily aggregate is small enough to read its entire prefix.
    # No page-local seed, retained flatfile, or fixed-length warmup approximation.
    payload = _historical_gateway_get(f"/snapshot/chart-macro-bars/{symbol}", {
        "timeframe": "1d", "start": "1970-01-01T00:00:00Z", "end": cursor.isoformat(),
        "as_of": cursor.isoformat(), "mode": "replay", "stage": "bars",
    }, timeout=30)
    return calendar_macd(payload, timeframe, cursor)
