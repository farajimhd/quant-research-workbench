"""Causal in-memory V7 over persisted completed one-second bars.

This consumes a typed prior-session seed and never queries or writes market
products. Structural projections are reused until the engine revision changes.
"""
from __future__ import annotations

from collections import deque
from datetime import date, datetime
from math import prod
from typing import Any, Callable, Mapping, Sequence

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
        self._strategy_one_revision = -1
        self._strategy_one_policy = ""
        self._strategy_one_rows: tuple[Mapping[str, Any], ...] = ()
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

    def _project_levels(self, as_of: datetime) -> None:
        if as_of.tzinfo is None or not self.engine.start <= as_of.timestamp() <= self.engine.end:
            raise ValueError("V7 projection time is outside the session")
        if as_of.timestamp() < self._latest_completed_second:
            raise ValueError("V7 projection cannot rewind before its consumed bars")
        revision = getattr(self.engine, "_projection_revision", 0)
        if revision != self._last_projection_revision:
            self._levels = projection(self.engine, as_of.timestamp(), {}, False)["unified_levels"]
            self._last_projection_revision = revision

    def context(self, *, as_of: datetime, price: float = 0.0) -> dict[str, Any]:
        """Return the latest causal structure, never advancing from future bars."""
        self._project_levels(as_of)
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

    def strategy_one_levels(self, *, as_of: datetime,
                            seed_policy: str) -> tuple[Mapping[str, Any], ...]:
        """Reuse validated geometry across unchanged V7 projection revisions."""
        from datetime import timezone
        from src.trading_runtime.strategy_one_v7 import admitted_v7_levels

        self._project_levels(as_of)
        revision = self._last_projection_revision
        if (revision != self._strategy_one_revision
                or seed_policy != self._strategy_one_policy):
            # Validate geometry at its latest input clock, not the caller's
            # possibly stale 100 ms boundary. Freshness is checked separately
            # so a new bar can revive unchanged geometry without refitting it.
            at = datetime.fromtimestamp(self.engine.as_of, timezone.utc)
            self._strategy_one_rows = admitted_v7_levels({
                "qmd_level_book_version": VERSION,
                "v7_input_policy": self.engine.input_policy,
                "v7_seed_input_policy": self.engine.seed_input_policy,
                "v7_max_input_timestamp": self.engine.as_of,
                "qmd_structure_unified_levels": self._levels,
            }, as_of=at, seed_policy=seed_policy)
            self._strategy_one_revision = revision
            self._strategy_one_policy = seed_policy
        now = as_of.timestamp()
        if not 0 < self.engine.as_of <= now:
            raise ValueError("Strategy 1 V7 input clock is invalid or future")
        return (self._strategy_one_rows if now - self.engine.as_of <= 1.000001
                else ())


class FixedV7Cache:
    """Lazily catch up only ticker books that reach strategy evaluation."""

    def __init__(self, *, market_plan: CertifiedMarketDayPlan,
                 seed_plan: CertifiedSeedPlan, session: date, client: Any,
                 observe_completed_second: Callable[[str, Mapping[str, Any], int], None] | None = None,
                 prefetch_horizon_ms: int = 0) -> None:
        if session.isoformat() not in market_plan.sessions or seed_plan.build_id != market_plan.build_id:
            raise ValueError("V7 seed and bar plans do not share the requested session/build")
        self.market_plan = market_plan
        self.session = session
        self.client = client
        if observe_completed_second is not None and not callable(observe_completed_second):
            raise TypeError("V7 completed-second observer must be callable")
        if (type(prefetch_horizon_ms) is not int or prefetch_horizon_ms < 0
                or prefetch_horizon_ms > 300_000
                or prefetch_horizon_ms % 1_000):
            raise ValueError("V7 lookahead buffer must be a bounded whole-second horizon")
        self._observe_completed_second = observe_completed_second
        self._prefetch_horizon_ms = prefetch_horizon_ms
        self._coverage = {row["ticker"]: row for row in seed_plan.units
                          if row["backtest_session"] == session.isoformat()}
        expected_tickers = {unit.ticker for unit in market_plan.units
                            if unit.stage == "bars" and unit.session_date == session.isoformat()}
        if not expected_tickers or set(self._coverage) != expected_tickers:
            raise ValueError("V7 seed plan does not cover the certified ticker population")
        self._streams: dict[str, FixedV7Stream] = {}
        self._last_loaded_second_ms: dict[str, int] = {}
        self._last_observed_second_ms: dict[str, int] = {}
        self._prefetched: dict[str, deque[Mapping[str, Any]]] = {}
        self._prefetched_through_ms: dict[str, int] = {}

    def has_stream(self, ticker: str) -> bool:
        """True after this session's private book has been loaded and caught up."""
        return ticker in self._streams

    @property
    def prefetches_seconds(self) -> bool:
        return self._prefetch_horizon_ms > 0

    def strategy_one_ready_without_read(self, ticker: str, *,
                                        as_of: datetime) -> bool:
        """Whether V7 projection at this boundary needs no ClickHouse catch-up.

        An existing stream may still need a new completed second. Equality,
        not merely membership, is necessary to keep that SELECT off the hot
        asyncio path without ever projecting a future-consumed book backward.
        """
        if ticker not in self._coverage:
            raise ValueError("V7 ticker is outside the certified seed population")
        completed_ms = self._boundary_ms(as_of) // 1_000 * 1_000
        return (ticker in self._streams
                and self._last_loaded_second_ms[ticker] == completed_ms)

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
        if self._prefetch_horizon_ms:
            raise RuntimeError("Prefetched V7 books must advance through their pinned buffer")
        if str(row.get("ticker") or "") != ticker:
            raise ValueError("V7 persisted second ticker changed")
        boundary_ms = self._boundary_ms(at)
        prior = self._last_loaded_second_ms[ticker]
        if boundary_ms <= prior or boundary_ms % 1_000:
            raise ValueError("V7 completed second duplicated or moved backward")
        if int(row.get("price_valid") or 0) and int(row.get("extremes_valid") or 0):
            stream.update_second(row, at=at)
        if self._observe_completed_second is not None:
            self._observe_completed_second(ticker, row, boundary_ms)
        self._last_observed_second_ms[ticker] = boundary_ms
        self._last_loaded_second_ms[ticker] = boundary_ms

    def advance_seconds(self, rows: Sequence[Mapping[str, Any]], *, at: datetime) -> None:
        """Amortize one async handoff across all active books at a boundary."""
        for row in rows:
            self.advance_second(str(row.get("ticker") or ""), row, at=at)

    def catch_up_seconds(self, tickers: Sequence[str], *, at: datetime) -> None:
        """Consume buffered bars for already-loaded books at this completed clock."""
        if not self._prefetch_horizon_ms:
            raise RuntimeError("V7 buffered catch-up needs an explicit prefetch horizon")
        for ticker in tickers:
            if ticker not in self._streams:
                raise ValueError("V7 catch-up cannot create an unactivated book")
            self._stream(ticker, as_of=at)

    def _stream(self, ticker: str, *, as_of: datetime) -> FixedV7Stream:
        boundary_ms = self._boundary_ms(as_of)
        completed_ms = boundary_ms // 1_000 * 1_000
        stream = self._streams.get(ticker)
        after_ms = 0
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
        else:
            after_ms = self._last_loaded_second_ms[ticker]
        def consume(row: Mapping[str, Any]) -> None:
            if str(row.get("ticker") or "") != ticker:
                raise ValueError("V7 catch-up changed ticker scope")
            second_ms = ((int(row["bucket_index"]) + 1) * 1_000
                         - SESSION_OPEN_OFFSET_MS)
            if (second_ms <= self._last_observed_second_ms.get(ticker, 0)
                    or second_ms > completed_ms or second_ms % 1_000):
                raise ValueError("V7 completed second duplicated or crossed its causal clock")
            bar_at = market_day_boundary(self.session, second_ms)
            if int(row.get("price_valid") or 0) and int(row.get("extremes_valid") or 0):
                stream.update_second(row, at=bar_at)
            if self._observe_completed_second is not None:
                self._observe_completed_second(ticker, row, second_ms)
            self._last_observed_second_ms[ticker] = second_ms

        if completed_ms > after_ms and self._prefetch_horizon_ms:
            buffered = self._prefetched.setdefault(ticker, deque())
            while buffered:
                second_ms = ((int(buffered[0]["bucket_index"]) + 1) * 1_000
                             - SESSION_OPEN_OFFSET_MS)
                if second_ms > completed_ms:
                    break
                consume(buffered.popleft())
            through = self._prefetched_through_ms.get(ticker, after_ms)
            if completed_ms > through:
                next_through = min(57_600_000,
                                   completed_ms + self._prefetch_horizon_ms)
                prior_bucket = ((through + SESSION_OPEN_OFFSET_MS) // 1_000 - 1)
                for row in iter_persisted_v7_seconds(
                    self.market_plan, session_date=self.session.isoformat(),
                    ticker=ticker, after_boundary_ms=through,
                    through_boundary_ms=next_through, client=self.client,
                ):
                    bucket = int(row["bucket_index"])
                    if bucket <= prior_bucket:
                        raise ValueError("V7 prefetched seconds are not unique and ordered")
                    prior_bucket = bucket
                    second_ms = ((bucket + 1) * 1_000
                                 - SESSION_OPEN_OFFSET_MS)
                    if second_ms <= completed_ms:
                        consume(row)
                    else:
                        if (str(row.get("ticker") or "") != ticker
                                or second_ms > next_through
                                or buffered and int(buffered[-1]["bucket_index"])
                                >= int(row["bucket_index"])):
                            raise ValueError("V7 prefetched seconds are not unique and ordered")
                        buffered.append(row)
                self._prefetched_through_ms[ticker] = next_through
        elif completed_ms > after_ms:
            for row in iter_persisted_v7_seconds(
                self.market_plan, session_date=self.session.isoformat(), ticker=ticker,
                after_boundary_ms=after_ms, through_boundary_ms=completed_ms,
                client=self.client,
            ):
                consume(row)
        self._streams[ticker] = stream
        self._last_loaded_second_ms[ticker] = completed_ms
        return stream

    def context(self, ticker: str, *, as_of: datetime, price: float) -> dict[str, Any]:
        return self._stream(ticker, as_of=as_of).context(as_of=as_of, price=price)

    def strategy_one_levels(self, ticker: str, *, as_of: datetime) -> tuple[Mapping[str, Any], ...]:
        """Admit only geometry matching this ticker's certified seed policy."""
        from src.market_engine.derived_trade_policy import POLICY

        pinned = self._coverage.get(ticker)
        if pinned is None:
            raise ValueError("Strategy 1 V7 ticker lacks pinned prior coverage")
        policy = str(pinned["input_policy"]) if int(pinned["level_count"]) else POLICY
        return self._stream(ticker, as_of=as_of).strategy_one_levels(
            as_of=as_of, seed_policy=policy)
