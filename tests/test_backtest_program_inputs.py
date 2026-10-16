import asyncio
from dataclasses import replace

import pytest
import polars as pl
from polars.testing import assert_frame_equal

from src.backend.backtest_program_inputs import prepare_program_block
from src.market_engine import columnar_history_product as c
from src.trading_runtime.columnar_rule_program import InputField, RuleProgram, RuleNode as N, Operation as O, evaluate_rules
from src.trading_runtime.columnar_strategy_release import project_release, restore_release
from tests.test_columnar_strategy_release import bundle
from test_columnar_history_publication import prepared_source, source, Store, publish, consume
from tests.test_qmd_columnar_history_transport import response_for
from tests.test_program_controller_runtime import definition_and_actor
from src.backend.replay_run_service import ReplayRunController
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy


def release_for(pin, *, minimum_age_seconds=None, entry_only=False):
    spec, _, kwargs = bundle()
    field = pin.fields[0]
    contract = InputField(field.name, field.unit, "q_live.qmd_causal_history_value_v1",
        pin.scope.product_revision, 1000)
    program = RuleProgram((N(O.FEATURE, field=field.name), N(O.LAG, left=0, window=60),
        N(O.CONSTANT, literal=0., unit=field.unit), N(O.GREATER, left=1, right=2)), 3)
    quiet = (RuleProgram((*program.nodes, N(O.NOT, left=program.output),
        N(O.AND, left=program.output, right=len(program.nodes))), len(program.nodes) + 1)
        if entry_only else program)
    ages = spec.minimum_age_seconds if minimum_age_seconds is None else {**spec.minimum_age_seconds, **minimum_age_seconds}
    return restore_release(project_release(replace(spec, programs={stage:
        quiet if entry_only and stage != 'entry' else program for stage in spec.programs}, minimum_age_seconds=ages),
        {field.name: contract}, **kwargs))


def test_prepared_block_retains_unpriced_clock_without_computing_a_stop():
    from datetime import datetime, UTC, timedelta
    from types import SimpleNamespace
    from src.backend.backtest_program_inputs import PreparedProgramBlock
    spec, fields, kwargs = bundle()
    spec = replace(spec, policy={**spec.policy, 'swing_left': 0, 'swing_right': 0})
    release = restore_release(project_release(spec, fields, **kwargs))
    at = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    rows = [dict(listing_id='listing', evaluation_block='origin', observed_ordinal=i,
        boundary_at=at + timedelta(seconds=i), available_at=at + timedelta(seconds=i),
        reference_close=price) for i, price in enumerate((None, 10.))]
    market = pl.DataFrame(rows, schema_overrides={'reference_close': pl.Float64})
    masks = market.select('listing_id', 'evaluation_block', 'observed_ordinal', 'boundary_at').with_columns(
        *[pl.lit(True).alias(stage) for stage in release.programs],
        *[pl.lit(True).alias(stage+'__valid') for stage in release.programs])
    block = PreparedProgramBlock(release.header['content_hash'], 'origin', 0, 2,
        {'listing': 'a'*64}, market, masks)
    population = SimpleNamespace(is_ranked=lambda listing, clock: clock > at)
    missing = block.boundary_for(release, population, listing_id='listing', ticker='ABC', ordinal=0)
    assert missing.close is None and missing.initial_stop is None and not missing.eligible
    observed = block.boundary_for(release, population, listing_id='listing', ticker='ABC', ordinal=1)
    assert observed.close == 10. and observed.initial_stop == 10. * (1-release.header['stop_fraction'])
    assert block.market.height == 2 and missing.boundary_at == at
    for ordinal in range(2):
        clock = at + timedelta(seconds=ordinal)
        assert block.boundary_at(release, population, listing_id='listing', ticker='ABC', at=clock) == (
            ordinal, block.boundary_for(release, population, listing_id='listing', ticker='ABC',
                                       ordinal=block.ordinal_for('listing', clock)))


@pytest.mark.parametrize('fault', ['first_identity', 'target_identity', 'target_clock', 'fractional', 'after'])
def test_combined_boundary_lookup_retains_exact_origin_and_target_guards(fault):
    from datetime import datetime, UTC, timedelta
    from types import SimpleNamespace
    from src.backend.backtest_program_inputs import PreparedProgramBlock
    spec, fields, kwargs = bundle()
    spec = replace(spec, policy={**spec.policy, 'swing_left': 0, 'swing_right': 0})
    release = restore_release(project_release(spec, fields, **kwargs))
    at = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    market = pl.DataFrame([dict(listing_id='listing', evaluation_block='origin', observed_ordinal=i,
        boundary_at=at + timedelta(seconds=i), available_at=at + timedelta(seconds=i),
        reference_close=10.) for i in range(3)])
    masks = market.select('listing_id', 'evaluation_block', 'observed_ordinal', 'boundary_at').with_columns(
        *[pl.lit(True).alias(stage) for stage in release.programs],
        *[pl.lit(True).alias(stage+'__valid') for stage in release.programs])
    block = PreparedProgramBlock(release.header['content_hash'], 'origin', 0, 3,
        {'listing': 'a'*64}, market, masks)
    # In-place corruption must still be detected; no cached mutable row is trusted.
    if fault.endswith('identity'):
        block.masks[0 if fault == 'first_identity' else 1, 'evaluation_block'] = 'other'
    elif fault == 'target_clock':
        block.market[1, 'boundary_at'] = at + timedelta(seconds=2)
    clock = at + timedelta(seconds=1, microseconds=1 if fault == 'fractional' else 0)
    if fault == 'after':
        clock = at + timedelta(seconds=3)
    with pytest.raises(ValueError):
        block.boundary_at(release, SimpleNamespace(is_ranked=lambda *_: True),
                          listing_id='listing', ticker='ABC', at=clock)


@pytest.mark.parametrize("concurrency", [1, 2])
def test_cohort_preparation_reuses_full_feature_origin_and_bounds_parallel_io(prepared_source, concurrency):
    from threading import Lock, Barrier
    from time import sleep
    from src.backend.backtest_program_inputs import prepare_program_cohort_inputs, preflight_program_cohort_inputs
    from src.backend.qmd_columnar_population import CertifiedPopulationPin
    feature_store, volume_store = Store(), Store()
    publish(feature_store, prepared_source)
    volume_source = replace(prepared_source, fields=())
    publish(volume_store, volume_source)
    features, volume = consume(feature_store, prepared_source), consume(volume_store, volume_source)
    feature_pin, volume_pin = features.pin(), volume.pin()
    release = release_for(feature_pin)
    from types import MappingProxyType
    from datetime import datetime, UTC
    opening = replace(prepared_source.opening_reference, rows_by_listing=MappingProxyType({
        identity: MappingProxyType({**row, "status": "resolved", "ticker": feature_pin.scope.ticker,
            "listing_id": identity, "adjusted_prior_close": 10.,
            "available_at": datetime(2026, 8, 3, 8, tzinfo=UTC)})
        for identity, row in prepared_source.opening_reference.rows_by_listing.items()}))
    population = CertifiedPopulationPin(opening,
        {volume_pin.scope.listing_id: volume_pin}, release.universe)
    active, peak, calls = 0, 0, []
    overlap_required = True
    lock, overlap = Lock(), Barrier(2)
    def post(path, request, timeout):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append((bool(request["field_ids"]), request["first_ordinal"], request["stop_ordinal"]))
        try:
            if concurrency == 2 and overlap_required:
                overlap.wait(timeout=5)
            sleep(.01)
            certificate = features if request["field_ids"] else volume
            return response_for(certificate, request["first_ordinal"], request["stop_ordinal"], request["field_ids"])
        finally:
            with lock:
                active -= 1
    kwargs = dict(first_ordinal=0, stop_ordinal=200, evaluation_block="optimizer-origin",
        first_bucket=feature_pin.scope.first_bucket + 80,
        stop_bucket=feature_pin.scope.first_bucket + 90,
        as_of=c.completed_clock(feature_pin.scope, feature_pin.scope.stop_bucket - 1), concurrency=concurrency)
    checked_pins, checked_assignments = preflight_program_cohort_inputs(release,
        {feature_pin.scope.listing_id: feature_pin}, population,
        {"first": feature_pin.scope.listing_id, "second": feature_pin.scope.listing_id},
        **kwargs, history_post=post)
    assert calls == [] and active == 0
    assert checked_pins[feature_pin.scope.listing_id] is feature_pin
    assert checked_assignments["first"] == checked_assignments["second"]
    from src.backend.backtest_program_input_plan import ProgramCohortInputPlan, preflight_program_session_inputs
    plan = ProgramCohortInputPlan({feature_pin.scope.listing_id: feature_pin}, population,
        {"first": feature_pin.scope.listing_id, "second": feature_pin.scope.listing_id}, **kwargs)
    assert preflight_program_session_inputs(release, (plan,), first_bucket=plan.first_bucket,
                                             stop_bucket=plan.stop_bucket) == (plan,)
    with pytest.raises(ValueError, match="requested session end"):
        preflight_program_session_inputs(release, (plan,), first_bucket=plan.first_bucket,
                                         stop_bucket=plan.stop_bucket + 1)
    with pytest.raises(ValueError, match="gap, overlap"):
        preflight_program_session_inputs(release, (plan, plan), first_bucket=plan.first_bucket,
                                         stop_bucket=plan.stop_bucket)
    next_plan = replace(plan, first_bucket=plan.stop_bucket, stop_bucket=plan.stop_bucket + 10)
    assert preflight_program_session_inputs(release, (plan, next_plan), first_bucket=plan.first_bucket,
                                             stop_bucket=next_plan.stop_bucket) == (plan, next_plan)
    with pytest.raises(ValueError, match="evaluation origin"):
        preflight_program_session_inputs(release, (plan, replace(next_plan, evaluation_block="different")),
                                         first_bucket=plan.first_bucket, stop_bucket=next_plan.stop_bucket)
    assert calls == []
    with pytest.raises(TypeError):
        checked_assignments["third"] = feature_pin.scope.listing_id
    result = asyncio.run(prepare_program_cohort_inputs(release,
        {feature_pin.scope.listing_id: feature_pin}, population,
        {"first": feature_pin.scope.listing_id, "second": feature_pin.scope.listing_id},
        **kwargs, history_post=post))
    assert peak == concurrency and active == 0
    assert sorted(calls) == [(False, 80, 90), (True, 0, 200)]
    assert result.blocks["first"] is result.blocks["second"]
    block = result.blocks["first"]
    assert block.first_ordinal == 0 and block.stop_ordinal == 200
    assert block.evaluation_block == "optimizer-origin" and len(result.projection.clock_offsets) == 10
    assert_frame_equal(block.masks, evaluate_rules(block.market, release.programs, release.fields), check_exact=True)
    from tests.test_strategy_signal_configuration import rows as signal_rows
    from src.backend.strategy_signal_configuration import restore_signal_configuration
    from src.backend.signal_stream_runtime_service import SignalStreamRuntime
    from tests.test_strategy_configuration_selection import ID
    source_rows = signal_rows()
    discovery, source_catalogs = restore_signal_configuration(source_rows, configuration_id=ID, configuration_revision="configuration-1")
    signal_runtime = SignalStreamRuntime()
    signal_runtime._hydrated = True
    at = result.projection.frame["boundary_at"].min()
    stage = signal_runtime.stage_resolve({"market_discovery": discovery}, result.projection.frame_for(at).to_dicts(),
                                         as_of=at, data_fields_projected=False)
    assert len(stage.occurrences) == 1
    price_instance = source_catalogs["stream-1"].catalog.bindings[0].instance_ref
    assert stage.occurrences[0]["field_evidence"][price_instance]["available_at"] == at.isoformat()
    from src.backend.backtest_program_session import PreparedProgramCohortWork
    definition, original = definition_and_actor()
    definition = replace(definition, session_date=feature_pin.scope.session_date)
    assignments = tuple(replace(original.assignments()[0], assignment_id=identity,
        strategy_id=release.header["strategy_id"], strategy_revision=release.header["strategy_number"])
        for identity in ("first",))
    actor = AssignedProgramStrategy(release, assignments,
        {"first": feature_pin.scope.listing_id})
    controller = ReplayRunController(definition, program_strategy=actor,
        program_liquidity_resolution_ms=100)
    # Preparation carries the scheduler's scope without granting execution;
    # actual source-publisher/planner validation remains at launch/cohort entry.
    scope = dict(source_publisher=None, catalogs=None, reader_factory=None, concurrency=concurrency,
        assignment_order=("first",), execution_policy=None, protection_profiles_at=None,
        outside_rth=False, maximum_events=1000)
    native = asyncio.run(controller.prepare_program_cohort_work(
        {feature_pin.scope.listing_id: feature_pin}, population, scope=scope,
        **kwargs, history_post=post))
    assert type(native) is PreparedProgramCohortWork
    assert tuple(native.blocks) == ("first",)
    assert native.scope == scope and native.first_bucket == kwargs["first_bucket"]
    assert_frame_equal(native.projection.frame, result.projection.frame, check_exact=True)
    before_repeat = len(calls)
    overlap_required = False  # A cache hit has only the population transport.
    repeated = asyncio.run(controller.prepare_program_cohort_work(
        {feature_pin.scope.listing_id: feature_pin}, population, scope=scope,
        **kwargs, history_post=post))
    assert repeated.blocks["first"] is native.blocks["first"]
    assert calls[before_repeat:] == [(False, 80, 90)]
    assert_frame_equal(repeated.projection.frame, native.projection.frame, check_exact=True)
    from datetime import time
    from src.backend.backtest_program_input_plan import prepare_program_session_input_plan
    controller.definition = replace(controller.definition, start_time=time(9, 31, 20), end_time=time(9, 31, 30))
    session_plan = replace(plan, listing_by_assignment=dict(controller._program_strategy._listings))
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
    from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit
    day = controller.definition.session_date.isoformat()
    ticker = actor.assignments()[0].ticker
    attempt = "00000000-0000-0000-0000-000000000002"
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "a" * 64, "b" * 64,
        (day,), (ticker,), tuple(MarketDayUnit("a" * 64, day, ticker, stage,
            attempt, "c" * 64, 2, "d" * 64) for stage in ("bars", "technical", "broker_100ms")),
        (100,), "e" * 64)
    prices = PriceLevelPlan(market.build_id, (PriceLevelUnit(day, ticker, attempt,
        "00000000-0000-0000-0000-000000000003", 2, 2, 0., "f" * 64),), "0" * 64)
    session_scope = dict(scope, reader_factory=lambda: pytest.fail("Preparation opened a liquidity reader"),
        market_plan=market, price_plan=prices)
    before_session = len(calls)
    with pytest.raises(ValueError, match="bounded native reader"):
        asyncio.run(prepare_program_session_input_plan(controller, (session_plan,),
            scopes=(scope,), history_post=post))
    assert len(calls) == before_session
    session_work = asyncio.run(prepare_program_session_input_plan(controller, (session_plan,),
        scopes=(session_scope,), history_post=post))
    assert len(session_work) == 1 and session_work[0].blocks["first"] is native.blocks["first"]
    assert calls[before_session:] == [(False, 80, 90)]
    from src.backend.backtest_program_input_plan import preflight_program_liquidity_scope
    # The real dependency compiler returns both strategy1s and broker100ms.
    # Scope preflight must preserve that parent without opening any reader.
    mixed_parent = replace(market, required_resolutions_ms=(100, 1000))
    preflight_program_liquidity_scope(controller, replace(session_work[0],
        scope=dict(session_scope, market_plan=mixed_parent)))
    assert mixed_parent.required_resolutions_ms == (100, 1000)
    from src.backend.backtest_liquidity_resolution import CertifiedLiquidityResolution
    from tests.test_liquidity_resolution_producer import source as liquidity_source
    unit = replace(liquidity_source().unit, session_date=controller.definition.session_date, ticker=ticker)
    certificate = CertifiedLiquidityResolution(unit,
        "00000000-0000-0000-0000-000000000003", "f" * 64, "e" * 64, 2, 0, 0.)
    one_second_scope = dict(scope, reader_factory=session_scope["reader_factory"],
        liquidity_certificates=(certificate,))
    controller._program_liquidity_resolution_ms = 1000
    preflight_program_liquidity_scope(controller, replace(session_work[0], scope=one_second_scope))
    for invalid in ((certificate, certificate), (replace(certificate,
            source=replace(unit, ticker="WRONG")),), ()):
        with pytest.raises(ValueError, match="certified1s assignments"):
            preflight_program_liquidity_scope(controller, replace(session_work[0],
                scope=dict(one_second_scope, liquidity_certificates=invalid)))
    controller._program_liquidity_resolution_ms = 100
    with pytest.raises(ValueError, match="certified100ms assignments"):
        asyncio.run(prepare_program_session_input_plan(controller, (session_plan,),
            scopes=(dict(session_scope, market_plan=replace(market, tickers=("WRONG",))),), history_post=post))
    assert len(calls) == before_session + 1
    with pytest.raises(ValueError, match="gap, overlap"):
        asyncio.run(prepare_program_session_input_plan(controller, (replace(session_plan,
            first_bucket=session_plan.first_bucket + 1),), scopes=(scope,), history_post=post))
    assert len(calls) == before_session + 1
    from datetime import timedelta
    controller.definition = replace(controller.definition,
        session_date=controller.definition.session_date + timedelta(days=1))
    with pytest.raises(ValueError, match="exact certified session"):
        asyncio.run(controller.prepare_program_cohort_work(
            {feature_pin.scope.listing_id: feature_pin}, population, scope=scope,
            **kwargs, history_post=post))
    with pytest.raises(ValueError, match="feature origin"):
        asyncio.run(prepare_program_cohort_inputs(release,
            {feature_pin.scope.listing_id: feature_pin}, population,
            {"first": feature_pin.scope.listing_id}, **{**kwargs, "first_ordinal": 81}, history_post=post))
    assert len(calls) == 6
    from src.backend.backtest_program_listing_cache import ProgramListingFeatureCache
    retained_bytes = block.market.estimated_size() + block.masks.estimated_size()
    partitioned = asyncio.run(prepare_program_cohort_inputs(release, checked_pins, population,
        checked_assignments, **kwargs, history_post=post,
        feature_cache=ProgramListingFeatureCache(maximum_retained_bytes=retained_bytes)))
    assert_frame_equal(partitioned.blocks["first"].market, block.market, check_exact=True)
    assert_frame_equal(partitioned.blocks["first"].masks, block.masks, check_exact=True)
    assert_frame_equal(partitioned.projection.frame, result.projection.frame, check_exact=True)
    partition_controller = ReplayRunController(replace(controller.definition,
        session_date=feature_pin.scope.session_date), program_strategy=actor, program_liquidity_resolution_ms=100)
    partition_plan = replace(session_plan, maximum_retained_feature_bytes=retained_bytes)
    partition_work = asyncio.run(prepare_program_session_input_plan(partition_controller, (partition_plan,),
        scopes=(session_scope,), history_post=post))
    assert type(partition_controller._program_feature_cache) is ProgramListingFeatureCache
    assert_frame_equal(partition_work[0].blocks["first"].masks, block.masks, check_exact=True)
    assert_frame_equal(partition_work[0].projection.frame, result.projection.frame, check_exact=True)
    from src.backend.backtest_program_population_cache import ProgramPopulationCache
    cached_controller = ReplayRunController(partition_controller.definition,
        program_strategy=actor, program_liquidity_resolution_ms=100)
    cached_plan = replace(partition_plan, maximum_retained_population_bytes=1000000)
    cached_work = asyncio.run(prepare_program_session_input_plan(cached_controller, (cached_plan,),
        scopes=(session_scope,), history_post=post))
    assert type(cached_controller._program_population_cache) is ProgramPopulationCache
    assert_frame_equal(cached_work[0].projection.frame, result.projection.frame, check_exact=True)
    before_cached_repeat = len(calls)
    repeated_work = asyncio.run(prepare_program_session_input_plan(cached_controller, (cached_plan,),
        scopes=(session_scope,), history_post=post))
    assert len(calls) == before_cached_repeat
    assert_frame_equal(repeated_work[0].projection.frame, result.projection.frame, check_exact=True)
    from src.backend.backtest_program_input_plan import preflight_program_liquidity_scope
    from test_configuration_execution_binding import binding
    execution = binding()
    bound_work = replace(partition_work[0], scope={**dict(partition_work[0].scope),
        'execution_policy': execution})
    before_binding_preflight = len(calls)
    with pytest.raises(ValueError, match='execution binding'):
        preflight_program_liquidity_scope(partition_controller, bound_work)
    revision = {**partition_controller.definition.configuration_revision,
        'revision_id': 'strategy-configuration:' + execution.header['configuration_id'],
        'selection_hash': execution.header['configuration_hash'],
        'execution_binding_hash': execution.header['content_hash']}
    partition_controller.definition = replace(partition_controller.definition, configuration_revision=revision)
    preflight_program_liquidity_scope(partition_controller, bound_work)
    assert len(calls) == before_binding_preflight


def test_prepared_scanner_bridge_keeps_exact_top10_and_rejects_missing_history(prepared_source):
    from datetime import timedelta
    from src.backend.backtest_program_inputs import PreparedProgramBlock, prepare_program_scanner_projection
    from src.backend.qmd_columnar_population import CertifiedPopulationWindow
    from src.market_engine.volume_rank_rules import select_volume_universe
    store = Store()
    publish(store, prepared_source)
    pin = consume(store, prepared_source).pin()
    release = release_for(pin)
    def post(path, request, timeout):
        return response_for(consume(store, prepared_source), request["first_ordinal"], request["stop_ordinal"], request["field_ids"])
    block = asyncio.run(prepare_program_block(release, {pin.scope.listing_id: pin},
        first_ordinal=0, stop_ordinal=200, evaluation_block="origin",
        as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), concurrency=1, history_post=post))
    names = tuple(f"L{index:02}" for index in range(12))
    market = pl.concat([block.market.with_columns(pl.lit(name).alias("listing_id"),
        pl.lit(100.).alias("rolling_volume")) for name in names])
    masks = pl.concat([block.masks.with_columns(pl.lit(name).alias("listing_id")) for name in names])
    expanded = PreparedProgramBlock(block.release_hash, block.evaluation_block, 0, 200,
        {name: "a" * 64 for name in names}, market, masks)
    rows = market.select("listing_id", "boundary_at", "available_at", "rolling_volume").with_columns(
        pl.col("listing_id").alias("ticker"),
        (pl.col("boundary_at") - pl.duration(days=1)).alias("prior_close_available_at"),
        pl.when(pl.col("listing_id") == "L00").then(release.universe.maximum_prior_close + 1.)
            .otherwise((release.universe.minimum_prior_close + release.universe.maximum_prior_close) / 2.)
            .alias("adjusted_prior_close"), pl.lit(release.universe.window_seconds).alias("volume_window_seconds"))
    ranked = select_volume_universe(rows, release.universe)
    population = CertifiedPopulationWindow("source", rows, ranked, 12, 11, 0, 0, 1)
    prepared = prepare_program_scanner_projection(expanded, population, release)
    at = market["boundary_at"][0]
    candidates = prepared.frame_for(at)
    assert candidates["listing_id"].to_list() == list(names[1:11])
    assert candidates["rank"].to_list() == list(range(1, 11))
    assert candidates["last_price"].to_list() == candidates["reference_close"].to_list()
    assert candidates["rolling_share_volume"].to_list() == [100.] * 10
    for field in release.fields:
        assert candidates.schema[field] == expanded.market.schema[field]
    start, stop = at + timedelta(seconds=79), at + timedelta(seconds=89)
    execution_rows = rows.filter((pl.col("boundary_at") > start) & (pl.col("boundary_at") <= stop))
    execution_population = replace(population, rows=execution_rows,
        ranked=select_volume_universe(execution_rows, release.universe))
    original_market, original_masks = expanded.market.clone(), expanded.masks.clone()
    scoped = prepare_program_scanner_projection(expanded, execution_population, release,
        start_at=start, stop_at=stop)
    assert len(scoped.clock_offsets) == 10
    for clock in scoped.clock_offsets:
        assert_frame_equal(scoped.frame_for(clock), prepared.frame_for(clock))
    assert_frame_equal(expanded.market, original_market)
    assert_frame_equal(expanded.masks, original_masks)
    with pytest.raises(ValueError, match="outside its prepared cohort"):
        scoped.frame_for(at)
    with pytest.raises(ValueError, match="execution window"):
        prepare_program_scanner_projection(expanded, execution_population, release, start_at=start)
    with pytest.raises(ValueError, match="outside its prepared cohort"):
        prepared.frame_for(at - timedelta(seconds=1))
    with pytest.raises(ValueError, match="immutable universe rule"):
        prepare_program_scanner_projection(expanded, replace(population, ranked=ranked.head(0)), release)
    without_clock = rows.filter(pl.col("boundary_at") != at)
    with pytest.raises(ValueError, match="population coverage"):
        prepare_program_scanner_projection(expanded, replace(population, rows=without_clock,
            ranked=select_volume_universe(without_clock, release.universe)), release)
    ambiguous = rows.with_columns(pl.lit("DUPLICATE").alias("ticker"))
    with pytest.raises(ValueError, match="repeats a ticker"):
        prepare_program_scanner_projection(expanded, replace(population, rows=ambiguous,
            ranked=select_volume_universe(ambiguous, release.universe)), release)
    missing = PreparedProgramBlock(block.release_hash, block.evaluation_block, 0, 200,
        {name: "a" * 64 for name in names if name != "L01"},
        market.filter(pl.col("listing_id") != "L01"), masks.filter(pl.col("listing_id") != "L01"))
    with pytest.raises(ValueError, match="history for a ranked listing"):
        prepare_program_scanner_projection(missing, population, release)


def test_transport_packets_preserve_full_rule_block_and_warmup(prepared_source):
    src = source(4200)
    store = Store(4200)
    publish(store, src)
    certificate = consume(store, src)
    pin = certificate.pin()
    release = release_for(pin)
    calls = []
    def post(path, request, timeout):
        calls.append((request["first_ordinal"], request["stop_ordinal"]))
        return response_for(certificate, *calls[-1], request["field_ids"])
    block = asyncio.run(prepare_program_block(release, {pin.scope.listing_id: pin},
        first_ordinal=0, stop_ordinal=4200, evaluation_block="optimizer-block-0",
        as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), concurrency=2, history_post=post))
    assert sorted(calls) == [(0, 4096), (4096, 4200)]
    expected = pl.concat([certificate.window(first_ordinal=first, stop_ordinal=stop,
        evaluation_block="optimizer-block-0") for first, stop in [(0, 4096), (4096, 4200)]])
    assert_frame_equal(block.market, expected, check_exact=True)
    assert_frame_equal(block.masks, evaluate_rules(expected, release.programs, release.fields), check_exact=True)
    assert block.masks["entry__valid"][4096]
    assert block.history_hashes[pin.scope.listing_id] == pin.header["content_hash"]
    from src.backend.backtest_program_feature_cache import ProgramFeatureBlockCache
    cache = ProgramFeatureBlockCache()
    async def cached_origin():
        arguments = dict(first_ordinal=0, stop_ordinal=4200, evaluation_block="optimizer-block-0",
            as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), concurrency=2, history_post=post)
        retained = await cache.load(release, {pin.scope.listing_id: pin}, **arguments)
        assert_frame_equal(retained.market, block.market, check_exact=True)
        assert_frame_equal(retained.masks, block.masks, check_exact=True)
        before = len(calls)
        assert await cache.load(release, {pin.scope.listing_id: pin}, **arguments) is retained
        assert len(calls) == before
        with pytest.raises(ValueError, match="beyond its as-of clock"):
            await cache.load(release, {pin.scope.listing_id: pin}, **{**arguments,
                "as_of": c.completed_clock(pin.scope, pin.scope.first_bucket + 4095)})
        assert len(calls) == before
    asyncio.run(cached_origin())


@pytest.mark.parametrize("bad", ["future", "field_revision", "listing", "capacity"])
def test_all_input_contracts_are_checked_before_first_io(prepared_source, bad):
    store = Store()
    publish(store, prepared_source)
    certificate = consume(store, prepared_source)
    pin = certificate.pin()
    release = release_for(pin)
    pins = {pin.scope.listing_id: pin}
    stop = 200
    at = c.completed_clock(pin.scope, pin.scope.stop_bucket - 1)
    if bad == "future":
        at = c.completed_clock(pin.scope, pin.scope.stop_bucket - 2)
    elif bad == "field_revision":
        name = next(iter(release.fields))
        release = replace(release, fields={name: replace(release.fields[name], revision="a" * 64)})
    elif bad == "listing":
        pins = {"other-listing": pin}
    else:
        stop = 262145
    calls = []
    def post(*a, **k):
        calls.append(a)
        raise AssertionError("Invalid input cannot start network I/O")
    with pytest.raises(ValueError):
        asyncio.run(prepare_program_block(release, pins, first_ordinal=0, stop_ordinal=stop,
            evaluation_block="block", as_of=at, concurrency=2, history_post=post))
    assert not calls


def test_main_controller_prepares_certified_inputs_for_its_actual_actor(prepared_source, tmp_path):
    store = Store()
    publish(store, prepared_source)
    certificate = consume(store, prepared_source)
    pin = certificate.pin()
    release = release_for(pin)
    definition, original = definition_and_actor()
    definition = replace(definition, session_date=pin.scope.session_date)
    actor = AssignedProgramStrategy(release, original.assignments(), {"assignment": "listing"})
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=1000)
    controller._journal = BacktestMemoryJournal(run_id=controller.run_id)
    calls = []
    def post(path, request, timeout):
        calls.append(request)
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"], request["field_ids"])
    async def run():
        await controller._initialize_runtime(record_configuration=False, record_lifecycle=False)
        try:
            block = await controller.prepare_program_inputs({"listing": pin}, first_ordinal=0,
                stop_ordinal=200, evaluation_block="optimizer-block-0", concurrency=2,
                as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1), history_post=post)
            assert block.market.height == block.masks.height == 200
            assert block.release_hash == actor.program_release.header["content_hash"]
            assert not actor.execution_state.positions
            assert len(calls) == 1
        finally:
            await controller._runtime.finish()
    asyncio.run(run())
