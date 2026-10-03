import asyncio
from dataclasses import replace
from datetime import date
from functools import wraps

import pytest

from src.trading_runtime.strategy_forty_four_coordinator import (
    CompletedLegFacts, StrategyFortyFourCoordinator, SubmissionReceipt,
)
from tests.test_strategy_forty_four_rules import facts


def run_async(function):
    @wraps(function)
    def run():
        return asyncio.run(function())
    return run


class Port:
    def __init__(self, *, cash=10_000., fail_publication=False, reject=False):
        self.cash = cash
        self.fail_publication = fail_publication
        self.reject = reject
        self.events = []
        self.amendments = []

    async def free_cash_after_reservations(self, account_id):
        return self.cash

    async def publish_batch(self, state):
        self.events.append(("lock", state))
        if self.fail_publication:
            raise OSError("uncertain receipt")

    async def submit_leg(self, batch, ordinal, intent):
        self.events.append(("submit", intent))
        return SubmissionReceipt(ordinal, intent.intent_id,
            "" if self.reject else f"group-{ordinal}",
            "rejected" if self.reject else "submitted")

    async def publish_state(self, state):
        self.events.append(("state", state))

    async def amend_stop(self, source, facts):
        self.amendments.append(source)
        return source.intent.invalidation_price


def coordinator(port):
    return StrategyFortyFourCoordinator(account_id="A", session_date=date(2026, 9, 3),
        source_token="certified-source-plan", port=port)


@run_async
async def test_one_batch_is_fenced_before_fifteen_independent_submissions():
    port = Port()
    engine = coordinator(port)
    state = await engine.decide([facts()], assignment_ids={"TEST": "X"})
    assert port.events[0][0] == "lock"
    assert len([row for kind, row in port.events if kind == "submit"]) == 15
    assert len({leg.group_id for leg in state.legs}) == 15
    assert all(len(intent.resolved_protection_profile().slices) == 1
               for kind, intent in port.events if kind == "submit")
    assert await engine.decide([facts(boundary_ms=21_000, admission_ms=21_000,
        structural_boundary_ms=21_000)], assignment_ids={"TEST": "X"}) is None


@run_async
async def test_rejected_batch_still_locks_the_ticker_for_the_session():
    engine = coordinator(Port(reject=True))
    state = await engine.decide([facts()], assignment_ids={"TEST": "X"})
    assert all(leg.outcome == "rejected" for leg in state.legs)
    assert await engine.decide([facts(boundary_ms=21_000, admission_ms=21_000,
        structural_boundary_ms=21_000)], assignment_ids={"TEST": "X"}) is None


@run_async
async def test_uncertain_lock_publication_poison_prevents_any_broker_effect_or_retry():
    port = Port(fail_publication=True)
    engine = coordinator(port)
    with pytest.raises(OSError):
        await engine.decide([facts()], assignment_ids={"TEST": "X"})
    assert "TEST" in engine.batches
    assert not any(kind == "submit" for kind, _ in port.events)
    with pytest.raises(RuntimeError, match="cold recovery"):
        await engine.decide([facts()], assignment_ids={"TEST": "X"})


@run_async
async def test_ranking_is_input_order_independent_and_does_not_fall_back_on_sizing():
    a = facts(ticker="AAA", ask=100., bid=99.9, close=100., swing_low=99.,
        resistances=tuple(replace(r, lower=r.lower * 10) for r in facts().resistances))
    z = facts(ticker="ZZZ")
    for ordered in ([a, z], [z, a]):
        port = Port(cash=100.)
        engine = coordinator(port)
        assert await engine.decide(ordered, assignment_ids={"AAA": "A", "ZZZ": "Z"}) is None
        assert not port.events


def management(leg, **changes):
    return replace(CompletedLegFacts(leg.group_id, 31_000, 20_100, 20_100,
        10., 1, leg.confirmed_stop, True, 10.8, 10.7, True, 0, .03,
        "certified-source-plan", 10.), **changes)


@run_async
async def test_native_fill_age_and_amendments_remain_leg_scoped():
    port = Port()
    engine = coordinator(port)
    state = await engine.decide([facts()], assignment_ids={"TEST": "X"})
    state = await engine.manage("TEST", [management(state.legs[0], boundary_ms=21_000,
        high=50.), management(state.legs[1], boundary_ms=21_000, high=60.)])
    assert not port.amendments
    assert state.legs[0].peak_after_entry == state.legs[1].peak_after_entry == 10.
    state = await engine.manage("TEST", [management(state.legs[0])])
    assert len(port.amendments) == 1
    assert port.amendments[0].group_id == state.legs[0].group_id
    assert state.legs[0].confirmed_stop == pytest.approx(10.69)
    assert state.legs[1].confirmed_stop == 9.79  # Native ten-decimal order precision.
    with pytest.raises(ValueError, match="causal"):
        await engine.manage("TEST", [management(state.legs[0])])


@run_async
async def test_future_fills_and_foreign_groups_are_rejected_before_amendment():
    port = Port()
    engine = coordinator(port)
    state = await engine.decide([facts()], assignment_ids={"TEST": "X"})
    with pytest.raises(ValueError, match="foreign"):
        await engine.manage("TEST", [management(state.legs[0], group_id="foreign")])
    assert not port.amendments
