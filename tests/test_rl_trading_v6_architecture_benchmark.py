from types import SimpleNamespace as NS
from datetime import date
import numpy as np
import pytest
import torch
from research.rl_trading.v6.architecture_benchmark import Candidate, WindowTeacher, matched_sizes, loss, passes
from research.rl_trading.v6.architecture_benchmark_data import windows, label_blind_sample
from research.rl_trading.v6.run_architecture_benchmark import admit_validation, resume_optimizer


def normalizers():
    return dict(mean=[0.]*147, std=[1.]*147), dict(mean=[0.]*35, std=[1.]*35)


@pytest.mark.parametrize('architecture', ['tcn', 'gru', 'transformer'])
def test_scalable_encoder_backward_all_supervised_heads(architecture):
    torch.set_num_threads(2)
    base, extra = normalizers()
    small = WindowTeacher(Candidate(architecture, 32), base, extra)
    large = WindowTeacher(Candidate(architecture, 64), base, extra)
    assert sum(p.numel() for p in large.parameters()) > sum(p.numel() for p in small.parameters())
    raw = torch.zeros(4, 120, 147); raw[:, :, 35:37] = 1
    present = torch.ones(4, 120, dtype=torch.bool)
    held = torch.zeros(4, 11); held[2:, 0] = 1
    actions = torch.arange(4)
    future = torch.arange(4)[:, None].expand(4, 5)
    data = dict(action=actions, quality=torch.tensor([.7, 0., 0., .8]),
        ratio=torch.tensor([.5, float('nan'), float('nan'), float('nan')]),
        future=future, future_soft=torch.nn.functional.one_hot(future, 4).float())
    value = loss(small, small.representation(raw, present, held), data, torch.ones(4))
    assert torch.isfinite(value)
    value.backward()
    for module in (small.encoder.project, small.heads.entry, small.heads.exit,
                   small.heads.quality, small.size, small.forecast.reference_held, small.forecast_gru):
        assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in module.parameters())


def test_matched_parameter_budget_is_label_blind():
    rows = matched_sizes(*normalizers())
    assert {row['architecture'] for row in rows} == {'tcn', 'gru', 'transformer'}
    assert max(row['parameters'] for row in rows) / min(row['parameters'] for row in rows) <= 1.15


def test_snapshot_excludes_target_and_keeps_adjusted_prior_values():
    scalar = np.zeros((3, 37), np.float32); scalar[:, 3] = [1., 2., 999.]
    source = NS(close_us=np.array([1, 2, 3]), scalar=scalar, levels=np.zeros((3, 2, 5, 11), np.float32))
    bank = NS(listing=lambda identity: source)
    session = NS(role='train', day=date(2026, 7, 31), listings=('a',), bank=bank,
        previous=None, source_certificate_sha256='bank', context_split_receipt_sha256='split')
    target = NS(close_us=3, held_index=np.array([], int), soft_tokens=[0, 1], token=1,
        soft_probabilities=[0., .7], allocation_ratio_target=.5, forecast_actions=np.array([0]),
        forecast_probabilities=np.array([[.7, .3, 0., 0.]]), episode_uid='pair')
    item = windows(session, [target])
    assert item['raw'][0, -2:, 3].tolist() == [1., 2.]
    assert item['present'].sum() == 2
    assert item['max_input_close_us'] == [2]
    session.role = 'sealed_test'
    with pytest.raises(ValueError, match='sealed'):
        windows(session, [target])


def test_validation_gate_requires_every_head_and_reload():
    metrics = dict(current={name: dict(count=2, f1=1.) for name in ('ENTRY', 'WAIT', 'HOLD', 'EXIT')},
        forecast=[{name: dict(count=2, f1=1.) for name in ('ENTRY', 'WAIT', 'HOLD', 'EXIT')} for _ in range(5)],
        allocation_count=2, allocation_mae=.01, quality=dict(ENTRY=.01, EXIT=.01),
        forecast_quality=[dict(ENTRY=.01, EXIT=.01) for _ in range(5)])
    assert passes(metrics)
    admit_validation(metrics, True)
    with pytest.raises(ValueError):
        admit_validation(metrics, False)
    metrics['forecast'][4]['EXIT']['f1'] = .94
    assert not passes(metrics)
    with pytest.raises(ValueError):
        admit_validation(metrics, True)


def test_validation_sampling_ignores_labels():
    targets = [NS(close_us=i, episode_uid=str(i), held_index=[], soft_tokens=[0, 1], token=i % 4) for i in range(30)]
    first = [x.close_us for x in label_blind_sample(targets, '2026-08-24', 10)]
    for target in targets:
        target.token = 999
    assert first == [x.close_us for x in label_blind_sample(list(reversed(targets)), '2026-08-24', 10)]


def test_resume_at_phase_boundary_keeps_joint_optimizer():
    model = WindowTeacher(Candidate('tcn', 32), *normalizers())
    optimizer = resume_optimizer(model, 256, 256)
    assert all(p.requires_grad for p in model.parameters())
    assert len(optimizer.param_groups[0]['params']) == len(list(model.parameters()))
    optimizer = resume_optimizer(model, 257, 256)
    assert not any(p.requires_grad for p in model.encoder.parameters())
    assert optimizer.param_groups[0]['lr'] == .003


def test_checkpoint_restore_reestablishes_frozen_flags():
    model = WindowTeacher(Candidate('gru', 32), *normalizers())
    model.head_parameters()
    restored = WindowTeacher(Candidate('gru', 32), *normalizers())
    restored.load_state_dict(model.state_dict())
    assert any(p.requires_grad for p in restored.encoder.parameters())
    restored.head_parameters()
    assert [p.requires_grad for p in restored.parameters()] == [p.requires_grad for p in model.parameters()]
