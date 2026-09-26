"""Exact sparse Strategy 1 candidate scalar rows, including empty coverage."""
from dataclasses import replace

import numpy as np
import pytest

from src.backend.backtest_strategy_one_candidate_contract import (
    VALUE_FIELDS, candidate_content_hash, project_candidate_rows,
)
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker


ATTEMPT = "00000000-0000-0000-0000-000000000001"


def _prepared():
    return PreparedStrategyOneTicker(
        "ABCD", 500, np.array([10, 21], dtype=np.int64),
        np.array([30_000, 60_000], dtype=np.int64),
        np.array([29_900, 59_900], dtype=np.int64),
        np.array([[30_000, 30_000, 30_000, 30_000],
                  [60_000, 60_000, 60_000, 60_000]], dtype=np.int64),
        np.array([30_000, 60_000], dtype=np.int64),
        np.array([150_000, 160_000], dtype=np.int64),
    )


def test_candidate_rows_are_flat_exact_and_ordered():
    rows = project_candidate_rows(_prepared(), build_id="build",
                                  session_date="2026-08-18",
                                  derivation_attempt_id=ATTEMPT)
    assert len(rows) == 2
    assert set(rows[0]) == set(VALUE_FIELDS) | {
        "source_build_id", "session_date", "ticker", "derivation_attempt_id"}
    assert rows[0]["stop_low_int"] == 150_000
    assert candidate_content_hash(rows) == candidate_content_hash(tuple(dict(row) for row in rows))
    changed = [dict(row) for row in rows]
    changed[1]["stop_low_int"] += 1
    assert candidate_content_hash(rows) != candidate_content_hash(tuple(changed))


def test_empty_candidate_scope_has_stable_nonempty_seal():
    prepared = _prepared()
    empty = replace(prepared, row_index=np.empty(0, dtype=np.int64),
                    boundary_ms=np.empty(0, dtype=np.int64),
                    episode_start_ms=np.empty(0, dtype=np.int64),
                    macd_boundary_ms=np.empty((0, 4), dtype=np.int64),
                    stop_bar_boundary_ms=np.empty(0, dtype=np.int64),
                    stop_low_int=np.empty(0, dtype=np.int64))
    assert project_candidate_rows(empty, build_id="build",
                                  session_date="2026-08-18",
                                  derivation_attempt_id=ATTEMPT) == ()
    assert len(candidate_content_hash(())) == 64


def test_candidate_projection_rejects_future_macd_and_duplicate_order():
    prepared = _prepared()
    future = replace(prepared, macd_boundary_ms=np.array(
        [[31_000, 30_000, 30_000, 30_000],
         [60_000, 60_000, 60_000, 60_000]], dtype=np.int64))
    with pytest.raises(ValueError, match="causal"):
        project_candidate_rows(future, build_id="build",
                               session_date="2026-08-18",
                               derivation_attempt_id=ATTEMPT)
    duplicate = replace(prepared, row_index=np.array([10, 10], dtype=np.int64))
    with pytest.raises(ValueError, match="causal"):
        project_candidate_rows(duplicate, build_id="build",
                               session_date="2026-08-18",
                               derivation_attempt_id=ATTEMPT)
