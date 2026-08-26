from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Iterable

import torch

from research.bar_gpt.v3.data import BarView, rollup_calendar_view, rollup_intraday_view
from research.bar_gpt.v3.schema import FEATURE_NAMES


INTRADAY_VIEW_US = {
    "1s": 1_000_000,
    "5s": 5_000_000,
    "10s": 10_000_000,
    "30s": 30_000_000,
    "1m": 60_000_000,
    "5m": 300_000_000,
    "30m": 1_800_000_000,
    "1h": 3_600_000_000,
}
CALENDAR_VIEWS = ("1D", "1W", "1MO")


@dataclass(frozen=True, slots=True)
class RawBar:
    ticker: str
    view: str
    bar_start_us: int
    bar_end_us: int
    available_at_us: int
    values: tuple[float, ...]
    revision: int
    source: str
    source_revision: str = ""


class TickerCache:
    def __init__(self, capacities: dict[str, int], raw_capacity_1s: int, raw_capacity_1d: int) -> None:
        self.capacities = dict(capacities)
        self.raw_capacity_1s = max(raw_capacity_1s, capacities.get("1s", 1))
        self.raw_capacity_1d = max(raw_capacity_1d, capacities.get("1D", 1))
        self.rows: dict[str, OrderedDict[int, RawBar]] = {
            name: OrderedDict() for name in (*INTRADAY_VIEW_US, *CALENDAR_VIEWS)
        }
        self.max_available_at: dict[str, int] = {name: 0 for name in self.rows}
        self.last_source_revision = ""

    def upsert(self, bar: RawBar) -> str:
        self._validate(bar)
        status = self._merge(bar)
        self._order_and_bound(bar.view)
        return status

    def upsert_many(self, bars: Iterable[RawBar]) -> dict[str, int]:
        counts = {"appended": 0, "corrected": 0, "duplicate": 0}
        touched: set[str] = set()
        for bar in bars:
            self._validate(bar)
            status = self._merge(bar)
            counts[status] += 1
            if status != "duplicate":
                touched.add(bar.view)
        for view in touched:
            self._order_and_bound(view)
        return counts

    def _validate(self, bar: RawBar) -> None:
        if bar.view not in self.rows:
            raise ValueError(f"unsupported BarGPT view {bar.view!r}")
        if len(bar.values) != len(FEATURE_NAMES):
            raise ValueError(f"raw BarGPT bar must contain {len(FEATURE_NAMES)} features")
        if bar.bar_end_us <= bar.bar_start_us or bar.available_at_us < bar.bar_end_us:
            raise ValueError("bar timestamps violate start < end <= available_at")

    def _merge(self, bar: RawBar) -> str:
        target = self.rows[bar.view]
        prior = target.get(bar.bar_start_us)
        if prior is not None and bar.revision <= prior.revision:
            return "duplicate"
        target[bar.bar_start_us] = bar
        self.max_available_at[bar.view] = max(self.max_available_at[bar.view], bar.available_at_us)
        self.last_source_revision = bar.source_revision or self.last_source_revision
        return "corrected" if prior is not None else "appended"

    def _order_and_bound(self, view: str) -> None:
        target = self.rows[view]
        ordered = sorted(target.items())
        target.clear()
        target.update(ordered)
        capacity = (
            self.raw_capacity_1s if view == "1s"
            else self.raw_capacity_1d if view == "1D"
            else self.capacities[view]
        )
        while len(target) > capacity:
            target.popitem(last=False)

    def readiness_count(self, view: str, origin_us: int) -> int:
        target = self.rows[view]
        capacity = self.capacities[view]
        if origin_us >= self.max_available_at[view]:
            return min(len(target), capacity)
        return min(sum(row.available_at_us <= origin_us for row in target.values()), capacity)

    def rebuild_intraday_bucket(self, view: str, bar_start_us: int, origin_available_us: int) -> None:
        if view == "1s" or view not in INTRADAY_VIEW_US:
            return
        timeframe = INTRADAY_VIEW_US[view]
        candidates = {
            (origin_available_us // timeframe - 1) * timeframe,
            (bar_start_us // timeframe) * timeframe,
        }
        for bucket_start in sorted(candidates):
            if bucket_start + timeframe > origin_available_us:
                continue
            source = [
                row for row in self.rows["1s"].values()
                if bucket_start <= row.bar_start_us < bucket_start + timeframe
            ]
            if not source:
                continue
            rolled = rollup_intraday_view(_bar_view(source), timeframe)
            if rolled.features.shape[0] == 0:
                continue
            index = -1
            self.upsert(RawBar(
                ticker=source[-1].ticker,
                view=view,
                bar_start_us=int(rolled.bar_start_us[index]),
                bar_end_us=int(rolled.bar_end_us[index]),
                available_at_us=int(rolled.available_at_us[index]),
                values=tuple(float(value) for value in rolled.features[index].tolist()),
                revision=max(row.revision for row in source),
                source="derived:bar_gpt_service",
                source_revision=self.last_source_revision,
            ))

    def update_calendar(self, origin_available_us: int) -> None:
        daily = list(self.rows["1D"].values())
        if not daily:
            return
        dates = [
            datetime.fromtimestamp(row.bar_start_us / 1_000_000, tz=UTC).date()
            for row in daily
        ]
        base = _bar_view(daily)
        week_ids = torch.as_tensor([date.isocalendar().year * 100 + date.isocalendar().week for date in dates], dtype=torch.long)
        month_ids = torch.as_tensor([date.year * 100 + date.month for date in dates], dtype=torch.long)
        for name, identifiers in (("1W", week_ids), ("1MO", month_ids)):
            rolled = rollup_calendar_view(base, identifiers)
            target = self.rows[name]
            target.clear()
            self.max_available_at[name] = 0
            for index in range(rolled.features.shape[0]):
                if int(rolled.available_at_us[index]) > origin_available_us:
                    continue
                self.upsert(RawBar(
                    ticker=daily[-1].ticker,
                    view=name,
                    bar_start_us=int(rolled.bar_start_us[index]),
                    bar_end_us=int(rolled.bar_end_us[index]),
                    available_at_us=int(rolled.available_at_us[index]),
                    values=tuple(float(value) for value in rolled.features[index].tolist()),
                    revision=max(row.revision for row in daily),
                    source="derived:bar_gpt_service",
                    source_revision=self.last_source_revision,
                ))

    def model_rows(self, view: str, origin_us: int) -> list[RawBar]:
        capacity = self.capacities[view]
        values = [row for row in self.rows[view].values() if row.available_at_us <= origin_us]
        return values[-capacity:]


class CausalCache:
    def __init__(self, capacities: dict[str, int], raw_capacity_1s: int, raw_capacity_1d: int = 500) -> None:
        self.capacities = dict(capacities)
        self.raw_capacity_1s = int(raw_capacity_1s)
        self.raw_capacity_1d = int(raw_capacity_1d)
        self._tickers: dict[str, TickerCache] = {}
        self._lock = RLock()
        self.metrics = {"appended": 0, "corrected": 0, "duplicate": 0, "evicted_tickers": 0}
        self._health_summary: dict[str, object] = {
            "ticker_count": 0,
            "rows_by_view": {view: 0 for view in self.capacities},
            "estimated_feature_bytes": 0,
            "metrics": dict(self.metrics),
            "snapshot_stale": False,
        }

    def upsert(self, bar: RawBar) -> str:
        with self._lock:
            ticker = self._tickers.setdefault(
                bar.ticker, TickerCache(self.capacities, self.raw_capacity_1s, self.raw_capacity_1d)
            )
            status = ticker.upsert(bar)
            self.metrics[status] += 1
            if bar.view == "1s" and status != "duplicate":
                for view in INTRADAY_VIEW_US:
                    ticker.rebuild_intraday_bucket(view, bar.bar_start_us, bar.available_at_us)
            if bar.view == "1D" and status != "duplicate":
                ticker.update_calendar(bar.available_at_us)
            return status

    def upsert_many(self, bars: Iterable[RawBar], *, derive: bool = True) -> dict[str, int]:
        counts = {"appended": 0, "corrected": 0, "duplicate": 0}
        if derive:
            for bar in bars:
                counts[self.upsert(bar)] += 1
            return counts
        rows = list(bars)
        grouped: dict[str, list[RawBar]] = {}
        for bar in rows:
            grouped.setdefault(bar.ticker.upper(), []).append(bar)
        for ticker_name, ticker_rows in grouped.items():
            with self._lock:
                ticker = self._tickers.setdefault(
                    ticker_name, TickerCache(self.capacities, self.raw_capacity_1s, self.raw_capacity_1d)
                )
                ticker_counts = ticker.upsert_many(ticker_rows)
                for status, count in ticker_counts.items():
                    self.metrics[status] += count
                    counts[status] += count
        return counts

    def rows(self, ticker: str, view: str, origin_us: int) -> list[RawBar]:
        with self._lock:
            cache = self._tickers.get(ticker.upper())
            return [] if cache is None else cache.model_rows(view, origin_us)

    def snapshot_rows(self, ticker: str, *, views: Iterable[str] | None = None) -> list[RawBar]:
        """Return an immutable copy of retained source rows for a warm snapshot."""
        selected = tuple(views or (*INTRADAY_VIEW_US, *CALENDAR_VIEWS))
        with self._lock:
            cache = self._tickers.get(ticker.upper())
            if cache is None:
                return []
            return [row for view in selected for row in cache.rows[view].values()]

    def readiness(self, ticker: str, origin_us: int, minimum_1s: int) -> dict[str, object]:
        with self._lock:
            cache = self._tickers.get(ticker.upper())
            counts = {
                view: 0 if cache is None else cache.readiness_count(view, origin_us)
                for view in self.capacities
            }
        missing = [view for view, count in counts.items() if count == 0]
        ready = counts.get("1s", 0) >= minimum_1s and not missing
        return {"ticker": ticker.upper(), "ready": ready, "counts": counts, "missing_views": missing}

    def evict_except(self, active: set[str]) -> list[str]:
        active = {value.upper() for value in active}
        with self._lock:
            removed = sorted(set(self._tickers) - active)
            for ticker in removed:
                del self._tickers[ticker]
            self.metrics["evicted_tickers"] += len(removed)
            return removed

    def summary(self) -> dict[str, object]:
        with self._lock:
            return self._summary_unlocked(snapshot_stale=False)

    def health_summary(self) -> dict[str, object]:
        """Return control-plane cache evidence without waiting on data-plane admission."""
        if not self._lock.acquire(blocking=False):
            return {**self._health_summary, "snapshot_stale": True}
        try:
            return self._summary_unlocked(snapshot_stale=False)
        finally:
            self._lock.release()

    def _summary_unlocked(self, *, snapshot_stale: bool) -> dict[str, object]:
        rows = {
            view: sum(len(ticker.rows[view]) for ticker in self._tickers.values())
            for view in self.capacities
        }
        summary = {
            "ticker_count": len(self._tickers),
            "rows_by_view": rows,
            "estimated_feature_bytes": sum(rows.values()) * len(FEATURE_NAMES) * 4,
            "metrics": dict(self.metrics),
            "snapshot_stale": snapshot_stale,
        }
        self._health_summary = summary
        return dict(summary)


def _bar_view(rows: list[RawBar]) -> BarView:
    return BarView(
        features=torch.as_tensor([row.values for row in rows], dtype=torch.float32),
        bar_start_us=torch.as_tensor([row.bar_start_us for row in rows], dtype=torch.long),
        bar_end_us=torch.as_tensor([row.bar_end_us for row in rows], dtype=torch.long),
        available_at_us=torch.as_tensor([row.available_at_us for row in rows], dtype=torch.long),
    )
