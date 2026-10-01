from datetime import date
from pathlib import Path
from dataclasses import replace

import numpy as np
import pytest
import torch

from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.training import TeacherDecision, ExecutionOutcome, train_session
from research.rl_trading.v6.wait_hold_supervision import split_wait_hold
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic


def labels():
    flat = TeacherDecision(1_000_000, 0, 0, np.ones(7, dtype=np.float32),
        np.empty(0, dtype=np.int64), np.empty((0, 11), dtype=np.float32),
        np.ones(2, dtype=bool), *(np.empty(0, dtype=bool) for _ in range(3)))
    held = replace(flat, close_us=2_000_000,
        held_index=np.array([0, 1]), held_features=np.ones((2, 11), dtype=np.float32),
        enter_allowed=np.zeros(2, dtype=bool), exit_allowed=np.ones(2, dtype=bool),
        stop_allowed=np.ones(2, dtype=bool), target_allowed=np.ones(2, dtype=bool))
    exit_row = replace(held, order_index=1, token=3)
    outcome = ExecutionOutcome(2_000_000, 1, 2_100_000, 2, 0, 1., 1., 0.)
    return (flat, held, exit_row), (outcome,)


def test_all_held_identities_are_supervised_with_preserved_weight_and_outcome_keys():
    source, outcomes = labels()
    migrated, fills = split_wait_hold(source, outcomes, 2)
    assert [d.token for d in migrated] == [0, 9, 10, 3]
    assert [d.order_index for d in migrated] == [0, 0, 1, 2]
    assert [d.sample_weight for d in migrated] == [1., .5, .5, 1.]
    assert sum(d.sample_weight for d in migrated) == len(source)
    assert fills[0].source_order_index == 2
    assert source[1].token == 0 and outcomes[0].source_order_index == 1
    assert all(np.array_equal(d.account, source[1].account) for d in migrated[1:])


@pytest.mark.parametrize('evaluation', [False, True])
def test_real_ranked_teacher_optimizer_and_evaluation_report_six_separate_classes(evaluation):
    clock = np.array([1_000_000, 2_000_000, 3_000_000]*2)
    scalar = np.zeros((6, 37), dtype=np.float32)
    scalar[:, 0] = .1
    bank = SessionBank(Path('unused'), {'offsets':{'A':[0,3], 'B':[3,6]}},
        clock, scalar, np.zeros((6, 2, 5, 11), dtype=np.float32))
    session = PackedSession(date(2026, 7, 31), 'development' if evaluation else 'train',
        Path('unused'), 'certificate', bank, None, ('A','B'))
    decisions, outcomes = split_wait_hold(*labels(), 2)
    policy = RankedBracketActorCritic(8, wait_hold=True)
    optimizer = torch.optim.AdamW(policy.parameters(), lr=.001)
    before = {k:v.clone() for k,v in policy.state_dict().items()}
    result = train_session(policy, optimizer, session, decisions, outcomes,
        device=torch.device('cpu'), clocks_per_chunk=3, evaluation=evaluation,
        teacher_loss='balanced-v2')
    assert result.action_class_counts == dict(wait=1, enter_long=0, exit_long=1,
                                             set_stop=0, set_target=0, hold=2)
    assert set(result.action_class_recall) == set(result.action_class_counts)
    assert result.hold_token_accuracy is not None
    assert np.isfinite(result.mean_loss)
    if evaluation:
        assert result.optimizer_steps == 0 and not optimizer.state
        assert all(torch.equal(before[k],v) for k,v in policy.state_dict().items())
    else:
        assert result.optimizer_steps == 1 and optimizer.state
        assert not torch.equal(before['decoder.wait_head.weight'], policy.decoder.wait_head.weight)
        assert not torch.equal(before['decoder.hold_head.weight'], policy.decoder.hold_head.weight)
