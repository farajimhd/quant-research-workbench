"""Complete certified population projection for the shared watchlist scanner.

Market features remain QMD producer-owned. This consumer joins opening-known
references to sealed elapsed-volume clocks, with explicit bounded concurrent
transport. A missing listing is a coverage failure, never an inferred zero.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from types import MappingProxyType
from typing import Mapping

import polars as pl

from src.backend.qmd_opening_reference import CertifiedOpeningReference
from src.backend.qmd_columnar_history import CertifiedHistoryPin
from src.backend.qmd_columnar_history_transport import read_history_window_async
from src.market_engine.opening_price_reference import content_hash, NEW_YORK
from src.market_engine.volume_rank_rules import VolumeUniverseRule, select_volume_universe


@dataclass(frozen=True)
class CertifiedPopulationPin:
    opening: CertifiedOpeningReference
    histories: Mapping[str, CertifiedHistoryPin]
    rule: VolumeUniverseRule

    def __post_init__(self):
        if (type(self.opening) is not CertifiedOpeningReference
                or type(self.rule) is not VolumeUniverseRule):
            raise TypeError("Scanner population needs certified opening and universe contracts")
        eligible = {row["listing_id"]: row for row in self.opening.resolved_rows()
                    if self.rule.minimum_prior_close <= row["adjusted_prior_close"]
                    <= self.rule.maximum_prior_close}
        if set(self.histories) != set(eligible):
            raise ValueError("Scanner history pins differ from the complete price-eligible population")
        for listing, pin in self.histories.items():
            row = eligible[listing]
            if (type(pin) is not CertifiedHistoryPin or pin.fields
                    or pin.scope.listing_id != listing or pin.scope.ticker != row["ticker"]
                    or pin.scope.build_id != self.opening.scope.build_id
                    or pin.scope.session_date != self.opening.scope.session_date
                    or pin.parents.market_plan_hash != self.opening.scope.market_plan_hash
                    or pin.parents.opening_reference_hash != self.opening.content_hash
                    or pin.parents.volume_window_seconds != self.rule.window_seconds):
                raise ValueError("Scanner history pin differs from certified population lineage")
        object.__setattr__(self, "histories", MappingProxyType(dict(self.histories)))

    @property
    def source_revision(self):
        return content_hash(dict(opening=self.opening.content_hash,
            histories=sorted((listing, pin.header["content_hash"])
                             for listing, pin in self.histories.items()),
            window_seconds=self.rule.window_seconds, top_count=self.rule.top_count,
            minimum_prior_close=self.rule.minimum_prior_close,
            maximum_prior_close=self.rule.maximum_prior_close))


@dataclass(frozen=True)
class CertifiedVolumePopulationPin(CertifiedPopulationPin):
    """Same complete scanner authority over the shared volume-only QMD product."""
    reader_factory: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        from .qmd_volume_reader import CertifiedVolumePin
        if (type(self.opening) is not CertifiedOpeningReference
                or type(self.rule) is not VolumeUniverseRule or not callable(self.reader_factory)):
            raise TypeError('Volume population requires explicit native QMD authorities')
        eligible = {row['listing_id']: row for row in self.opening.resolved_rows()
            if self.rule.minimum_prior_close <= row['adjusted_prior_close'] <= self.rule.maximum_prior_close}
        if set(self.histories) != set(eligible):
            raise ValueError('Volume pins differ from the complete price-eligible population')
        for listing, pin in self.histories.items():
            row = eligible[listing]
            if (type(pin) is not CertifiedVolumePin or pin.scope.listing_id != listing
                    or pin.scope.ticker != row['ticker'] or pin.scope.build_id != self.opening.scope.build_id
                    or pin.scope.session_date != self.opening.scope.session_date
                    or pin.parents.market_plan_hash != self.opening.scope.market_plan_hash
                    or pin.parents.opening_reference_hash != self.opening.content_hash
                    or pin.parents.volume_window_seconds != self.rule.window_seconds):
                raise ValueError('Volume pin differs from certified population lineage')
        object.__setattr__(self, 'histories', MappingProxyType(dict(self.histories)))


def is_certified_population_pin(pin):
    return type(pin) in (CertifiedPopulationPin, CertifiedVolumePopulationPin)


async def certify_population_history_async(opening, rule, requests, *, reader_factory, concurrency,
        maximum_retained_bytes=256 * 1024 * 1024):
    """Load every eligible volume seal through the native bounded cold reader."""
    from .qmd_columnar_history import HistoryPinRequest, certify_history_pins_async
    from .qmd_volume_reader import VolumePinRequest, certify_volume_pins_async
    volume_only = bool(requests) and all(type(r) is VolumePinRequest for r in requests)
    if (type(opening) is not CertifiedOpeningReference or type(rule) is not VolumeUniverseRule
            or type(requests) is not tuple
            or not volume_only and any(type(r) is not HistoryPinRequest for r in requests)):
        raise TypeError('Population loading requires native opening, ranking and history requests')
    eligible = {row['listing_id']: row for row in opening.resolved_rows()
        if rule.minimum_prior_close <= row['adjusted_prior_close'] <= rule.maximum_prior_close}
    if (len(requests) != len(eligible)
            or {r.scope.listing_id for r in requests} != set(eligible)):
        raise ValueError('Population requests differ from the complete price-eligible population')
    for request in requests:
        row = eligible[request.scope.listing_id]
        if ((not volume_only and request.fields) or request.scope.ticker != row['ticker']
                or request.scope.build_id != opening.scope.build_id
                or request.scope.session_date != opening.scope.session_date
                or request.parents.market_plan_hash != opening.scope.market_plan_hash
                or request.parents.opening_reference_hash != opening.content_hash
                or request.parents.volume_window_seconds != rule.window_seconds):
            raise ValueError('Population request differs from its certified opening lineage')
    if not eligible:
        if (not callable(reader_factory) or type(concurrency) is not int or not 1 <= concurrency <= 6):
            raise ValueError('Population loading requires a bounded native reader')
        return CertifiedPopulationPin(opening, {}, rule)
    if volume_only:
        histories = await certify_volume_pins_async(reader_factory, requests, concurrency=concurrency,
            maximum_retained_bytes=maximum_retained_bytes)
        return CertifiedVolumePopulationPin(opening, histories, rule, reader_factory)
    histories = await certify_history_pins_async(reader_factory, requests, concurrency=concurrency)
    return CertifiedPopulationPin(opening, histories, rule)


@dataclass(frozen=True)
class CertifiedPopulationWindow:
    source_revision: str
    rows: pl.DataFrame
    ranked: pl.DataFrame
    listing_count: int
    price_eligible_count: int
    covered_empty_reference_count: int
    excluded_identity_count: int
    price_rejected_count: int
    _membership: Mapping = field(init=False, repr=False)

    def __post_init__(self):
        identities = self.ranked.select("boundary_at", "listing_id")
        if identities.select(pl.struct("boundary_at", "listing_id").is_duplicated().any()).item():
            raise ValueError("Scanner population repeated a ranked listing/clock")
        grouped = identities.group_by("boundary_at").agg(pl.col("listing_id"))
        object.__setattr__(self, "_membership", MappingProxyType({at: frozenset(listings)
            for at, listings in grouped.iter_rows()}))

    def is_ranked(self, listing_id: str, at: datetime):
        return listing_id in self._membership.get(at, ())

    def scanner_frame(self):
        """Registered flat field projection for the existing app scanner funnel."""
        return self.rows.rename({"adjusted_prior_close": "opening_prior_regular_close",
                                 "rolling_volume": "rolling_share_volume"})


async def read_population_window(pin: CertifiedPopulationPin, *, first_bucket: int,
        stop_bucket: int, as_of: datetime, concurrency: int, history_post=None):
    """Read bounded columns concurrently; reject the entire window on failure.

    Concurrency comes from the caller's admitted compute funnel. Only that many
    workers/tasks exist; there is no task per listing or independent background
    queue. Transport cancellation drains each owned blocking HTTP operation.
    """
    _preflight_population_window(pin, first_bucket=first_bucket, stop_bucket=stop_bucket,
        as_of=as_of, concurrency=concurrency)
    if type(pin) is CertifiedVolumePopulationPin:
        from .qmd_volume_reader import volume_window
        from .qmd_columnar_history_transport import _owned_worker
        def project():
            frames = {listing: _population_listing_frame(pin, listing,
                volume_window(history,
                    first_ordinal=first_bucket - history.scope.first_bucket,
                    stop_ordinal=stop_bucket - history.scope.first_bucket, as_of=as_of))
                for listing, history in sorted(pin.histories.items())}
            return _population_window(pin, frames)
        return await _owned_worker(project)
    iterator = iter(sorted(pin.histories.items()))
    results = {}
    async def worker():
        while True:
            item = next(iterator, None)
            if item is None:
                return
            listing, history = item
            frame = await read_history_window_async(history,
                first_ordinal=first_bucket - history.scope.first_bucket,
                stop_ordinal=stop_bucket - history.scope.first_bucket,
                evaluation_block="scanner-population", as_of=as_of, history_post=history_post)
            results[listing] = _population_listing_frame(pin, listing, frame)
    async with asyncio.TaskGroup() as group:
        for _ in range(min(concurrency, len(pin.histories))):
            group.create_task(worker())
    return _population_window(pin, results)


def _preflight_population_window(pin, *, first_bucket, stop_bucket, as_of, concurrency):
    if (not is_certified_population_pin(pin) or type(concurrency) is not int
            or not 1 <= concurrency <= 32 or type(first_bucket) is not int
            or type(stop_bucket) is not int or not 14400 <= first_bucket < stop_bucket <= 72000
            or stop_bucket - first_bucket > 4096
            or len(pin.histories) * (stop_bucket - first_bucket) > 262144
            or not isinstance(as_of, datetime) or as_of.tzinfo is None
            or as_of.utcoffset() is None):
        raise ValueError("Scanner population window exceeds its explicit clock/concurrency bounds")
    completed = datetime.combine(pin.opening.scope.session_date, time(4), NEW_YORK) + timedelta(seconds=stop_bucket - 14400)
    if completed > as_of:
        raise ValueError("Scanner population cannot read future completed clocks")
    # Preflight every request before the first network read, not inside workers.
    from src.backend.qmd_columnar_history_transport import window_request
    for history in pin.histories.values():
        if type(pin) is CertifiedVolumePopulationPin:
            from .qmd_volume_reader import preflight_volume_window
            preflight_volume_window(history, first_ordinal=first_bucket - history.scope.first_bucket,
                stop_ordinal=stop_bucket - history.scope.first_bucket, as_of=as_of)
        else:
            window_request(history, first_ordinal=first_bucket - history.scope.first_bucket,
                stop_ordinal=stop_bucket - history.scope.first_bucket, as_of=as_of)


def _population_listing_frame(pin, listing, frame):
    reference = pin.opening.rows_by_listing[listing]
    return frame.select("listing_id", "boundary_at", "available_at", "rolling_volume").with_columns(
        pl.lit(pin.histories[listing].scope.ticker).alias("ticker"),
        pl.lit(reference["available_at"], dtype=pl.Datetime("us", "UTC")).alias("prior_close_available_at"),
        pl.lit(reference["adjusted_prior_close"], dtype=pl.Float64).alias("adjusted_prior_close"),
        pl.lit(pin.rule.window_seconds, dtype=pl.UInt32).alias("volume_window_seconds"))


def _population_window(pin, results):
    schema = dict(listing_id=pl.String, boundary_at=pl.Datetime("us", "UTC"),
        available_at=pl.Datetime("us", "UTC"), rolling_volume=pl.Float64,
        ticker=pl.String, prior_close_available_at=pl.Datetime("us", "UTC"),
        adjusted_prior_close=pl.Float64, volume_window_seconds=pl.UInt32)
    rows = pl.concat([results[listing] for listing in sorted(results)]) if results else pl.DataFrame(schema=schema)
    ranked = select_volume_universe(rows, pin.rule)
    references = tuple(pin.opening.rows_by_listing.values())
    return CertifiedPopulationWindow(pin.source_revision, rows, ranked, len(references),
        len(pin.histories), sum(row["status"] == "covered_empty" for row in references),
        sum(row["status"] == "excluded_identity" for row in references),
        sum(row["status"] == "resolved" for row in references) - len(pin.histories))
