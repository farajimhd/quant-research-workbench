"""Bounded QMD input preparation for the app's shared program actor.

Network packets are transport bounds, not rule evaluation blocks. All packets
of an explicit block are joined before evaluating its market-only Polars DAGs.
No history calculation, broker projection or alternative backtest is owned here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping
from threading import BoundedSemaphore

import polars as pl

from .qmd_columnar_history import CertifiedHistoryPin
from .qmd_columnar_history_transport import read_history_window_async, window_request, _owned_worker
from src.trading_runtime.columnar_rule_program import evaluate_rules
from src.trading_runtime.columnar_strategy_release import ProgramRelease


@dataclass(frozen=True)
class PreparedProgramScannerProjection:
    release_hash: str
    source_revision: str
    frame: pl.DataFrame
    clock_offsets: Mapping[datetime, tuple[int, int]]

    def frame_for(self, at):
        if at not in self.clock_offsets:
            raise ValueError("Program scanner clock is outside its prepared cohort")
        offset, count = self.clock_offsets[at]
        return self.frame.slice(offset, count)


def prepare_program_scanner_projection(block, population, release, *, start_at=None, stop_at=None):
    """Join certified ranking and prepared history once per evaluation block."""
    from .qmd_columnar_population import CertifiedPopulationWindow
    from src.market_engine.volume_rank_rules import select_volume_universe
    if (type(block) is not PreparedProgramBlock or type(population) is not CertifiedPopulationWindow
            or type(release) is not ProgramRelease or block.release_hash != release.header["content_hash"]):
        raise ValueError("Program scanner projection lacks its prepared release/population")
    ranked = select_volume_universe(population.rows, release.universe)
    if not ranked.equals(population.ranked):
        raise ValueError("Program scanner ranking differs from its immutable universe rule")
    clocks = block.market.select("boundary_at").unique().sort("boundary_at")
    if start_at is not None or stop_at is not None:
        if (not isinstance(start_at, datetime) or not isinstance(stop_at, datetime)
                or start_at.tzinfo is None or stop_at.tzinfo is None
                or start_at.utcoffset() is None or stop_at.utcoffset() is None
                or start_at.microsecond or stop_at.microsecond
                or not timedelta(0) < stop_at - start_at <= timedelta(seconds=256)
                or (stop_at - start_at) % timedelta(seconds=1)
                or clocks.is_empty()
                or clocks["boundary_at"].min() > start_at + timedelta(seconds=1)
                or clocks["boundary_at"].max() < stop_at):
            raise ValueError("Program scanner execution window lacks its complete prepared clocks")
        # Select execution clocks without trimming or reevaluating the feature
        # block. Warm-up remains part of the original market-only rule origin.
        start_at, stop_at = start_at.astimezone(timezone.utc), stop_at.astimezone(timezone.utc)
        clocks = clocks.filter((pl.col("boundary_at") > start_at) & (pl.col("boundary_at") <= stop_at))
        if clocks.height != (stop_at - start_at) // timedelta(seconds=1):
            raise ValueError("Program scanner execution window has missing prepared clocks")
    if population.price_eligible_count > 0 and clocks.join(
            population.rows.select("boundary_at").unique(), on="boundary_at", how="anti").height:
        raise ValueError("Program scanner lacks population coverage for a prepared clock")
    ranked = ranked.join(clocks, on="boundary_at", how="semi")
    if ranked.select(pl.struct("boundary_at", "ticker").is_duplicated().any()).item():
        raise ValueError("Program scanner repeats a ticker at one completed clock")
    keys = ["boundary_at", "listing_id"]
    if ranked.join(block.market.select(keys), on=keys, how="anti").height:
        raise ValueError("Program scanner lacks prepared history for a ranked listing")
    selected = ranked.select(*keys, "ticker", "rank", "prior_close_available_at", "adjusted_prior_close",
        pl.col("rolling_volume").alias("_scanner_rolling_volume"))
    frame = block.market.join(selected, on=keys, how="inner", validate="1:1")
    if ("rolling_volume" in frame.columns
            and frame.select((pl.col("rolling_volume") != pl.col("_scanner_rolling_volume")).any()).item()):
        raise ValueError("Program scanner and prepared history volume differ")
    frame = frame.with_columns(pl.col("reference_close").alias("last_price"),
        pl.col("available_at").alias("last_price_available_at"),
        pl.col("_scanner_rolling_volume").alias("rolling_share_volume"),
        pl.col("available_at").alias("rolling_share_volume_available_at"),
        pl.col("adjusted_prior_close").alias("opening_prior_regular_close"),
        pl.col("prior_close_available_at").alias("opening_prior_regular_close_available_at"),
        pl.col("rank").alias("liquidity_rank")).drop("_scanner_rolling_volume").sort(["boundary_at", "rank"])
    offsets = {at: (0, 0) for at in clocks["boundary_at"]}
    offset = 0
    for at, count in frame.group_by("boundary_at", maintain_order=True).len().iter_rows():
        offsets[at] = (offset, count)
        offset += count
    return PreparedProgramScannerProjection(block.release_hash, population.source_revision,
        frame, MappingProxyType(offsets))


async def publish_prepared_program_source_clock(controller, projection, publisher, *,
        at, catalogs, watchlist_runtime=None):
    """Use the app's configured stream resolver and typed publication queue."""
    from .replay_run_service import ReplayRunController
    from .signal_stream_runtime_service import SignalStreamRuntime
    from .signal_stream_typed_publication import TypedSignalPublicationQueue
    if (type(controller) is not ReplayRunController
            or type(projection) is not PreparedProgramScannerProjection
            or type(publisher) is not TypedSignalPublicationQueue):
        raise ValueError("Program source publication requires its native prepared scope")
    source, actor = controller._program_signal_runtime, controller._program_strategy
    revision = controller.definition.configuration_revision
    if (type(source) is not SignalStreamRuntime or actor is None
            or projection.release_hash != actor.program_release.header["content_hash"]
            or source._typed_scope != (revision["revision_id"], projection.source_revision)):
        raise ValueError("Program source projection differs from its owned configuration")
    candidates = projection.frame_for(at).to_dicts()
    assigned = {actor._listings[row.assignment_id]: row.ticker for row in actor.assignments()}
    if any(assigned.get(row["listing_id"]) != row["ticker"] for row in candidates):
        raise ValueError("Program source listing/ticker identity differs from controller assignments")
    cursor = source.typed_source_cursor()
    stage = await _owned_worker(source.stage_resolve, revision["payload"], candidates,
        as_of=at, watchlist_runtime=watchlist_runtime, data_fields_projected=False)
    if (controller._program_signal_runtime is not source or controller._program_strategy is not actor
            or controller.definition.configuration_revision is not revision or source.typed_source_cursor() != cursor):
        raise ValueError("Program source ownership changed during configured stream resolution")
    pending = source.submit_staged(stage, publisher, batch_sequence=cursor[0] + 1,
        previous_commit_hash=cursor[1], configuration_revision=revision["revision_id"],
        source_revision=projection.source_revision, catalogs=catalogs, allow_clock_only=True)
    await pending.wait_async()
    if (controller._program_signal_runtime is not source or controller._program_strategy is not actor
            or controller.definition.configuration_revision is not revision):
        raise ValueError("Program source ownership changed before acknowledged promotion")
    return source.promote_staged(pending)


@dataclass(frozen=True)
class PreparedProgramBlock:
    release_hash: str
    evaluation_block: str
    first_ordinal: int
    stop_ordinal: int
    history_hashes: Mapping[str, str]
    market: pl.DataFrame
    masks: pl.DataFrame
    _offsets: Mapping[str, int] = field(init=False, repr=False)

    def __post_init__(self):
        width = self.stop_ordinal - self.first_ordinal
        keys = ("listing_id", "evaluation_block", "observed_ordinal", "boundary_at")
        if (width < 1 or self.market.height != len(self.history_hashes) * width
                or self.masks.height != self.market.height
                or not self.market.select(keys).equals(self.masks.select(keys))):
            raise ValueError("Prepared program block has inconsistent market/mask coverage")
        groups = self.market.group_by("listing_id", maintain_order=True).len()
        listings = groups["listing_id"].to_list()
        if listings != sorted(self.history_hashes) or groups.filter(pl.col("len") != width).height:
            raise ValueError("Prepared program block has inconsistent listing intervals")
        object.__setattr__(self, "_offsets", MappingProxyType({listing: index * width
            for index, listing in enumerate(listings)}))

    def row_for(self, listing_id: str, ordinal: int):
        """O(1) completed-row lookup; never rescan a full block at each clock."""
        if type(ordinal) is not int or not self.first_ordinal <= ordinal < self.stop_ordinal:
            raise ValueError("Program row is outside its prepared evaluation block")
        index = self._offsets[listing_id] + ordinal - self.first_ordinal
        market, masks = self.market.row(index, named=True), self.masks.row(index, named=True)
        if any(row["listing_id"] != listing_id or row["observed_ordinal"] != ordinal
                or row["evaluation_block"] != self.evaluation_block for row in (market, masks)):
            raise ValueError("Prepared program row lost its exact clock/listing identity")
        return market, masks

    def ordinal_for(self, listing_id: str, at: datetime) -> int:
        """Locate an exact certified dense 1s clock, including cold readback."""
        first, _ = self.row_for(listing_id, self.first_ordinal)
        elapsed = at - first["boundary_at"]
        if elapsed < timedelta(0) or elapsed % timedelta(seconds=1):
            raise ValueError("Program completion is outside its exact dense clock")
        ordinal = self.first_ordinal + elapsed // timedelta(seconds=1)
        row, _ = self.row_for(listing_id, ordinal)
        if row["boundary_at"] != at:
            raise ValueError("Program completion differs from its prepared clock")
        return ordinal

    def boundary_for(self, release, population, *, listing_id, ticker, ordinal):
        from src.trading_runtime.columnar_program_coordinator import ProgramBoundary
        if self.release_hash != release.header["content_hash"]:
            raise ValueError("Program boundary release differs from prepared inputs")
        row, mask = self.row_for(listing_id, ordinal)
        policy = release.header
        stop_field = f"history.swing_low.{policy['swing_left']}.{policy['swing_right']}"
        if policy["swing_left"]:
            if stop_field not in row or stop_field + "__valid" not in row:
                raise ValueError("Program dispatch lacks its certified initial-stop field")
            stop = row[stop_field] if row[stop_field + "__valid"] else None
        else:
            stop = row["reference_close"] * (1 - policy["stop_fraction"])
        return ProgramBoundary(row["boundary_at"], row["available_at"], listing_id, ticker,
            row["reference_close"], population.is_ranked(listing_id, row["boundary_at"]),
            {stage: mask[stage] for stage in release.programs},
            {stage: mask[stage + "__valid"] for stage in release.programs}, stop)

    def completion_identity(self, *, run_id, assignment_id, source_hash,
            listing_id, ordinal, terminal):
        from src.trading_runtime.journal_contract import canonical_json
        if type(terminal) is not bool:
            raise ValueError("Program completion terminal flag must be typed")
        self.row_for(listing_id, ordinal)
        return sha256(canonical_json(dict(schema_version=1, run_id=run_id,
            assignment_id=assignment_id, source_commit_hash=source_hash,
            release_hash=self.release_hash, history_hash=self.history_hashes[listing_id],
            evaluation_block=self.evaluation_block, observed_ordinal=ordinal,
            terminal=terminal)).encode()).hexdigest()


async def prepare_program_block(release: ProgramRelease, pins: Mapping[str, CertifiedHistoryPin], *,
        first_ordinal: int, stop_ordinal: int, evaluation_block: str, as_of: datetime,
        concurrency: int, history_post=None) -> PreparedProgramBlock:
    """Read certified columns with a fixed worker pool and one explicit origin.

    The caller supplies the optimizer's complete evaluation block including its
    warm-up. Trimming or starting a block at an HTTP packet boundary is forbidden.
    The resulting masks are selected sequentially using actual filled state.
    """
    if (type(release) is not ProgramRelease or not pins
            or type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal
            or len(pins) * (stop_ordinal - first_ordinal) > 262144
            or type(concurrency) is not int or not 1 <= concurrency <= 32
            or type(evaluation_block) is not str or not 1 <= len(evaluation_block) <= 128):
        raise ValueError("Program input block exceeds its explicit preparation bounds")
    owned = dict(pins)
    jobs = []
    session = None
    for listing, pin in sorted(owned.items()):
        if type(pin) is not CertifiedHistoryPin or listing != pin.scope.listing_id:
            raise ValueError("Program input listing differs from its cold history pin")
        identity = (pin.scope.build_id, pin.scope.session_date, pin.parents.market_plan_hash,
                    pin.parents.opening_reference_hash, pin.scope.first_bucket,
                    pin.scope.product_revision)
        if session is not None and session != identity:
            raise ValueError("Program inputs differ in their certified session or clock origin")
        session = identity
        catalog = {field.name: field for field in pin.fields}
        for name, field in release.fields.items():
            if (name not in catalog or field.unit != catalog[name].unit
                    or field.product != "arte.qmd_causal_history_value_v1"
                    or field.revision != pin.scope.product_revision or field.resolution_ms != 1000):
                raise ValueError("Program field differs from its producer-owned history contract")
        packet_size = min(4096, 65536 // max(1, len(pin.fields)))
        for first in range(first_ordinal, stop_ordinal, packet_size):
            stop = min(first + packet_size, stop_ordinal)
            window_request(pin, first_ordinal=first, stop_ordinal=stop, as_of=as_of)
            jobs.append((listing, pin, first, stop))
    # All pins, field contracts and future-clock checks precede the first I/O.
    iterator = iter(enumerate(jobs))
    packets = [None] * len(jobs)
    async def worker():
        while (item := next(iterator, None)) is not None:
            index, (listing, pin, first, stop) = item
            packets[index] = await read_history_window_async(pin, first_ordinal=first,
                stop_ordinal=stop, evaluation_block=evaluation_block, as_of=as_of,
                history_post=history_post)
    workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(jobs)))]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    def evaluate():
        # One cumulative Float32 origin per listing/block, across every packet.
        market = pl.concat(packets).sort("listing_id", "evaluation_block", "observed_ordinal")
        masks = evaluate_rules(market, release.programs, release.fields)
        return market, masks
    market, masks = await _owned_worker(evaluate)
    return PreparedProgramBlock(release.header["content_hash"], evaluation_block,
        first_ordinal, stop_ordinal,
        MappingProxyType({listing: pin.header["content_hash"] for listing, pin in owned.items()}),
        market, masks)


@dataclass(frozen=True)
class PreparedProgramCohortInputs:
    blocks: Mapping
    population: object
    projection: PreparedProgramScannerProjection

    def __post_init__(self):
        object.__setattr__(self, "blocks", MappingProxyType(dict(self.blocks)))


def preflight_program_cohort_inputs(release, pins, population_pin, listing_by_assignment, *,
        first_ordinal, stop_ordinal, evaluation_block, first_bucket, stop_bucket,
        as_of, concurrency, history_post=None):
    """Check existing QMD source/block scope without transport or evaluation.

    The supplied feature origin includes its full warm-up. Only the supplied
    execution clocks require scanner rows. No producer, broker or run starts.
    """
    from .qmd_columnar_population import CertifiedPopulationPin
    if (type(release) is not ProgramRelease or type(population_pin) is not CertifiedPopulationPin
            or population_pin.rule != release.universe or not isinstance(pins, Mapping)
            or not isinstance(listing_by_assignment, Mapping) or not listing_by_assignment
            or any(type(key) is not str or not key or type(value) is not str or not value
                   for key, value in listing_by_assignment.items())
            or set(pins) != set(listing_by_assignment.values())
            or type(concurrency) is not int or not 1 <= concurrency <= 32
            or type(first_bucket) is not int or type(stop_bucket) is not int
            or not 14400 <= first_bucket < stop_bucket <= 72000 or stop_bucket - first_bucket > 256
            or type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal
            or len(pins) * (stop_ordinal - first_ordinal) > 262144
            or len(population_pin.histories) * (stop_bucket - first_bucket) > 262144
            or type(evaluation_block) is not str or not 1 <= len(evaluation_block) <= 128
            or history_post is not None and not callable(history_post)):
        raise ValueError("Programme preparation lacks its exact bounded source/block scope")
    owned, assignments = dict(pins), dict(listing_by_assignment)
    # Volume-only and feature products have independent catalog revisions and
    # attempts. Their clocks and certified causal parents must agree exactly.
    identity = ("build_id", "session_date", "listing_id", "ticker", "first_bucket", "stop_bucket")
    for listing, pin in owned.items():
        volume = population_pin.histories.get(listing)
        if (type(pin) is not CertifiedHistoryPin or volume is None
                or any(getattr(pin.scope, name) != getattr(volume.scope, name) for name in identity)
                or pin.parents != volume.parents
                or first_ordinal > first_bucket - pin.scope.first_bucket
                or stop_ordinal < stop_bucket - pin.scope.first_bucket):
            raise ValueError("Programme feature origin differs from its complete scanner lineage")
        catalog = {field.name: field for field in pin.fields}
        for name, field in release.fields.items():
            if (name not in catalog or field.unit != catalog[name].unit
                    or field.product != "arte.qmd_causal_history_value_v1"
                    or field.revision != pin.scope.product_revision or field.resolution_ms != 1000):
                raise ValueError("Programme field differs from its certified history contract")
        window_request(pin, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal, as_of=as_of)
    for pin in population_pin.histories.values():
        window_request(pin, first_ordinal=first_bucket - pin.scope.first_bucket,
            stop_ordinal=stop_bucket - pin.scope.first_bucket, as_of=as_of)
    return MappingProxyType(owned), MappingProxyType(assignments)


async def prepare_program_cohort_inputs(release, pins, population_pin, listing_by_assignment, *,
        first_ordinal, stop_ordinal, evaluation_block, first_bucket, stop_bucket,
        as_of, concurrency, history_post=None, feature_cache=None):
    """Use the same read-only preflight before bounded QMD/scanner preparation."""
    from .qmd_columnar_population import read_population_window
    from .qmd_gateway_client import qmd_history_post_json
    from .backtest_market_data import market_day_boundary
    from .backtest_program_feature_cache import ProgramFeatureBlockCache
    if feature_cache is not None and type(feature_cache) is not ProgramFeatureBlockCache:
        raise ValueError("Programme preparation requires its built-in feature cache")
    owned, assignments = preflight_program_cohort_inputs(release, pins, population_pin,
        listing_by_assignment, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal,
        evaluation_block=evaluation_block, first_bucket=first_bucket, stop_bucket=stop_bucket,
        as_of=as_of, concurrency=concurrency, history_post=history_post)
    # Both existing worker pools share this budget. Waiting occurs in owned
    # transport threads, never on the event loop or while holding a connection.
    budget = BoundedSemaphore(concurrency)
    def post(*args, **kwargs):
        with budget:
            return (history_post or qmd_history_post_json)(*args, **kwargs)
    async with asyncio.TaskGroup() as group:
        prepare_features = prepare_program_block if feature_cache is None else feature_cache.load
        features = group.create_task(prepare_features(release, owned,
            first_ordinal=first_ordinal, stop_ordinal=stop_ordinal,
            evaluation_block=evaluation_block, as_of=as_of, concurrency=concurrency, history_post=post))
        population = group.create_task(read_population_window(population_pin,
            first_bucket=first_bucket, stop_bucket=stop_bucket,
            as_of=as_of, concurrency=concurrency, history_post=post))
    block, population = features.result(), population.result()
    day = population_pin.opening.scope.session_date
    projection = await _owned_worker(prepare_program_scanner_projection, block, population, release,
        start_at=market_day_boundary(day, (first_bucket - 14400) * 1000),
        stop_at=market_day_boundary(day, (stop_bucket - 14400) * 1000))
    return PreparedProgramCohortInputs({identity: block for identity in assignments}, population, projection)


async def prepare_program_cohort_work(controller, pins, population_pin, *, scope,
        first_ordinal, stop_ordinal, evaluation_block, first_bucket, stop_bucket,
        as_of, concurrency, history_post=None):
    """Prepare native scheduler work without changing policy or execution state."""
    from .replay_run_service import ReplayRunController
    from .backtest_program_session import PreparedProgramCohortWork
    from .qmd_columnar_population import CertifiedPopulationPin
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    if type(controller) is not ReplayRunController:
        raise ValueError("Programme preparation requires its native controller")
    actor = controller._program_strategy
    if (type(actor) is not AssignedProgramStrategy or type(population_pin) is not CertifiedPopulationPin
            or population_pin.opening.scope.session_date != controller.definition.session_date):
        raise ValueError("Programme preparation lacks its installed actor or exact certified session")
    assignments = actor.assignments()
    listings = dict(actor._listings)
    release = actor.program_release
    # Validate scheduler scope shape before network requests. Execution
    # authorities are checked again by the existing launch/cohort gates.
    prototype = PreparedProgramCohortWork(first_bucket, stop_bucket, {}, None, None, scope)
    def require_owner():
        if (controller._program_strategy is not actor or actor.program_release is not release
                or actor.assignments() != assignments or actor._listings != listings
                or controller._runtime_finished or controller._program_dispatch_failure is not None):
            raise RuntimeError("Programme preparation authority changed during QMD reads")
    require_owner()
    from .backtest_program_feature_cache import ProgramFeatureBlockCache
    cache = getattr(controller, "_program_feature_cache", None)
    if cache is None:
        cache = controller._program_feature_cache = ProgramFeatureBlockCache()
    elif type(cache) is not ProgramFeatureBlockCache:
        raise ValueError("Controller programme preparation has a foreign feature cache")
    prepared = await prepare_program_cohort_inputs(release, pins, population_pin, listings,
        first_ordinal=first_ordinal, stop_ordinal=stop_ordinal, evaluation_block=evaluation_block,
        first_bucket=first_bucket, stop_bucket=stop_bucket, as_of=as_of,
        concurrency=concurrency, history_post=history_post, feature_cache=cache)
    require_owner()
    return PreparedProgramCohortWork(first_bucket, stop_bucket, prepared.blocks,
        prepared.population, prepared.projection, prototype.scope)
