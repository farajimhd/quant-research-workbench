import importlib.util
import json
from pathlib import Path
import numpy as np
import pytest
import torch
from research.rl_trading.v7.experiments import entry_net as net

spec = importlib.util.spec_from_file_location('entry_score_fixture', Path(__file__).with_name('test_rl_trading_v7_entry_score.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
authority = fixtures.authority


def model_and_obs():
    torch.set_num_threads(1)
    torch.manual_seed(1917)
    forecast = net.shared.PredictivePolicy().state_dict()
    model = net.EntryNet(forecast, np.zeros(22), np.ones(22))
    obs = net.shared.observation_batch(fixtures.snapshots(.1))
    obs['account'][:, 0] = 1
    return model, obs


def test_initial_baseline_and_target_isolation():
    model, obs = model_and_obs()
    values = model(**net.tensors(obs, [0], 'cpu')).detach().numpy()
    np.testing.assert_allclose(values, net.baseline(obs['features'], obs['account']), rtol=1e-6)
    extra = dict(obs, y=np.full((1, 5), 1e9))
    scorer = net.Scorer(model.eval(), {}, 'cpu')
    np.testing.assert_array_equal(scorer.score(obs), scorer.score(extra))


def test_nonzero_head_then_encoder_gradient():
    model, obs = model_and_obs()
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
    for step in range(2):
        optimizer.zero_grad()
        loss = ((model(**net.tensors(obs, [0], 'cpu'))-20)/10).square().mean()
        loss.backward()
        assert model.head[-1].weight.grad.abs().sum() > 0
        if step:
            assert model.encoder.frame[0].weight.grad.abs().sum() > 0
        optimizer.step()


def test_future_rejected_before_read(authority, monkeypatch):
    data, root = authority
    labels = net.shared.EntryLabels(data, root)
    monkeypatch.setattr(net.shared, 'sha', lambda _: pytest.fail('Read before training membership guard'))
    with pytest.raises(ValueError, match='TRAINING'):
        labels.load('2030-01-01')


def test_actual_training_resume_reload_and_integrity(authority, tmp_path, monkeypatch):
    data, labels = authority
    torch.set_num_threads(1)
    torch.manual_seed(22)
    saved = dict(policy=net.shared.PredictivePolicy().state_dict(), contract_hash='forecast')
    monkeypatch.setattr(net.shared, 'PredictiveDataset', lambda *_: data)
    monkeypatch.setattr(net.shared, 'load_forecast', lambda *_: (saved, 'sha'))
    common = ['--labels', str(labels), '--device', 'cpu', '--epochs', '2']
    uninterrupted = tmp_path/'uninterrupted'
    assert net.main(common+['--output', str(uninterrupted)]) == 0
    interrupted = tmp_path/'interrupted'
    save = net.save_epoch
    def stop_after_one(root, state):
        save(root, state)
        if state['epoch'] == 1:
            (root/'STOP').touch()
    monkeypatch.setattr(net, 'save_epoch', stop_after_one)
    assert net.main(common+['--output', str(interrupted)]) == 2
    (interrupted/'STOP').unlink()
    monkeypatch.setattr(net, 'save_epoch', save)
    # Simulate stale publication and an unsealed crash artifact.
    (interrupted/'latest.json').write_text('{}')
    (interrupted/'epoch-002.pt').write_bytes(b'uncommitted')
    assert net.main(common+['--output', str(interrupted), '--resume']) == 0
    assert list((interrupted/'uncommitted').iterdir())
    a = torch.load(uninterrupted/'epoch-002.pt', weights_only=False)
    b = torch.load(interrupted/'epoch-002.pt', weights_only=False)
    for key in a['model']:
        torch.testing.assert_close(a['model'][key], b['model'][key], rtol=0, atol=0)
    assert a['metrics'] == b['metrics']
    scorer = net.load_bundle(interrupted, data.source, 'unused')
    assert scorer.row_step == 10
    json.dumps(scorer.binding)
    obs = fixtures.snapshots()
    obs['candidate_valid'][0, 4] = False
    scores = scorer.score(obs)
    assert scores.shape == (1, 5) and np.isneginf(scores[0, 4])
    np.testing.assert_array_equal(scores, net.load_bundle(interrupted, data.source, 'unused').score(obs))
    pointer = json.loads((interrupted/'best.json').read_text())
    with (interrupted/f"epoch-{pointer['epoch']:03d}.pt").open('ab') as stream:
        stream.write(b'tamper')
    with pytest.raises(ValueError, match='integrity'):
        net.load_bundle(interrupted, data.source, 'unused')
