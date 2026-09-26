"""Entry evidence is published only after normalized child read-back."""
from uuid import uuid4

import pytest

from pipelines.strategy_one import entry_evidence_publication as subject
from pipelines.strategy_one import entry_evidence_derivation as derivation
from src.backend.backtest_strategy_one_entry_product import (
    ActivationFact, CandidateFact,
)


def scope():
    return subject.EntryPublicationScope(
        "build", "2026-08-18", "AAA", str(uuid4()), str(uuid4()),
        "c" * 64, "a" * 64, "b" * 64, "d" * 64, "e" * 64, "f" * 64,
        (31_000,), (30_000,))


def facts():
    return ((ActivationFact("AAA", 30_000, 100_000, .5, ("R1",)),),
            (CandidateFact("AAA", 31_000, 30_000, 30_000, "P1", 101_000,
                           "v7_resistance", "R3", "P1", True,
                           9.89, 12.0, "R3", 3),))


class Writer:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)
        return ""


def test_coverage_is_written_last_after_exact_readback(monkeypatch):
    writer = Writer()
    state = {"covered": False, "readback": False}
    values = facts()
    inserted = []

    def covered(*_args):
        if not state["covered"]:
            state["covered"] = True
            return None
        assert state["readback"]
        return state["attempt"]

    def insert(_writer, table, _scope, attempt, rows):
        state["attempt"] = attempt
        inserted.append((table, rows))

    def readback(*_args):
        assert len(inserted) == 3
        state["readback"] = True
        return (*values, 1)

    monkeypatch.setattr(subject, "_verify_existing", covered)
    monkeypatch.setattr(subject, "_insert_rows", insert)
    monkeypatch.setattr(subject, "_read_children", readback)
    assert subject.publish_unit(writer, scope(), derive=lambda: values) == "published"
    assert len(writer.sql) == 1
    assert writer.sql[0].startswith("INSERT INTO arte.strategy_one_entry_coverage_v1")
    assert [table for table, _ in inserted] == [
        subject.ACTIVATION_TABLE, subject.ACTIVATION_RESISTANCE_TABLE,
        subject.EVIDENCE_TABLE]


def test_uncertain_child_readback_never_writes_coverage(monkeypatch):
    writer = Writer()
    monkeypatch.setattr(subject, "_verify_existing", lambda *_: None)
    monkeypatch.setattr(subject, "_insert_rows", lambda *_args: None)
    monkeypatch.setattr(subject, "_read_children", lambda *_args: ((), (), 0))
    with pytest.raises(RuntimeError, match="child read-back differs"):
        subject.publish_unit(writer, scope(), derive=facts)
    assert writer.sql == []


def test_existing_coverage_skips_derivation(monkeypatch):
    writer = Writer()
    monkeypatch.setattr(subject, "_verify_existing", lambda *_: str(uuid4()))
    assert subject.publish_unit(writer, scope(), derive=lambda: pytest.fail(
        "covered unit was recalculated")) == "skipped"
    assert writer.sql == []


def test_causal_producer_uses_candidate_only_scheduler_and_completed_evidence(
        monkeypatch):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
    from src.backend.backtest_strategy_one_activation import StrategyOneActivation
    from src.backend.backtest_strategy_one_entry_product import (
        project_activation, project_candidate,
    )
    from test_backtest_strategy_one_entry_product import evidence
    from test_backtest_strategy_one_entry_store import _plans

    plans = _plans()
    market, candidates, activations, pivots, hod, seeds = plans
    plans = (market, candidates, activations,
             replace(pivots, intervals=pivots.intervals + (("BBB", ()),)),
             replace(hod, contexts=hod.contexts + (("BBB", ()),)),
             replace(seeds, units=seeds.units + ({
                 "ticker": "BBB", "backtest_session": "2026-08-18"},)))
    value = evidence()
    value = replace(value, candidate=replace(
        value.candidate, market_row={**value.candidate.market_row,
                                     "price_valid": 1,
                                     "indicator_resolution_ms": 100}))
    scoped = derivation.publication_scope(*plans, ticker="AAA")

    class Reader:
        def close(self):
            pass

    class Evidence:
        def __init__(self, **kwargs):
            assert tuple(ticker for ticker, _ in kwargs["pivot_plan"].intervals) == ("AAA",)
            assert tuple(ticker for ticker, _ in kwargs["hod_plan"].contexts) == ("AAA",)
            assert tuple(row["ticker"] for row in kwargs["seed_plan"].units) == ("AAA",)

        async def observe_activation(self, _value):
            return value.activation

        async def entry_evidence(self, _candidate, *, tick):
            assert tick == .01
            return value

        async def observe_completed_seconds(self, _work):
            pass

    monkeypatch.setattr(derivation, "project_market_day_plan", lambda plan, _tickers: plan)
    monkeypatch.setattr(derivation, "StrategyOneCausalEvidence", Evidence)
    monkeypatch.setattr(
        derivation, "build_certified_strategy_one_scheduler",
        lambda *_args, **_kwargs: StrategyOneBoundaryScheduler(
            session_date="2026-08-18", candidate_rows=iter((value.candidate,)),
            activation_rows=iter((StrategyOneActivation(
                30_000, "AAA", 100_000),)),
            active_source=lambda _ticker, _after: iter(())))
    prices = SimpleNamespace(projected=lambda _plan: None)
    result = asyncio.run(derivation.derive_unit(
        scoped, *plans, prices, client_factory=Reader))
    assert result == ((project_activation(value.activation),),
                      (project_candidate(value),))
