import numpy as np
import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from src.backend.backtest_native_channel_sequences import (
    NativeChannelSequencePolicy, prepare_installed_native_channel_sequence,
)
from src.backend.backtest_native_channel_session_source import PreparedNativeChannelSession
from src.backend.backtest_native_participation_ranking import prepare_installed_participation_scores
from src.backend.backtest_prepared_participation_scores import score_prepared_native_session
from src.market_engine.native_causal_channel_contract import issue_source_plan
from src.trading_runtime.native_completed_bar_sequences import CompletedBarSequencePolicy
from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy


def prepared(inputs):
    client, authority, packet, _, _ = inputs
    sequence = prepare_installed_native_channel_sequence(client, packet.request,
        packet.feature_attempt_id, policy=NativeChannelSequencePolicy(packet.request.policy,
            CompletedBarSequencePolicy(1, 1000), 60000, 'close_return', 'ge', 0., 1000, 'none'),
        producer_source_hash=issue_source_plan(packet).producer_source_hash, authority=authority)
    return PreparedNativeChannelSession(packet.request.tickers, (sequence,))


def policy():
    return ParticipationRankingPolicy(((60000, 1.),), .5, .5, 1., 1, 100)


def test_prepared_scores_match_installed_reader_without_new_transport(installed):
    session = prepared(installed)
    client, authority, packet, decisions, _ = installed
    mandatory = np.array([False, True, True, True])
    expected = prepare_installed_participation_scores(client, packet.request,
        packet.feature_attempt_id, decisions, mandatory, policy=policy(),
        producer_source_hash=issue_source_plan(packet).producer_source_hash, authority=authority)
    reads, writes = len(client.sql), len(client.writes)
    result = score_prepared_native_session(session, decisions, mandatory, policy=policy())
    for actual, wanted in zip(result, expected):
        np.testing.assert_array_equal(actual, wanted)
        assert not actual.flags.writeable
    assert len(client.sql) == reads and len(client.writes) == writes


def test_window_keeps_original_rows_and_scores_only_executable_candidates(installed):
    session = prepared(installed)
    client, _, _, decisions, _ = installed
    mandatory = np.ones(decisions.height, dtype=bool)
    expected = score_prepared_native_session(session, decisions, mandatory, policy=policy())
    # The excluded historical candidate belongs to a ticker absent from AH I/O.
    left = decisions.with_columns(pl.when(pl.col('decision_day_ms') == 60000)
        .then(pl.lit('PM_ONLY')).otherwise(pl.col('ticker')).alias('ticker'))
    reads = len(client.sql)
    actual = score_prepared_native_session(session, left, mandatory, policy=policy(),
        execution_window_day_ms=(60000, 480000))
    active = decisions['decision_day_ms'].to_numpy() > 60000
    np.testing.assert_array_equal(actual.scores[active], expected.scores[active])
    np.testing.assert_array_equal(actual.available[active], expected.available[active])
    assert np.isnan(actual.scores[~active]).all()
    assert not actual.available[~active].any()
    np.testing.assert_array_equal(actual.mandatory_eligible, mandatory)
    assert len(client.sql) == reads
    for values in actual:
        assert not values.flags.writeable
    with pytest.raises(ValueError, match='omits native candidate tickers'):
        score_prepared_native_session(session, left, mandatory, policy=policy(),
            execution_window_day_ms=(0, 480000))


def test_certified_empty_window_retains_unavailable_source_rows():
    from tests.test_native_channel_participant_scope import certified_plan
    from src.backend.backtest_native_channel_participant_scope import EmptyNativeChannelParticipantSession
    from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
    empty = EmptyNativeChannelParticipantSession(certified_plan(), 600, 700)
    left = pl.DataFrame({'build_id': ['build'], 'session_date': ['2026-08-04'],
        'ticker': ['PM'], 'attempt_id': ['attempt'],
        'decision_day_ms': [SESSION_OPEN_OFFSET_MS + 200]})
    window = (SESSION_OPEN_OFFSET_MS + 600, SESSION_OPEN_OFFSET_MS + 700)
    result = score_prepared_native_session(empty, left, np.ones(1, dtype=bool),
        policy=policy(), execution_window_day_ms=window)
    assert np.isnan(result.scores).all() and not result.available.any()
    assert result.mandatory_eligible.tolist() == [True]
    with pytest.raises(ValueError, match='empty session differs'):
        score_prepared_native_session(empty, left.with_columns(
            pl.lit(window[1], dtype=pl.Int64).alias('decision_day_ms')),
            np.ones(1, dtype=bool), policy=policy(), execution_window_day_ms=window)


@pytest.mark.parametrize('window', [(True, 1000), [0, 1000], (1000, 1000),
                                  (-100, 1000), (0, 1001), (0, 86400100)])
def test_invalid_execution_window_fails_closed(installed, window):
    with pytest.raises(ValueError, match='aligned native scoring execution window'):
        score_prepared_native_session(prepared(installed), installed[3],
            np.ones(4, dtype=bool), policy=policy(), execution_window_day_ms=window)


@pytest.mark.parametrize('mutation', ['ticker', 'attempt', 'future', 'duplicate', 'mask'])
def test_prepared_scoring_rejects_foreign_or_invalid_decisions_before_transport(installed, mutation):
    session = prepared(installed)
    client, _, _, decisions, _ = installed
    mandatory = np.ones(decisions.height, dtype=bool)
    if mutation == 'ticker':
        decisions = decisions.with_columns(pl.lit('FOREIGN').alias('ticker'))
    elif mutation == 'attempt':
        decisions = decisions.with_columns(pl.lit('FOREIGN').alias('attempt_id'))
    elif mutation == 'future':
        decisions = decisions.with_columns(pl.lit(86400000, dtype=pl.Int64).alias('decision_day_ms'))
    elif mutation == 'duplicate':
        decisions = pl.concat([decisions.head(1)] * decisions.height)
    else:
        mandatory = np.ones(decisions.height, dtype=np.int64)
    reads = len(client.sql)
    with pytest.raises(ValueError):
        score_prepared_native_session(session, decisions, mandatory, policy=policy())
    assert len(client.sql) == reads
