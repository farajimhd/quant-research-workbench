"""Clock coordination guards; financial behavior has separate native tests."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import polars as pl
import pytest

from src.backend.backtest_program_clock import dispatch_prepared_program_clock
from src.backend.backtest_program_inputs import PreparedProgramBlock
from src.backend.qmd_columnar_population import CertifiedPopulationWindow
from src.backend.replay_run_service import ReplayRunController
from src.backend.signal_stream_runtime_service import SignalStreamRuntime
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from tests.test_program_controller_runtime import definition_and_actor


@pytest.mark.parametrize("failure", [None, "coverage", "block", "first_owner", "last_owner"])
def test_complete_clock_preserves_explicit_order_and_checks_before_side_effects(failure):
    _, original = definition_and_actor()
    first, = original.assignments()
    second = replace(first, assignment_id="second", account_id="other-account")
    actor = AssignedProgramStrategy(original.program_release, (first, second),
                                   {"assignment": "listing", "second": "listing"})
    at = datetime(2026, 10, 12, 14, tzinfo=timezone.utc)
    row = dict(listing_id="listing", evaluation_block="origin", observed_ordinal=0,
               boundary_at=at, available_at=at, reference_close=10.)
    header = actor.program_release.header
    row[f"history.swing_low.{header['swing_left']}.{header['swing_right']}"] = 9.
    row[f"history.swing_low.{header['swing_left']}.{header['swing_right']}__valid"] = True
    mask = {key: row[key] for key in ("listing_id", "evaluation_block", "observed_ordinal", "boundary_at")}
    for stage in actor.program_release.programs:
        mask[stage], mask[stage + "__valid"] = True, True
    block = PreparedProgramBlock(header["content_hash"], "origin", 0, 1,
        {"listing": "a" * 64}, pl.DataFrame([row]), pl.DataFrame([mask]))
    population = CertifiedPopulationWindow("source", pl.DataFrame(),
        pl.DataFrame(schema={"boundary_at": pl.Datetime("us", "UTC"), "listing_id": pl.String}),
        1, 1, 0, 0, 0)
    source = object.__new__(SignalStreamRuntime)
    source._typed_scope = (None, "source")
    source.typed_source_boundary = lambda clock: (1, "b" * 64, at)
    controller = object.__new__(ReplayRunController)
    controller._program_strategy, controller._program_signal_runtime = actor, source
    controller._runtime = SimpleNamespace(strategy=actor, last_event_time=at)
    controller._program_dispatch_failure = None
    calls = []
    async def dispatch(actual_block, actual_population, **kwargs):
        assert actual_block is block and actual_population is population
        calls.append(kwargs["assignment_id"])
        if ((failure == "first_owner" and len(calls) == 1)
                or (failure == "last_owner" and len(calls) == 2)):
            controller._runtime = object()
        return {"status": "wait"}
    controller.dispatch_prepared_program_boundary = dispatch
    blocks = {"assignment": block, "second": block}
    if failure == "coverage":
        del blocks["assignment"]
    if failure == "block":
        blocks["assignment"] = replace(block, release_hash="0" * 64)
    async def run():
        request = dispatch_prepared_program_clock(controller, blocks, population, at=at,
            assignment_order=("second", "assignment"), execution_policy=None,
            protection_profiles={"assignment": None, "second": None}, outside_rth=False)
        if failure:
            with pytest.raises(ValueError):
                await request
            assert calls == ([] if failure in {"coverage", "block"} else
                             ["second"] if failure == "first_owner" else ["second", "assignment"])
            if failure.endswith("owner"):
                assert controller._program_dispatch_failure is not None
        else:
            results = await request
            assert calls == ["second", "assignment"]
            assert tuple(identity for identity, _ in results) == tuple(calls)
    asyncio.run(run())
