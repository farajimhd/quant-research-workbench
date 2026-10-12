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

    def __post_init__(self):
        object.__setattr__(self, "pins", MappingProxyType(dict(self.pins)))
        object.__setattr__(self, "listing_by_assignment", MappingProxyType(dict(self.listing_by_assignment)))


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
        pins, assignments = preflight_program_cohort_inputs(release, cohort.pins, cohort.population_pin,
            cohort.listing_by_assignment, first_ordinal=cohort.first_ordinal,
            stop_ordinal=cohort.stop_ordinal, evaluation_block=cohort.evaluation_block,
            first_bucket=cohort.first_bucket, stop_bucket=cohort.stop_bucket,
            as_of=cohort.as_of, concurrency=cohort.concurrency)
        identity = (cohort.population_pin.opening.scope.session_date,
            cohort.population_pin.source_revision, tuple(sorted(assignments.items())),
            tuple(sorted((listing, pin.header["content_hash"]) for listing, pin in pins.items())),
            cohort.first_ordinal, cohort.evaluation_block)
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
            history_post=history_post)
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
                or market.required_resolutions_ms != (100,)
                or len(tickers) * (work.stop_bucket - work.first_bucket) * 10 > 262144):
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
                or any(row.source.session_date != day for row in certificates)
                or len(tickers) * (work.stop_bucket - work.first_bucket) > 262144):
            raise ValueError("Programme liquidity scope differs from certified1s assignments")
        for certificate in certificates:
            liquidity_window_sql(certificate, first_bucket=work.first_bucket, stop_bucket=work.stop_bucket)
