"""Exact certified momentum projection and numbered admission parity."""
from dataclasses import replace
from uuid import uuid4
import re

import numpy as np
import pyarrow as pa
import pytest

from src.backend.backtest_market_data import MarketDayUnit, SESSION_OPEN_OFFSET_MS
from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
from src.backend.backtest_strategy_one_static_gate import compile_static_entry_gate, RISING_MOMENTUM_REQUIRED
from src.backend.backtest_strategy_one_stateful import propose_certified_strategy_one_entry
from src.trading_runtime.strategy_rising_momentum_witness import rising_momentum_entry
from test_backtest_strategy_one_entry_store import _plans, Reader
from test_backtest_strategy_one_static_gate import _entry
from test_strategy_one_stateful import _facts


def sources(boundaries=(31_000,)):
    plans = list(_plans())
    market, candidates = plans[:2]
    build = "c" * 64
    attempt = candidates.coverage[0].source_attempts[1]
    technical = MarketDayUnit(build, market.sessions[0], "AAA", "technical", attempt,
                              "a" * 64, 100_000, "b" * 64)
    plans[0] = replace(market, build_id=build, token="a" * 64,
                       units=(technical,), required_resolutions_ms=(100, 1_000, 10_000))
    prepared = replace(candidates.prepared[0], boundary_ms=np.array(boundaries),
                       episode_start_ms=np.full(len(boundaries), 30_000))
    plans[1] = replace(candidates, source_build_id=build, token="b" * 64,
                       prepared=(prepared,))
    return tuple(plans)


class ArrowSource:
    def __init__(self, *, missing=(), falling=False, duplicate=False):
        self.queries = []
        self.missing, self.falling, self.duplicate = missing, falling, duplicate

    def iter_arrow_record_batches(self, sql):
        self.queries.append(sql)
        pairs = re.findall(r"\((\d+),(\d+)\)", sql)
        rows = []
        for resolution, bucket in pairs:
            resolution, bucket = int(resolution), int(bucket)
            clock = (bucket + 1) * resolution - SESSION_OPEN_OFFSET_MS
            if (resolution, clock) in self.missing:
                continue
            line = -float(clock) if self.falling else float(clock)
            rows.append((resolution, clock, line, 0.0))
        rows.sort()
        if self.duplicate and rows:
            rows.append(rows[0])
        yield pa.record_batch([
            pa.array([row[i] for row in rows], type=typ)
            for i, typ in enumerate((pa.uint32(), pa.int64(), pa.float64(), pa.float64()))],
            names=("resolution_ms", "boundary_ms", "macd_line", "macd_signal"))


@pytest.mark.parametrize("falling,allowed", [(False, True), (True, False)])
def test_native_mask_scalar_adapter_and_static_gate_agree(falling, allowed):
    plans = sources()
    reader = ArrowSource(falling=falling)
    momentum = load_rising_momentum_plan(*plans[:2], client=reader)
    witness = momentum.lookup("AAA", 31_000)
    assert rising_momentum_entry(witness) is allowed
    assert momentum.eligible_mask().tolist() == [allowed]
    candidate, fact, activation, financial = _facts()
    decision = propose_certified_strategy_one_entry(
        candidate, fact, activation, financial, strategy_number=13, momentum=witness)
    assert (decision.proposal is not None) is allowed
    if allowed:
        assert decision.proposal.momentum == witness
    gate = compile_static_entry_gate(plans[1], _entry(plans), strategy_number=13,
                                     momentum_plan=momentum)
    assert gate.rejection_mask.tolist() == [0 if allowed else RISING_MOMENTUM_REQUIRED]
    assert len(reader.queries) == 1
    assert plans[0].units[0].attempt_id in reader.queries[0]
    with pytest.raises(ValueError):
        momentum.current_line.setflags(write=True)


def test_missing_adjacent_prior_never_carries_older_source():
    plans = sources()
    reader = ArrowSource(missing=((1_000, 30_000), (10_000, 20_000)))
    momentum = load_rising_momentum_plan(*plans[:2], client=reader)
    assert momentum.eligible_mask().tolist() == [False]
    assert momentum.lookup("AAA", 31_000).observations[0].prior_boundary_ms == 0
    assert "(1000,14428)" not in reader.queries[0]


def test_attempt_drift_missing_plan_and_cross_key_fail_before_admission():
    plans = sources()
    reader = ArrowSource()
    altered = replace(plans[1], coverage=(replace(plans[1].coverage[0],
        source_attempts=(str(uuid4()), str(uuid4()), str(uuid4()))),))
    with pytest.raises(ValueError, match="attempt"):
        load_rising_momentum_plan(plans[0], altered, client=reader)
    assert not reader.queries
    momentum = load_rising_momentum_plan(*plans[:2], client=reader)
    with pytest.raises(ValueError, match="outside"):
        momentum.lookup("AAA", 31_100)
    with pytest.raises(ValueError, match="exact certified"):
        compile_static_entry_gate(plans[1], _entry(plans), strategy_number=13)
    assert compile_static_entry_gate(plans[1], _entry(plans), strategy_number=12).rejection_mask.tolist() == [0]
    with pytest.raises(ValueError, match="sealed"):
        replace(momentum, current_line=momentum.current_line + 1)


def test_loading_is_bounded_by_unique_source_chunks_not_candidate_rows():
    plans = sources(tuple(range(31_000, 231_000, 100)))
    reader = ArrowSource()
    momentum = load_rising_momentum_plan(*plans[:2], client=reader)
    assert len(momentum.keys) == 2_000
    assert momentum.eligible_mask().all()
    assert len(reader.queries) == 1
    assert all(len(re.findall(r"\((\d+),(\d+)\)", sql)) <= 512 for sql in reader.queries)

@pytest.mark.parametrize("falling,expected", [(False, 1), (True, 0)])
def test_real_coordinator_uses_exact_momentum_plan(falling, expected):
    import asyncio
    from src.backend.backtest_strategy_one_activation import StrategyOneActivation
    from src.backend.backtest_strategy_one_coordinator import run_strategy_one_proposals
    from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
    from src.backend.backtest_strategy_one_scheduler import StrategyOneBoundaryScheduler
    plans = sources()
    momentum = load_rising_momentum_plan(*plans[:2], client=ArrowSource(falling=falling))
    candidate, fact, activation, financial = _facts()
    entry = CertifiedEntryEvidencePlan(plans[0].build_id, "2026-08-18", (),
                                       (activation,), (fact,), "e" * 64)
    proposals = []

    async def noop(*args):
        pass

    async def views(*args):
        return (financial,)

    async def proposal(value):
        proposals.append(value)

    scheduler = StrategyOneBoundaryScheduler(
        session_date="2026-08-18", candidate_rows=iter((candidate,)),
        activation_rows=iter((StrategyOneActivation(30_000, "AAA", 100_000),)),
        active_source=lambda *args: iter(()))
    counts = asyncio.run(run_strategy_one_proposals(
        scheduler, entry, process_broker_boundary=noop, financial_views=views,
        on_entry_proposal=proposal, on_management=noop,
        position_source_owned=lambda view: False, financially_active_tickers=lambda: (),
        finish_boundary=noop, observe_activation=noop, observe_completed_seconds=noop,
        strategy_number=13, momentum_plan=momentum))
    assert counts.entry_proposals == expected
    assert all(row.strategy_number == 13 and row.momentum == momentum.lookup("AAA", 31_000)
               for row in proposals)


def test_duplicate_source_and_forged_build_identity_rejected():
    plans = sources()
    with pytest.raises(ValueError):
        load_rising_momentum_plan(*plans[:2], client=ArrowSource(duplicate=True))
    momentum = load_rising_momentum_plan(*plans[:2], client=ArrowSource())
    with pytest.raises(ValueError, match="sealed"):
        replace(momentum, source_build_id=str(uuid4()))


def test_projected_requests_preserve_full_gate_and_fail_closed_if_omitted():
    plans = sources((31_000, 31_100))
    entry = _entry(plans)
    entry = replace(entry, candidates=(entry.candidates[0], replace(
        entry.candidates[0], boundary_ms=31_100, protection_valid=False)))
    base = compile_static_entry_gate(plans[1], entry, strategy_number=12)
    assert base.eligible_indices.tolist() == [0]
    full = load_rising_momentum_plan(*plans[:2], client=ArrowSource())
    projected = load_rising_momentum_plan(*plans[:2], client=ArrowSource(),
                                         candidate_indices=base.eligible_indices)
    full_gate = compile_static_entry_gate(plans[1], entry, strategy_number=13,
                                         momentum_plan=full)
    projected_gate = compile_static_entry_gate(plans[1], entry, strategy_number=13,
                                              momentum_plan=projected)
    assert projected_gate.eligible_indices.tolist() == full_gate.eligible_indices.tolist()
    assert projected.requested_mask.tolist() == [True, False]
    assert np.isnan(projected.current_line[1]).all()
    assert not projected.current_boundaries_ms[1].any()
    with pytest.raises(ValueError):
        projected.requested_mask.setflags(write=True)
    with pytest.raises(ValueError, match="not requested"):
        projected.lookup("AAA", 31_100)
    reader = ArrowSource()
    empty = load_rising_momentum_plan(*plans[:2], client=reader,
                                     candidate_indices=np.array([], dtype=np.int64))
    assert not reader.queries
    with pytest.raises(ValueError, match="omit base-eligible"):
        compile_static_entry_gate(plans[1], entry, strategy_number=13, momentum_plan=empty)


@pytest.mark.parametrize("indices", [np.array([0, 0]), np.array([-1]),
                                      np.array([1]), np.array([0.0])])
def test_projection_indices_must_be_exact_bounded_keys(indices):
    plans = sources()
    reader = ArrowSource()
    with pytest.raises(ValueError, match="indices"):
        load_rising_momentum_plan(*plans[:2], client=reader, candidate_indices=indices)
    assert not reader.queries
