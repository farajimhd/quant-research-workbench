"""Real certified parent fixtures; no installed feature or financial claim."""
import numpy as np
import polars as pl
import pytest
from dataclasses import replace

from tests.test_backtest_declared_native_fixed_entry import parent, Source  # noqa: F401
from src.backend.backtest_declared_native_fixed_plan import (
    load_declared_entry_source_plan, compile_declared_momentum_plan,
)
from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
from src.backend.backtest_native_candidate_feature_scope import (
    declared_candidate_feature_decisions, require_complete_candidate_feature_scope,
    DeclaredParticipationRead, prepare_declared_native_participation_scores,
)
from tests.test_native_channel_campaign import unit  # noqa: F401
from src.market_engine.native_causal_channel_contract import NativeChannelPolicy, NativeChannelRequest
from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy
from src.backend.backtest_native_participation_ranking import PreparedParticipationScores


@pytest.fixture
def source(parent):
    return load_declared_entry_source_plan(parent, client=Source(parent))


@pytest.fixture
def feature_source(parent):
    # The older controlled source fixture predates canonical UInt64 bars
    # checksums. Fix fixture metadata, not NativeChannelRequest validation.
    market = replace(parent.market, units=tuple(
        replace(unit, output_hash='1') if unit.stage == 'bars' else unit
        for unit in parent.market.units))
    current = compile_declared_momentum_plan(parent.capabilities, market,
        parent.candidates, parent.entry, parent.momentum)
    return load_declared_entry_source_plan(current, client=Source(current))


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


def read_for(source, authority):
    resolution = source.parent.market.required_resolutions_ms[0]
    rows = declared_candidate_feature_decisions(source, max_rows=1000)
    request = NativeChannelRequest(source.parent.market, source.parent.market.sessions[0],
        tuple(sorted(set(rows['ticker']))), int(rows['decision_day_ms'].max()),
        NativeChannelPolicy((resolution,), 100, ((resolution, resolution),), 5, 5))
    return DeclaredParticipationRead(request, '11111111-1111-4111-8111-111111111111',
        'a' * 64, authority), ParticipationRankingPolicy(((resolution, 1.),), .5, .5, 1., 1, 1000)


def test_bound_preparation_preserves_complete_order_and_unknowns(feature_source, unit, monkeypatch):
    source = feature_source
    from src.backend import backtest_native_candidate_feature_scope as module
    read, policy = read_for(source, unit[1])
    calls = []
    def score(client, request, attempt, decisions, mandatory, **kwargs):
        calls.append(decisions)
        values = np.arange(decisions.height, dtype=np.float64)
        known = mandatory.copy()
        known[0] = False
        values[~known] = np.nan
        return PreparedParticipationScores(values, known, mandatory)
    monkeypatch.setattr(module, 'prepare_installed_participation_scores', score)
    output = prepare_declared_native_participation_scores(object(), source, (read,), policy=policy)
    assert len(calls) == 1
    assert calls[0].equals(declared_candidate_feature_decisions(source, max_rows=1000))
    np.testing.assert_array_equal(output.mandatory_eligible, source.eligible_mask)
    assert not output.available[0] and np.isnan(output.scores[0])
    assert np.isnan(output.scores[~output.available]).all()
    for column in output:
        with pytest.raises(ValueError): column.setflags(write=True)


def test_repeated_packet_scope_fails_before_feature_transport(feature_source, unit, monkeypatch):
    source = feature_source
    from src.backend import backtest_native_candidate_feature_scope as module
    read, policy = read_for(source, unit[1])
    monkeypatch.setattr(module, 'prepare_installed_participation_scores',
        lambda *a, **k: pytest.fail('Invalid roster must fail before source SELECT'))
    with pytest.raises(ValueError, match='repeats certified candidate scope'):
        prepare_declared_native_participation_scores(object(), source, (read, read), policy=policy)
