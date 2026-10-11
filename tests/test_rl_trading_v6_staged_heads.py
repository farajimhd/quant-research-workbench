import pytest
import torch
from torch import nn
from research.rl_trading.v6.hierarchical_heads import HierarchicalTickerHeads, HierarchicalForecast
from research.rl_trading.v6.staged_heads import configure, objective, capture, targets_from_packet, VERSION


def fixture():
    torch.manual_seed(17)
    policy = nn.Module()
    policy.encoder = nn.Linear(8, 8)
    policy.decoder = nn.Module()
    policy.decoder.heads = HierarchicalTickerHeads(8)
    policy.decoder.size_head = nn.Linear(8, 1)
    policy.teacher_forecast = HierarchicalForecast(8, HierarchicalTickerHeads(8))
    policy.action_gru = nn.GRUCell(19, 8)
    x = torch.randn(4, 8)
    actions = torch.arange(4)
    quality = torch.tensor([.8, .2, .3, .9])
    allocation = torch.tensor([.4, float('nan'), float('nan'), float('nan')])
    hard = torch.tensor([[0, 2], [1, 0], [2, 3], [3, 1]])
    forecasts = [(torch.arange(4), hard, torch.nn.functional.one_hot(hard, 4).float())]
    return policy, (x, actions, quality, allocation, forecasts, torch.ones(4))


def test_all_supervised_heads_receive_gradients_without_encoder_updates():
    policy, packet = fixture()
    parameters = configure(policy)
    assert len(parameters) == len({id(p) for p in parameters})
    loss = objective(policy, *packet)
    loss.backward()
    assert all(p.grad is None for p in policy.encoder.parameters())
    for module in (policy.decoder.heads.entry, policy.decoder.heads.exit,
                   policy.decoder.heads.quality, policy.decoder.size_head,
                   policy.teacher_forecast.reference_held, policy.action_gru):
        assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in module.parameters())
    optimizer = torch.optim.Adam(parameters, lr=.01)
    before = {k: v.clone() for k, v in policy.encoder.state_dict().items()}
    optimizer.step()
    assert all(torch.equal(before[k], v) for k, v in policy.encoder.state_dict().items())


@pytest.mark.parametrize('case', ['duplicate', 'missing', 'context_grad', 'invalid_ratio', 'invalid_future'])
def test_fail_closed_targets(case):
    policy, packet = fixture()
    x, actions, quality, ratio, forecasts, weights = packet
    if case == 'duplicate': forecasts[0][0][0] = 1
    if case == 'missing': forecasts = []
    if case == 'context_grad': x.requires_grad_()
    if case == 'invalid_ratio': ratio[1] = .5
    if case == 'invalid_future': forecasts[0][2][0, 0, :] = 0
    with pytest.raises(ValueError): objective(policy, x, actions, quality, ratio, forecasts, weights)


def test_capture_rejects_non_train_before_reading_targets():
    from types import SimpleNamespace
    with pytest.raises(ValueError):
        capture(None, SimpleNamespace(role='development'), [object()], 'cpu')


def test_capture_removes_hook_after_evaluator_failure(monkeypatch):
    from types import SimpleNamespace
    from research.rl_trading.v6 import run_ranked_teacher_generalization as evaluator
    policy, _ = fixture()
    def fail(*args, **kwargs):
        raise RuntimeError('causal source failure')
    monkeypatch.setattr(evaluator, 'evaluate_probabilities', fail)
    with pytest.raises(RuntimeError, match='causal source failure'):
        capture(policy, SimpleNamespace(role='train'), [object()], 'cpu')
    assert len(policy.decoder.heads._forward_pre_hooks) == 0


def test_packet_identity_is_required_not_just_matching_counts():
    from types import SimpleNamespace
    from datetime import date
    import numpy as np
    session = SimpleNamespace(role='train', day=date(2026, 7, 31), listings=['A'])
    target = SimpleNamespace(token=1, held_index=[], soft_tokens=(0, 1),
        soft_probabilities=(.2, .8), close_us=10, episode_uid='episode-A',
        sample_weight=.5, allocation_ratio_target=.4,
        forecast_actions=np.array([2], dtype=np.int64),
        forecast_probabilities=np.array([[0., 0., 1., 0.]]))
    packet = dict(role='train', version=VERSION, day='2026-07-31',
        context=torch.zeros(1, 8), action=[0], close_us=[10],
        episode_uid=['episode-A'], listing_index=[0], weights=[.5])
    tensors = targets_from_packet(session, [target], packet)
    assert tensors[1].tolist() == [0]
    packet['episode_uid'] = ['episode-B']
    with pytest.raises(ValueError, match='identity'):
        targets_from_packet(session, [target], packet)


def test_fit_resume_reproduces_optimizer_and_rejects_binding_drift(tmp_path):
    from research.rl_trading.v6.run_staged_underfit import restore_fit
    from research.rl_trading.v1.common import file_hash
    policy, packet = fixture(); parameters = configure(policy)
    optimizer = torch.optim.AdamW(parameters, lr=.003)
    objective(policy, *packet).backward(); optimizer.step(); optimizer.zero_grad()
    checkpoint = tmp_path / 'fit.pt'
    torch.save(dict(model=policy.state_dict(), optimizer=optimizer.state_dict(),
        torch_rng=torch.get_rng_state(), binding='verified-packet', step=1), checkpoint)
    objective(policy, *packet).backward(); optimizer.step()
    expected = {k: v.clone() for k, v in policy.state_dict().items()}
    restored, packet2 = fixture(); optimizer2 = torch.optim.AdamW(configure(restored), lr=.003)
    assert restore_fit(checkpoint, file_hash(checkpoint), 'verified-packet', restored, optimizer2, 2) == 1
    objective(restored, *packet2).backward(); optimizer2.step()
    assert all(torch.equal(v, restored.state_dict()[k]) for k, v in expected.items())
    with pytest.raises(ValueError, match='binding'):
        restore_fit(checkpoint, file_hash(checkpoint), 'different-packet', restored, optimizer2, 2)
    with pytest.raises(ValueError, match='hash'):
        restore_fit(checkpoint, '0'*64, 'verified-packet', restored, optimizer2, 2)


def test_launcher_rejects_changed_receipts(tmp_path):
    import json
    from research.rl_trading.v1.common import digest
    from research.rl_trading.v6.run_staged_underfit import read_authenticated
    payload = dict(role='train', rows=128)
    payload['hash'] = digest(payload)
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(payload))
    assert read_authenticated(path)['rows'] == 128
    payload['rows'] = 129
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='hash changed'):
        read_authenticated(path)
