"""First-setup refinement cannot substitute current growth for its anchor."""
from dataclasses import replace

import numpy as np
import pyarrow as pa
import pytest

from test_backtest_strategy_initial_momentum import plans, Source
from test_backtest_strategy_rising_momentum import sources
from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
from src.backend.backtest_strategy_initial_momentum_growth import (
    compile_initial_momentum_growth_plan,
)


class StrongFirst(Source):
    def iter_arrow_record_batches(self, sql):
        for batch in super().iter_arrow_record_batches(sql):
            rows = batch.to_pylist()
            for row in rows:
                if row["resolution_ms"] == 10_000:
                    row["macd_line"] = {20_000: 1.0, 30_000: 1.6,
                                        40_000: 1.8}[row["boundary_ms"]]
            yield pa.RecordBatch.from_pylist(rows, schema=batch.schema)


class StrongLater(Source):
    def iter_arrow_record_batches(self, sql):
        for batch in super().iter_arrow_record_batches(sql):
            rows = batch.to_pylist()
            for row in rows:
                if row["resolution_ms"] == 10_000:
                    row["macd_line"] = {20_000: 1.0, 30_000: 1.2,
                                        40_000: 1.9}[row["boundary_ms"]]
            yield pa.RecordBatch.from_pylist(rows, schema=batch.schema)


def market_for(candidates):
    market = sources((31_000, 41_000))[0]
    return replace(market, units=(replace(market.units[0],
        attempt_id=candidates.coverage[0].source_attempts[1]),))


def test_weak_first_cannot_be_replaced_by_later_strong_candidate():
    candidates, entry, _, _ = plans()
    source = StrongLater()
    momentum = load_rising_momentum_plan(market_for(candidates), candidates, client=source)
    # Later58% cannot replace the frozen first20% result for the episode.
    refined = compile_initial_momentum_growth_plan(candidates, entry, momentum)
    assert refined.initial.eligible_mask.tolist() == [True, True]
    assert refined.eligible_mask.tolist() == [False, False]
    assert refined.initial.first_indices.tolist() == [0, 0]
    with pytest.raises(ValueError, match="outside admitted"):
        refined.lookup("AAA", 41_000)
    assert len(source.queries) == 1


def test_strong_first_preserves_later_current_ten_percent_and_source_identity():
    candidates, entry, _, _ = plans()
    source = StrongFirst()
    momentum = load_rising_momentum_plan(market_for(candidates), candidates, client=source)
    refined = compile_initial_momentum_growth_plan(candidates, entry, momentum)
    assert refined.eligible_mask.tolist() == [True, True]
    assert refined.candidates is candidates and refined.entry is entry
    assert refined.momentum is momentum
    anchor = refined.lookup("AAA", 41_000)
    assert anchor.first_setup == momentum.lookup("AAA", 31_000)
    selection = refined.selection_witness("AAA", 41_000)
    assert selection.selection_token == refined.token
    assert selection.initial == anchor
    assert refined.token != refined.initial.token
    assert len(source.queries) == 1
    with pytest.raises(ValueError):
        refined.eligible_mask.setflags(write=True)
    with pytest.raises(ValueError, match="content seal"):
        replace(refined, token="f" * 64)
    with pytest.raises(ValueError, match="eligibility differs"):
        replace(refined, eligible_mask=np.array([True, False]))


def test_refinement_rejects_untyped_parent_before_source_read():
    from src.backend.backtest_strategy_initial_momentum_growth import CertifiedInitialMomentumGrowthPlan
    with pytest.raises(ValueError, match="exact certified"):
        CertifiedInitialMomentumGrowthPlan(object(), np.array([], dtype=bool), "f" * 64)
