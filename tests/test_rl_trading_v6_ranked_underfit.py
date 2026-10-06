import copy
from dataclasses import asdict, replace
import numpy as np
import pytest
import torch
from research.rl_trading.v6.run_ranked_teacher_underfit import passes, select_targets
from research.rl_trading.v6.probe_teacher_sequence import subset
from research.rl_trading.v6.teacher_forecast import configure
from research.rl_trading.v6.temporal_encoders import replace_encoder
from research.rl_trading.v6.training import train_session
from test_rl_trading_v6_teacher_forecast import fixture


def test_ranked_gate_requires_every_actual_head_and_coverage():
    names = ('wait', 'enter_long', 'hold', 'exit_long')
    report = dict(action_class_counts={n: 2 for n in names}, action_class_f1={n: 1. for n in names},
        allocation_targets=2, allocation_ratio_mae=.01, action_quality_mae={'ENTRY': .01, 'EXIT': .01},
        forecast_label_metrics=[{'labels': {n: dict(count=2, f1=1.) for n in ('ENTRY', 'WAIT', 'HOLD', 'EXIT')}} for _ in range(5)],
        forecast_quality_mae=[{'ENTRY': .01, 'EXIT': .01} for _ in range(5)])
    assert passes(report)
    for key in ('action_class_counts', 'action_class_f1', 'allocation_ratio_mae', 'forecast_label_metrics', 'forecast_quality_mae', 'action_quality_mae'):
        bad = copy.deepcopy(report)
        if key == 'allocation_ratio_mae': bad[key] = float('nan')
        elif key == 'action_class_counts': bad[key]['hold'] = 0
        elif key == 'action_class_f1': bad[key]['wait'] = .9
        elif key == 'action_quality_mae': bad[key]['ENTRY'] = .03
        else: bad[key] = []
        assert not passes(bad)


@pytest.mark.parametrize('device', ['cpu'] + (['cuda'] if torch.cuda.is_available() else []))
def test_real_ranked_train_keeps_untargeted_market_inputs_and_reloads(device):
    policy, session, labels = fixture(device)
    replace_encoder(policy, 'tcn', structured=True)
    configure(policy, hierarchical=True, shared_heads=False)
    labels = tuple(replace(d, forecast_actions=np.zeros(len(d.forecast_close_us), np.int64)) for d in labels)
    packed, targets = subset(session, labels, session.listings, 3_000_000, 7_000_000)
    assert packed.listings == session.listings == ('A', 'B')
    np.testing.assert_array_equal(packed.bank.listing('B').scalar, session.bank.listing('B').scalar[:7])
    assert all(d.enter_allowed.shape == (2,) and d.soft_tokens == (0, 1) for d in targets)
    optimizer = torch.optim.AdamW(policy.parameters(), lr=.001)
    before = copy.deepcopy(policy.state_dict())
    result = train_session(policy, optimizer, packed, targets, (), device=torch.device(device),
        teacher_loss='branch-balanced-v3', regression_weights=(0., 0.))
    assert result.decisions == 5 and result.allocation_targets == 5
    assert result.forecast_targets == (5, 5, 5, 5, 5)
    assert any(not torch.equal(before[n], p) for n, p in policy.state_dict().items())
    metric = asdict(train_session(policy, None, packed, targets, (), device=torch.device(device),
        evaluation=True, evaluate_train=True))
    assert not passes(metric)  # Missing held classes never certify all-head learnability.
    restored, _, _ = fixture(device)
    replace_encoder(restored, 'tcn', structured=True)
    configure(restored, hierarchical=True, shared_heads=False)
    restored.load_state_dict(copy.deepcopy(policy.state_dict()))
    assert metric == asdict(train_session(restored, None, packed, targets, (), device=torch.device(device), evaluation=True, evaluate_train=True))


def test_target_sampler_fails_closed_on_absent_held_forecasts():
    _, session, labels = fixture()
    with pytest.raises(ValueError, match='Source lacks'):
        select_targets(labels, len(session.listings), 16)
