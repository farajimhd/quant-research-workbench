"""Real certified parent fixtures; no installed feature or financial claim."""
import numpy as np
import polars as pl
import pytest

from tests.test_backtest_declared_native_fixed_entry import parent, Source  # noqa: F401
from src.backend.backtest_declared_native_fixed_plan import load_declared_entry_source_plan
from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
from src.backend.backtest_native_candidate_feature_scope import (
    declared_candidate_feature_decisions, require_complete_candidate_feature_scope,
)


@pytest.fixture
def source(parent):
    return load_declared_entry_source_plan(parent, client=Source(parent))


def test_complete_parent_population_and_original_clock_attempts(source):
    before = source.eligible_mask.copy()
    rows = declared_candidate_feature_decisions(source, max_rows=1000)
    assert rows.height == len(source.parent.momentum.keys)
    assert list(zip(rows['ticker'], rows['decision_day_ms'])) == [
        (ticker, boundary + SESSION_OPEN_OFFSET_MS)
        for ticker, boundary in source.parent.momentum.keys]
    assert rows['attempt_id'].to_list() == list(source.source_attempts[0])
    assert rows['build_id'].unique().to_list() == [source.parent.market.build_id]
    np.testing.assert_array_equal(source.eligible_mask, before)
    assert require_complete_candidate_feature_scope(source, rows, max_rows=1000).equals(rows)


@pytest.mark.parametrize('change', ['omit', 'reverse', 'attempt', 'clock', 'label'])
def test_partial_or_replaced_populations_cannot_supply_feature_scope(source, change):
    rows = declared_candidate_feature_decisions(source, max_rows=1000)
    altered = {
        'omit': lambda: rows.head(rows.height - 1),
        'reverse': rows.reverse,
        'attempt': lambda: rows.with_columns(pl.lit('foreign').alias('attempt_id')),
        'clock': lambda: rows.with_columns((pl.col('decision_day_ms') + 100).alias('decision_day_ms')),
        'label': lambda: rows.with_columns(pl.lit(1).alias('future_winner')),
    }[change]()
    with pytest.raises(ValueError, match='complete certified candidate population'):
        require_complete_candidate_feature_scope(source, altered, max_rows=1000)


def test_bound_rejects_complete_population_instead_of_truncating(source):
    with pytest.raises(ValueError, match='exceeds declared bound'):
        declared_candidate_feature_decisions(source, max_rows=1)
    with pytest.raises(ValueError, match='positive candidate feature row bound'):
        declared_candidate_feature_decisions(source, max_rows=True)
