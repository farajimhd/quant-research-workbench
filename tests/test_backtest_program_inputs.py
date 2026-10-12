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


def release_for(pin, *, minimum_age_seconds=None):
    spec, _, kwargs = bundle()
    field = pin.fields[0]
    contract = InputField(field.name, field.unit, "arte.qmd_causal_history_value_v1",
        pin.scope.product_revision, 1000)
    program = RuleProgram((N(O.FEATURE, field=field.name), N(O.LAG, left=0, window=60),
        N(O.CONSTANT, literal=0., unit=field.unit), N(O.GREATER, left=1, right=2)), 3)
    ages = spec.minimum_age_seconds if minimum_age_seconds is None else {**spec.minimum_age_seconds, **minimum_age_seconds}
    return restore_release(project_release(replace(spec, programs={stage: program for stage in spec.programs}, minimum_age_seconds=ages),
        {field.name: contract}, **kwargs))


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
        return response_for(consume(store, prepared_source), request["first_ordinal"], request["stop_ordinal"])
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
        return response_for(certificate, *calls[-1])
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
        return response_for(certificate, request["first_ordinal"], request["stop_ordinal"])
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
