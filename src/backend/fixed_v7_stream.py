"""Causal in-memory V7 over persisted completed one-second bars.

This consumes a typed prior-session seed and never queries or writes market
products. Structural projections are reused until the engine revision changes.
"""
from __future__ import annotations

from datetime import date, datetime
from math import prod
from typing import Any, Mapping, Sequence

from src.backend.swing_book_source import session_bounds
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS,
    iter_persisted_v7_seconds, market_day_boundary,
)
from src.backend.structural_v7_seed import CertifiedSeedPlan, load_seed, split_evidence
from src.market_engine.streaming_level_book import StreamingLevelBook, VERSION
from src.market_engine.v7_qmd import projection


class FixedV7Stream:
    def __init__(self, seed: Mapping[str, Any], *, ticker: str, session: date,
                 splits: Sequence[dict[str, Any]] = (), consume_seed: bool = False) -> None:
        start, end = session_bounds(session.isoformat())
        if float(seed["available_at"]) > start.timestamp():
            raise ValueError("V7 seed was not available at the session opening")
        if str(seed["session"]) >= session.isoformat():
            raise ValueError("V7 seed must precede the simulated session")
        factor = prod(float(item["split_from"]) / float(item["split_to"])
                      for item in splits)
        self.engine = StreamingLevelBook(dict(seed), ticker=ticker,
            session=session.isoformat(), start=start.timestamp(), end=end.timestamp(),
            split_factor=factor, split_evidence=splits, consume_prior=consume_seed)
        self._last_projection_revision = -1
        self._levels: list[dict[str, Any]] = []
        self._latest_completed_second = start.timestamp()

    def update_second(self, row: Mapping[str, Any], *, at: datetime) -> None:
        """Advance exactly one completed, valid 1s bar at its close boundary."""
        if at.tzinfo is None or int(row["resolution_ms"]) != 1_000:
            raise ValueError("V7 requires a timezone-aware completed 1s bar")
        stamp = at.timestamp()
        if stamp <= self._latest_completed_second:
            raise ValueError("V7 completed seconds must be strictly increasing")
        if int(row.get("price_valid") or 0) != 1 or int(row.get("extremes_valid") or 0) != 1:
            raise ValueError("V7 cannot fit an incomplete persisted second")
        scale = 10_000.0
        bar = {"t": stamp, "open": float(row["open_int"]) / scale,
               "high": float(row["high_int"]) / scale,
               "low": float(row["low_int"]) / scale,
               "close": float(row["close_int"]) / scale,
               "volume": float(row["volume"])}
        self.engine.update(bar, observed_at=stamp)
        self._latest_completed_second = stamp

    def context(self, *, as_of: datetime, price: float = 0.0) -> dict[str, Any]:
        """Return the latest causal structure, never advancing from future bars."""
        if as_of.tzinfo is None or not self.engine.start <= as_of.timestamp() <= self.engine.end:
            raise ValueError("V7 projection time is outside the session")
        if as_of.timestamp() < self._latest_completed_second:
            raise ValueError("V7 projection cannot rewind before its consumed bars")
        revision = getattr(self.engine, "_projection_revision", 0)
        if revision != self._last_projection_revision:
            self._levels = projection(self.engine, as_of.timestamp(), {}, False)["unified_levels"]
            self._last_projection_revision = revision
        from src.backend.experimental_structure_book import context as level_context
        return {
            **level_context({"unified_levels": self._levels}, price),
            "qmd_structure_unified_levels": self._levels,
            "qmd_structure_session_high": self.engine.hod,
            "qmd_level_book_version": VERSION,
            "v7_max_input_timestamp": self.engine.as_of,
            "v7_input_policy": self.engine.input_policy,
            "v7_seed_input_policy": self.engine.seed_input_policy,
        }


class FixedV7Cache:
    """Lazily catch up only ticker books that reach strategy evaluation."""

    def __init__(self, *, market_plan: CertifiedMarketDayPlan,
                 seed_plan: CertifiedSeedPlan, session: date, client: Any) -> None:
        if session.isoformat() not in market_plan.sessions or seed_plan.build_id != market_plan.build_id:
            raise ValueError("V7 seed and bar plans do not share the requested session/build")
        self.market_plan = market_plan
        self.session = session
        self.client = client
        self._coverage = {row["ticker"]: row for row in seed_plan.units
                          if row["backtest_session"] == session.isoformat()}
        expected_tickers = {unit.ticker for unit in market_plan.units
                            if unit.stage == "bars" and unit.session_date == session.isoformat()}
        if not expected_tickers or set(self._coverage) != expected_tickers:
            raise ValueError("V7 seed plan does not cover the certified ticker population")
        self._streams: dict[str, FixedV7Stream] = {}

    def has_stream(self, ticker: str) -> bool:
        """True after this session's private book has been loaded and caught up."""
        return ticker in self._streams

    def _boundary_ms(self, at: datetime) -> int:
        if at.tzinfo is None:
            raise ValueError("V7 Backtest boundary requires a timezone")
        start = market_day_boundary(self.session, 0)
        if at.astimezone(start.tzinfo).date() != self.session:
            raise ValueError("V7 Backtest boundary is outside the pinned session")
        elapsed = at - start
        boundary_ms = elapsed.days * 86_400_000 + elapsed.seconds * 1_000 + elapsed.microseconds // 1_000
        if elapsed.microseconds % 1_000 or not 0 <= boundary_ms <= 57_600_000:
            raise ValueError("V7 Backtest boundary is not a valid completed millisecond")
        return boundary_ms

    def advance_second(self, ticker: str, row: Mapping[str, Any], *, at: datetime) -> None:
        stream = self._streams.get(ticker)
        if stream is None:
            return
        if str(row.get("ticker") or "") != ticker:
            raise ValueError("V7 persisted second ticker changed")
        if int(row.get("price_valid") or 0) and int(row.get("extremes_valid") or 0):
            stream.update_second(row, at=at)

    def advance_seconds(self, rows: Sequence[Mapping[str, Any]], *, at: datetime) -> None:
        """Amortize one async handoff across all active books at a boundary."""
        for row in rows:
            self.advance_second(str(row.get("ticker") or ""), row, at=at)

    def context(self, ticker: str, *, as_of: datetime, price: float) -> dict[str, Any]:
        boundary_ms = self._boundary_ms(as_of)
        stream = self._streams.get(ticker)
        if stream is None:
            pinned = self._coverage.get(ticker)
            if pinned is None:
                raise ValueError("V7 seed ticker is outside the certified population")
            seed = load_seed(self.client, ticker=ticker, session=self.session, coverage=pinned)
            splits = split_evidence(self.client, ticker=ticker,
                                    seed_session=date.fromisoformat(seed["session"]),
                                    session=self.session)
            stream = FixedV7Stream(seed, ticker=ticker, session=self.session,
                                   splits=splits, consume_seed=True)
            for row in iter_persisted_v7_seconds(
                self.market_plan, session_date=self.session.isoformat(), ticker=ticker,
                through_boundary_ms=boundary_ms, client=self.client,
            ):
                if int(row.get("price_valid") or 0) and int(row.get("extremes_valid") or 0):
                    bar_at = market_day_boundary(
                        self.session,
                        (int(row["bucket_index"]) + 1) * 1_000 - SESSION_OPEN_OFFSET_MS,
                    )
                    stream.update_second(row, at=bar_at)
            self._streams[ticker] = stream
        return stream.context(as_of=as_of, price=price)
