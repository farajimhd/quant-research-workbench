"""Read-only complete-session planning over existing certified QMD cohorts."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType

from .backtest_program_inputs import preflight_program_cohort_inputs


@dataclass(frozen=True, slots=True)
class ProgramCohortInputPlan:
    pins: object
    population_pin: object
    listing_by_assignment: object
    first_ordinal: int
    stop_ordinal: int
    evaluation_block: str
    first_bucket: int
    stop_bucket: int
    as_of: datetime
    concurrency: int
    maximum_retained_feature_bytes: int | None = None
    maximum_retained_population_bytes: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "pins", MappingProxyType(dict(self.pins)))
        object.__setattr__(self, "listing_by_assignment", MappingProxyType(dict(self.listing_by_assignment)))


@dataclass(frozen=True, slots=True)
class ProgramPublishedInputRequest:
    """Published source identities and explicit existing compute-funnel bounds."""
    opening: object
    feature_requests: tuple
    volume_requests: tuple
    first_ordinal: int
    stop_ordinal: int
    evaluation_block: str
    cohort_windows: tuple
    as_of: datetime
    concurrency: int
    maximum_retained_feature_bytes: int | None = None
    maximum_retained_population_bytes: int | None = None


async def certify_program_session_input_plan(controller, request, *, reader_factory):
    """Cold load central seals before the app may attach any journal writer."""
    from .replay_run_service import ReplayRunController
    from .qmd_opening_reference import CertifiedOpeningReference
    from .qmd_columnar_history import HistoryPinRequest, certify_history_pins_async
    from .qmd_columnar_population import certify_population_history_async
    from .backtest_program_inputs import required_program_history_fields, preflight_program_feature_catalog
    if (type(controller) is not ReplayRunController or controller._program_strategy is None
            or type(request) is not ProgramPublishedInputRequest
            or type(request.opening) is not CertifiedOpeningReference
            or not callable(reader_factory) or type(request.concurrency) is not int
            or not 1 <= request.concurrency <= 6
            or type(request.feature_requests) is not tuple or not request.feature_requests
            or type(request.volume_requests) is not tuple
            or any(type(r) is not HistoryPinRequest for r in request.feature_requests + request.volume_requests)
            or type(request.cohort_windows) is not tuple or not request.cohort_windows
            or len(request.cohort_windows) > 57600
            or type(request.first_ordinal) is not int or type(request.stop_ordinal) is not int
            or not 0 <= request.first_ordinal < request.stop_ordinal
            or type(request.evaluation_block) is not str or not request.evaluation_block
            or not isinstance(request.as_of, datetime) or request.as_of.tzinfo is None
            or request.as_of.utcoffset() is None
            or (request.maximum_retained_feature_bytes is not None and
                (type(request.maximum_retained_feature_bytes) is not int or request.maximum_retained_feature_bytes < 1))
            or (request.maximum_retained_population_bytes is not None and
                (type(request.maximum_retained_population_bytes) is not int or request.maximum_retained_population_bytes < 1))):
        raise ValueError('Published programme inputs lack explicit native source and compute bounds')
    release = controller._program_strategy.program_release
    eligible = {row['listing_id']: row for row in request.opening.resolved_rows()
        if release.universe.minimum_prior_close <= row['adjusted_prior_close'] <= release.universe.maximum_prior_close}
    for packet in (request.feature_requests, request.volume_requests):
        if len(packet) != len(eligible) or {r.scope.listing_id for r in packet} != set(eligible):
            raise ValueError('Published programme inputs differ from the complete eligible population')
        for source in packet:
            if (source.scope.ticker != eligible[source.scope.listing_id]['ticker']
                    or source.scope.build_id != request.opening.scope.build_id
                    or source.scope.session_date != controller.definition.session_date
                    or source.scope.session_date != request.opening.scope.session_date
                    or source.parents.market_plan_hash != request.opening.scope.market_plan_hash
                    or source.parents.opening_reference_hash != request.opening.content_hash
                    or source.parents.volume_window_seconds != release.universe.window_seconds):
                raise ValueError('Published programme inputs differ from their opening lineage')
    required = set(required_program_history_fields(release))
    first_source = request.feature_requests[0].scope
    for source in request.feature_requests:
        if (not required <= {f.name for f in source.fields}
                or source.scope.first_bucket != first_source.first_bucket
                or source.scope.stop_bucket != first_source.stop_bucket
                or request.stop_ordinal > source.scope.clock_count
                or any(field.revision != source.scope.product_revision for field in release.fields.values())):
            raise ValueError('Published programme features differ from their release or evaluation origin')
    if any(source.fields or source.scope.first_bucket != first_source.first_bucket
            or source.scope.stop_bucket != first_source.stop_bucket for source in request.volume_requests):
        raise ValueError('Published programme volume clocks differ from their feature origin')
    previous = None
    for window in request.cohort_windows:
        if (type(window) is not tuple or len(window) != 2
                or any(type(value) is not int for value in window)
                or not 14400 <= window[0] < window[1] <= 72000
                or window[1] - window[0] > 256 or (previous is not None and window[0] != previous)):
            raise ValueError('Published programme cohorts have a gap, overlap or invalid clock bound')
        previous = window[1]
    from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
    day_start = market_day_boundary(controller.definition.session_date.isoformat(), 0)
    start_ms = (controller.definition.requested_start - day_start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    end_ms = (controller.definition.session_end - day_start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    if (start_ms % 1000 or end_ms % 1000
            or request.cohort_windows[0][0] != int(start_ms // 1000)
            or request.cohort_windows[-1][1] != int(end_ms // 1000)):
        raise ValueError('Published programme cohorts differ from the requested session window')
    population = await certify_population_history_async(request.opening, release.universe,
        request.volume_requests, reader_factory=reader_factory, concurrency=request.concurrency)
    pins = await certify_history_pins_async(reader_factory, request.feature_requests, concurrency=request.concurrency)
    preflight_program_feature_catalog(release, pins)
    cohorts = tuple(ProgramCohortInputPlan(pins, population, controller._program_strategy._listings,
        request.first_ordinal, request.stop_ordinal, request.evaluation_block, first, stop,
        request.as_of, request.concurrency, request.maximum_retained_feature_bytes,
        request.maximum_retained_population_bytes)
        for first, stop in request.cohort_windows)
    return preflight_program_session_inputs(release, cohorts,
        first_bucket=cohorts[0].first_bucket, stop_bucket=cohorts[-1].stop_bucket)


def preflight_program_session_inputs(release, cohorts, *, first_bucket, stop_bucket):
    """Verify exact coverage before any transport, calculation or publication.

    This is an input-plan proof only. Broker, source publication, configuration,
    ownership and run admission remain the existing separate launch gates.
    """
    if (type(cohorts) is not tuple or not cohorts or len(cohorts) > 57600
            or type(first_bucket) is not int or type(stop_bucket) is not int
            or not 14400 <= first_bucket < stop_bucket <= 72000):
        raise ValueError("Programme input plan requires its bounded complete session")
    expected, common = first_bucket, None
    for cohort in cohorts:
        if type(cohort) is not ProgramCohortInputPlan or cohort.first_bucket != expected:
            raise ValueError("Programme input plan has a gap, overlap or unordered cohort")
        if (cohort.maximum_retained_feature_bytes is not None and
                (type(cohort.maximum_retained_feature_bytes) is not int or cohort.maximum_retained_feature_bytes < 1)):
            raise ValueError("Programme input plan requires its explicit retained-byte budget")
        if (cohort.maximum_retained_population_bytes is not None and
                (type(cohort.maximum_retained_population_bytes) is not int or cohort.maximum_retained_population_bytes < 1)):
            raise ValueError('Programme input plan requires its explicit population byte budget')
        pins, assignments = preflight_program_cohort_inputs(release, cohort.pins, cohort.population_pin,
            cohort.listing_by_assignment, first_ordinal=cohort.first_ordinal,
            stop_ordinal=cohort.stop_ordinal, evaluation_block=cohort.evaluation_block,
            first_bucket=cohort.first_bucket, stop_bucket=cohort.stop_bucket,
            as_of=cohort.as_of, concurrency=cohort.concurrency,
            listing_partitioned=cohort.maximum_retained_feature_bytes is not None)
        identity = (cohort.population_pin.opening.scope.session_date,
            cohort.population_pin.source_revision, tuple(sorted(assignments.items())),
            tuple(sorted((listing, pin.header["content_hash"]) for listing, pin in pins.items())),
            cohort.first_ordinal, cohort.evaluation_block, cohort.maximum_retained_feature_bytes,
            cohort.maximum_retained_population_bytes)
        if common is not None and identity != common:
            raise ValueError("Programme input plan changed its source, assignments or evaluation origin")
        common, expected = identity, cohort.stop_bucket
    if expected != stop_bucket:
        raise ValueError("Programme input plan does not cover the requested session end")
    return cohorts


async def prepare_program_session_input_plan(controller, cohorts, *, scopes, history_post=None):
    """Prepare validated session work through the native controller, without launch."""
    from .replay_run_service import ReplayRunController, ReplayRunDefinition, RunMode
    from .backtest_program_session import PreparedProgramCohortWork, validate_prepared_program_session
    from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
    if type(controller) is not ReplayRunController or controller._program_strategy is None:
        raise ValueError("Programme input-plan preparation requires its native controller")
    definition, actor = controller.definition, controller._program_strategy
    release = actor.program_release
    if (type(definition) is not ReplayRunDefinition or definition.mode is not RunMode.BACKTEST
            or definition.execution_mode != "strategy"):
        raise ValueError("Programme input-plan preparation requires its Backtest strategy definition")
    if definition.final_session_date not in (None, definition.session_date):
        raise ValueError("Programme session input plan requires one explicit session")
    start = market_day_boundary(definition.session_date.isoformat(), 0)
    first_ms = (definition.requested_start - start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    stop_ms = (definition.session_end - start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    if first_ms % 1000 or stop_ms % 1000:
        raise ValueError("Programme session input plan requires whole-second endpoints")
    plans = preflight_program_session_inputs(release, cohorts,
        first_bucket=int(first_ms // 1000), stop_bucket=int(stop_ms // 1000))
    if type(scopes) is not tuple or len(scopes) != len(plans):
        raise ValueError("Programme input plan requires every native cohort scope")
    prototypes = tuple(PreparedProgramCohortWork(plan.first_bucket, plan.stop_bucket, {}, None, None, scope)
                       for plan, scope in zip(plans, scopes, strict=True))
    if any(dict(plan.listing_by_assignment) != actor._listings for plan in plans):
        raise ValueError("Programme input plan differs from controller assignment coverage")
    for prototype in prototypes:
        preflight_program_liquidity_scope(controller, prototype)
    prepared = []
    for plan, prototype in zip(plans, prototypes, strict=True):
        if (controller.definition is not definition or controller._program_strategy is not actor
                or actor.program_release is not release):
            raise RuntimeError("Programme input-plan controller ownership changed")
        work = await controller.prepare_program_cohort_work(plan.pins, plan.population_pin,
            scope=prototype.scope, first_ordinal=plan.first_ordinal, stop_ordinal=plan.stop_ordinal,
            evaluation_block=plan.evaluation_block, first_bucket=plan.first_bucket,
            stop_bucket=plan.stop_bucket, as_of=plan.as_of, concurrency=plan.concurrency,
            history_post=history_post, maximum_retained_feature_bytes=plan.maximum_retained_feature_bytes,
            maximum_retained_population_bytes=plan.maximum_retained_population_bytes)
        if (controller.definition is not definition or controller._program_strategy is not actor
                or actor.program_release is not release):
            raise RuntimeError("Programme input-plan controller ownership changed")
        prepared.append(work)
    work, _, _ = validate_prepared_program_session(controller, tuple(prepared))
    return work


def preflight_program_liquidity_scope(controller, work):
    """Prove native liquidity requests without opening a reader or producing data."""
    from .backtest_market_data import (CertifiedMarketDayPlan, project_market_day_plan,
        market_day_source_sqls, SESSION_OPEN_OFFSET_MS)
    from .backtest_liquidity_price import PriceLevelPlan
    from .backtest_liquidity_resolution import CertifiedLiquidityResolution, liquidity_window_sql
    scope = work.scope
    from src.trading_runtime.configuration_execution_binding import ConfigurationExecutionBinding
    execution = scope['execution_policy']
    approved = controller.definition.configuration_revision.get('execution_binding_hash')
    if type(execution) is ConfigurationExecutionBinding or approved is not None:
        revision = controller.definition.configuration_revision
        if (type(execution) is not ConfigurationExecutionBinding
                or execution.header['content_hash'] != approved
                or execution.header['configuration_hash'] != revision.get('selection_hash')
                or revision.get('revision_id') != 'strategy-configuration:' + execution.header['configuration_id']):
            raise ValueError('Programme execution binding differs from its approved configuration')
    concurrency = scope["concurrency"]
    if (not callable(scope["reader_factory"]) or type(concurrency) is not int
            or not 1 <= concurrency <= 32):
        raise ValueError("Programme liquidity scope lacks its bounded native reader")
    tickers = {assignment.ticker for assignment in controller._program_strategy.assignments()}
    day = controller.definition.session_date
    market, prices = scope.get("market_plan"), scope.get("price_plan")
    certificates = scope.get("liquidity_certificates")
    if controller._program_liquidity_resolution_ms == 100:
        if (certificates is not None or type(market) is not CertifiedMarketDayPlan
                or type(prices) is not PriceLevelPlan or market.sessions != (day.isoformat(),)
                or set(market.tickers) != tickers or len(market.tickers) != len(tickers)
                or market.required_resolutions_ms != (100,)):
            raise ValueError("Programme liquidity scope differs from certified100ms assignments")
        for ticker in market.tickers:
            projected = project_market_day_plan(market, (ticker,))
            market_day_source_sqls(projected,
                after_boundary_ms=work.first_bucket * 1000 - SESSION_OPEN_OFFSET_MS,
                through_boundary_ms=work.stop_bucket * 1000 - SESSION_OPEN_OFFSET_MS,
                price_plan=prices.projected(projected))
    else:
        if (controller._program_liquidity_resolution_ms != 1000 or market is not None
                or prices is not None or not isinstance(certificates, (tuple, list))
                or any(type(row) is not CertifiedLiquidityResolution for row in certificates)
                or len(certificates) != len(tickers)
                or {row.source.ticker for row in certificates} != tickers
                or any(row.source.session_date != day for row in certificates)):
            raise ValueError("Programme liquidity scope differs from certified1s assignments")
        for certificate in certificates:
            liquidity_window_sql(certificate, first_bucket=work.first_bucket, stop_bucket=work.stop_bucket)
