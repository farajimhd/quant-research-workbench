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


def release_for(pin):
    spec, _, kwargs = bundle()
    field = pin.fields[0]
    contract = InputField(field.name, field.unit, "arte.qmd_causal_history_value_v1",
        pin.scope.product_revision, 1000)
    program = RuleProgram((N(O.FEATURE, field=field.name), N(O.LAG, left=0, window=60),
        N(O.CONSTANT, literal=0., unit=field.unit), N(O.GREATER, left=1, right=2)), 3)
    return restore_release(project_release(replace(spec, programs={stage: program for stage in spec.programs}),
        {field.name: contract}, **kwargs))


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
