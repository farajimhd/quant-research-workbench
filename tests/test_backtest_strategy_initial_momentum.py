"""First-setup anchors bind full certified evidence before financial selection."""
from dataclasses import replace

import numpy as np
import pyarrow as pa
import pytest

from test_backtest_strategy_rising_momentum import ArrowSource, sources, _entry
from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
from src.backend.backtest_strategy_initial_momentum import compile_initial_momentum_plan


class Source(ArrowSource):
    def __init__(self, weak_first=False):
        super().__init__()
        self.weak_first = weak_first

    def iter_arrow_record_batches(self, sql):
        for batch in super().iter_arrow_record_batches(sql):
            rows = batch.to_pylist()
            for row in rows:
                if row["resolution_ms"] == 10_000:
                    values = {20_000: 1.0, 30_000: 1.05 if self.weak_first else 1.2,
                              40_000: 1.5}
                    row["macd_line"] = values[row["boundary_ms"]]
            yield pa.RecordBatch.from_pylist(rows, schema=batch.schema)


def plans(weak_first=False, first_invalid=False):
    parent = sources((31_000, 41_000))
    entry = _entry(parent)
    facts = tuple(replace(entry.candidates[0], boundary_ms=at,
                          protection_valid=not (first_invalid and at == 31_000))
                  for at in (31_000, 41_000))
    entry = replace(entry, candidates=facts)
    source = Source(weak_first)
    momentum = load_rising_momentum_plan(*parent[:2], client=source)
    return parent[1], entry, momentum, source


def test_weak_initial_setup_cannot_resurrect_at_later_strong_entry():
    candidates, entry, momentum, source = plans(weak_first=True)
    assert momentum.eligible_mask(17).tolist() == [False, True]
    initial = compile_initial_momentum_plan(candidates, entry, momentum)
    assert initial.first_indices.tolist() == [0, 0]
    assert initial.eligible_mask.tolist() == [False, False]
    with pytest.raises(ValueError, match="outside admitted"):
        initial.lookup("AAA", 41_000)
    assert len(source.queries) == 1  # Compiler performs no additional source I/O.


def test_first_structural_setup_skips_invalid_candidate_and_preserves_scalar_identity():
    candidates, entry, momentum, _ = plans(weak_first=True, first_invalid=True)
    initial = compile_initial_momentum_plan(candidates, entry, momentum)
    assert initial.first_indices.tolist() == [1, 1]
    assert initial.eligible_mask.tolist() == [False, True]
    anchor = initial.lookup("AAA", 41_000)
    assert anchor.first_setup == momentum.lookup("AAA", 41_000)
    assert anchor.episode_start_ms == 30_000
    for array in (initial.first_indices, initial.eligible_mask, initial.episode_start_ms):
        with pytest.raises(ValueError):
            array.setflags(write=True)


def test_plan_reconstruction_rejects_anchor_mask_seal_and_source_drift():
    candidates, entry, momentum, _ = plans()
    initial = compile_initial_momentum_plan(candidates, entry, momentum)
    with pytest.raises(ValueError, match="selection differs"):
        replace(initial, first_indices=np.array([0, 1], dtype=np.int64))
    with pytest.raises(ValueError, match="selection differs"):
        replace(initial, eligible_mask=np.array([True, False]))
    with pytest.raises(ValueError, match="content seal"):
        replace(initial, token="f" * 64)
    with pytest.raises(ValueError, match="exact certified"):
        compile_initial_momentum_plan(replace(candidates, token="f" * 64), entry, momentum)
    with pytest.raises(ValueError, match="exact certified"):
        compile_initial_momentum_plan(candidates, replace(entry, token="bad"), momentum)


def test_future_candidates_do_not_change_earlier_anchor_or_admission():
    candidates, entry, momentum, _ = plans()
    full = compile_initial_momentum_plan(candidates, entry, momentum)
    prepared = replace(candidates.prepared[0], boundary_ms=np.array([31_000]),
                       episode_start_ms=np.array([30_000]))
    prefix_candidates = replace(candidates, prepared=(prepared,))
    parent = sources((31_000,))
    market = replace(parent[0], units=(replace(parent[0].units[0],
        attempt_id=candidates.coverage[0].source_attempts[1]),))
    prefix_momentum = load_rising_momentum_plan(market, prefix_candidates, client=Source())
    prefix = compile_initial_momentum_plan(prefix_candidates, entry, prefix_momentum)
    assert prefix.first_indices.tolist() == full.first_indices[:1].tolist()
    assert prefix.eligible_mask.tolist() == full.eligible_mask[:1].tolist()
    assert prefix.lookup("AAA", 31_000) == full.lookup("AAA", 31_000)


def test_unrequested_base_setup_fails_closed():
    candidates, entry, _, _ = plans()
    parent = sources((31_000, 41_000))
    market = replace(parent[0], units=(replace(parent[0].units[0],
        attempt_id=candidates.coverage[0].source_attempts[1]),))
    momentum = load_rising_momentum_plan(market, candidates, client=Source(),
                                        candidate_indices=np.array([1], dtype=np.int64))
    with pytest.raises(ValueError, match="omits"):
        compile_initial_momentum_plan(candidates, entry, momentum)
