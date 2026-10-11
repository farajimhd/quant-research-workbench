from dataclasses import replace

import numpy as np
import pytest

from src.backend.backtest_native_channel_participant_scope import native_channel_participant_tickers
from src.backend.backtest_strategy_one_candidate_store import CandidateCoverage, CertifiedCandidatePlan, _token
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker


def certified_plan():
    prepared = []
    coverage = []
    for ticker, boundaries in [('PM', [100, 200]), ('RTH', [300, 400]), ('AH', [500, 600])]:
        column = np.array(boundaries, dtype=np.int64)
        prepared.append(PreparedStrategyOneTicker(ticker, 2, column, column,
            column, np.zeros((2, 4), dtype=np.int64), column, column))
        coverage.append(CandidateCoverage('2026-08-04', ticker, 'attempt',
                                         ('bars', 'technical', 'broker'), 2, 'a' * 64))
    coverage = tuple(coverage)
    return CertifiedCandidatePlan('build', 'rule', 'scan', coverage, tuple(prepared),
                                 _token('build', 'rule', 'scan', coverage))


@pytest.mark.parametrize('start,end,expected', [
    (0, 200, ('PM',)), (400, 600, ('AH',)), (200, 300, ('RTH',)),
    (100, 200, ('PM',)), (600, 700, ()),
])
def test_exact_run_window_and_unchanged_parent(start, end, expected):
    plan = certified_plan()
    token = plan.token
    assert native_channel_participant_tickers(plan, flat_start_boundary_ms=start,
                                            through_boundary_ms=end) == expected
    assert plan.token == token
    assert len(plan.prepared) == 3


def test_rejects_uncertified_or_unordered_parent():
    plan = certified_plan()
    with pytest.raises(ValueError):
        native_channel_participant_tickers(replace(plan, token='bad'),
            flat_start_boundary_ms=0, through_boundary_ms=200)
    plan.prepared[0].boundary_ms[:] = [200, 100]
    with pytest.raises(ValueError):
        native_channel_participant_tickers(plan, flat_start_boundary_ms=0,
                                          through_boundary_ms=200)


@pytest.mark.parametrize('start,end', [(True, 200), (100, 100), (1, 200), (0, 201)])
def test_rejects_invalid_window(start, end):
    with pytest.raises(ValueError):
        native_channel_participant_tickers(certified_plan(),
            flat_start_boundary_ms=start, through_boundary_ms=end)


def test_empty_session_proof_rejects_participants_and_all_position_lookups():
    from src.backend.backtest_native_channel_participant_scope import EmptyNativeChannelParticipantSession
    empty=EmptyNativeChannelParticipantSession(certified_plan(),600,700)
    with pytest.raises(ValueError,match='cannot hold a position'):
        empty.sequence_for('AH')
    with pytest.raises(ValueError,match='possible entry participants'):
        EmptyNativeChannelParticipantSession(certified_plan(),400,600)
