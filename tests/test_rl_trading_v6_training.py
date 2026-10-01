from datetime import date
from pathlib import Path

import numpy as np
import torch
import pytest

from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.model import BracketPolicy
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.training import (ExecutionOutcome,
                                             TeacherDecision, train_session)


@pytest.mark.parametrize('policy_type', [BracketPolicy, RankedBracketActorCritic])
@pytest.mark.parametrize('evaluation', [False, True])
def test_v6_chronological_train_core_updates_encoder_and_bracket_heads(policy_type, evaluation):
    clocks = np.asarray([1_000_000, 2_000_000, 1_000_000, 2_000_000],
                        dtype=np.int64)
    scalar = np.zeros((4, 37), dtype=np.float32)
    scalar[:, 0] = [.1, .2, .3, .4]
    bank = SessionBank(Path('unused'),
        {'offsets': {'A': [0, 2], 'B': [2, 4]}}, clocks,
        scalar, np.zeros((4, 2, 5, 11), dtype=np.float32))
    session = PackedSession(date(2026, 7, 31), 'development' if evaluation else 'train', Path('unused'),
                            'certificate', bank, None, ('A', 'B'))
    decisions = (
        TeacherDecision(1_000_000, 0, 1,
            np.asarray([10000., 10000., 0., 0., 0., 0., 0.], dtype=np.float32),
            np.empty(0, dtype=np.int64), np.empty((0, 9), dtype=np.float32),
            np.asarray([True, False]), np.empty(0, dtype=bool),
            np.empty(0, dtype=bool), np.empty(0, dtype=bool),
            size_fraction=.25),
        TeacherDecision(2_000_000, 0, 4,
            np.asarray([7500., 10000., 0., .25, 1., 0., 0.], dtype=np.float32),
            np.asarray([0], dtype=np.int64),
            np.asarray([[250., 10., 1., 0., 0., 0., 0., 0., 0.]],
                       dtype=np.float32),
            np.asarray([False, True]), np.asarray([True]),
            np.asarray([True]), np.asarray([True]),
            oracle_log_distance=.05),
    )
    outcomes = (ExecutionOutcome(1_000_000, 0, 1_100_000,
                                  1, 0, .25, .2, 0.),)
    policy = policy_type(width=8)
    before = policy.encoder.project.weight.detach().clone()
    all_before = {name:value.detach().clone() for name,value in policy.state_dict().items()}
    optimizer = torch.optim.AdamW(policy.parameters(), lr=.001)
    metrics = train_session(policy, optimizer, session, decisions, outcomes,
        device=torch.device('cpu'), clocks_per_chunk=2, evaluation=evaluation,
        learning_rate_for_clock=lambda clock: .000123)
    assert metrics.decisions == 2 and metrics.execution_outcomes == 1
    assert metrics.optimizer_steps == (0 if evaluation else 1)
    assert np.isfinite(metrics.mean_loss)
    assert metrics.action_class_counts['enter_long'] == 1
    assert metrics.action_class_counts['set_stop'] == 1
    assert sum(metrics.action_class_counts.values()) == metrics.decisions
    assert metrics.buy_size_mae is not None
    assert metrics.stop_log_distance_mae is not None
    assert metrics.target_log_distance_mae is None
    assert all(0 <= value <= 1 for value in metrics.action_class_f1.values())
    if evaluation:
        assert optimizer.param_groups[0]['lr']==.001
        assert all(torch.equal(value,policy.state_dict()[name]) for name,value in all_before.items())
        assert not optimizer.state
        assert all(parameter.grad is None for parameter in policy.parameters())
    else:
        assert optimizer.param_groups[0]['lr']==.000123
        assert not torch.equal(before, policy.encoder.project.weight)
