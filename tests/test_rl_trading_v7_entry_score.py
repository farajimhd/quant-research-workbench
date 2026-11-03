import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from research.rl_trading.v7.experiments import entry_score as score


def snapshots(value=1.):
    return dict(seconds=np.array([12]), selection=np.zeros((1, 5), np.int32),
                features=np.full((1, 5, 112), value, np.float32),
                candidate_valid=np.ones((1, 5), bool), account=np.zeros((1, 8), np.float32))


def report(day, contract_hash):
    fills = [dict(second=13, ticker='ABC', side=1, quantity=10, price=10., fees=1.),
             dict(second=20, ticker='ABC', side=-1, quantity=10, price=11., fees=1.)]
    records = [dict(row=0, second=12, slot=i, status='closed', terminal_quantity=0,
                    net_dollars=8., fills=copy.deepcopy(fills)) for i in range(5)]
    records[1].update(status='unfilled', net_dollars=0., fills=[])
    records[2].update(status='ineligible_now', net_dollars=None, fills=[])
    return dict(day=day, contract_hash=contract_hash, records=records, parity_checked=3,
                counts=dict(closed=3, unfilled=1, ineligible_now=1, unliquidated=0))


@pytest.fixture
def authority(tmp_path):
    days = [str(d) for d in np.arange(np.datetime64('2026-01-01'), np.datetime64('2026-05-31'))]
    source = SimpleNamespace(days=days, binding={'synthetic': True},
                             items={d: {'selected': ['ABC']} for d in days})
    data = SimpleNamespace(days=days, fit_days=days[:120], development_days=days[120:],
                           source=source, binding={'snapshot': 'synthetic'},
                           load=lambda day: snapshots(days.index(day)+1.))
    root = tmp_path/'labels'
    root.mkdir()
    contract = dict(score.label_contract(data), parity_count=4)
    contract['hash'] = score.digest(contract)
    score.write_json(root/'manifest.json', contract)
    score.write_json(root/'status.json', dict(status='complete', completed=days, validation_access=False))
    for day in days:
        path = root/(day+'.json')
        score.write_json(path, report(day, contract['hash']))
        score.write_json(root/(day+'.complete.json'), dict(sha256=score.sha(path), contract_hash=contract['hash']))
    return data, root


def test_fit_only_scaler_and_fixed_penalty():
    fit = np.array([[1., 4.], [2., 4.], [3., 4.]])
    p = score.fit_ridge(fit, np.array([1., 2., 3.]))
    np.testing.assert_array_equal(p['mean'], [2., 4.])
    assert p['std'][1] == 1
    np.testing.assert_allclose(score.predict(fit, p), [2-1/1.001, 2, 2+1/1.001])
    before = copy.deepcopy(p)
    score.predict(np.array([[1e9, -1e9]]), p)
    for key in p:
        np.testing.assert_array_equal(before[key], p[key])
    fractional = score.fit_ridge(np.full((1000, 1), .15), np.arange(1000.))
    np.testing.assert_array_equal(fractional['std'], [1.])


def test_labels_include_observed_zero_and_reject_future_before_io(authority, monkeypatch):
    data, root = authority
    labels = score.EntryLabels(data, root)
    obs, keep, y, counts = labels.load(data.days[0])
    assert keep.tolist() == [0, 1, 3, 4]
    assert y.tolist() == [8., 0., 8., 8.]
    assert counts['ineligible_now'] == 1
    monkeypatch.setattr(score, 'sha', lambda _: pytest.fail('Out-of-training day opened a file'))
    with pytest.raises(ValueError, match='TRAINING'):
        labels.load('2027-01-01')


@pytest.mark.parametrize('mutation', ['unliquidated', 'cashflow', 'missing'])
def test_targets_fail_closed(mutation):
    r = report('day', 'hash')
    if mutation == 'unliquidated':
        r['records'][0].update(status='unliquidated', net_dollars=None)
    elif mutation == 'cashflow':
        r['records'][0]['net_dollars'] = 100.
    else:
        r['records'].pop()
    with pytest.raises(ValueError):
        score.checked_records(r, snapshots(), ['ABC'])


def test_label_seals_and_live_binding(authority):
    data, root = authority
    labels = score.EntryLabels(data, root)
    path = root/(data.days[0]+'.json')
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError, match='hash'):
        labels.load(data.days[0])
    with pytest.raises(ValueError, match='hash'):
        score.EntryLabels(data, root)


def test_incomplete_labels_rejected_before_snapshot_access(authority):
    data, root = authority
    data.load = lambda _: pytest.fail('Incomplete authority opened snapshots')
    score.write_json(root/'status.json', dict(status='running', completed=data.days[:-1], validation_access=False))
    with pytest.raises(ValueError, match='complete'):
        score.EntryLabels(data, root)


def test_attention_exact_tokens_no_targets():
    torch.set_num_threads(1)
    model = score.PredictivePolicy().eval().requires_grad_(False)
    obs = snapshots()
    obs['y'] = np.full((1, 5, 3), 99999.)
    actual = score.feature_matrix(obs, 'attention', model=model)
    batch = score.observation_batch(obs)
    with torch.no_grad():
        expected = model.encode(**{k: torch.as_tensor(v) for k, v in batch.items()})[:, :5].numpy()
    np.testing.assert_array_equal(actual[..., :64], expected)
    np.testing.assert_array_equal(actual[..., 64:78], obs['features'][..., -14:])
    obs['y'] *= -1
    np.testing.assert_array_equal(actual, score.feature_matrix(obs, 'attention', model=model))


def test_main_both_bundles_reload_and_tamper(authority, tmp_path, monkeypatch):
    data, labels = authority
    torch.set_num_threads(1)
    model = score.PredictivePolicy()
    saved = dict(policy=model.state_dict(), contract_hash='forecast-contract')
    monkeypatch.setattr(score, 'PredictiveDataset', lambda *_: data)
    monkeypatch.setattr(score, 'load_forecast', lambda *_: (saved, 'forecast-sha'))
    output = tmp_path/'fit'
    assert score.main(['--output', str(output), '--labels', str(labels), '--device', 'cpu']) == 0
    for kind in score.KINDS:
        bundle = score.load_bundle(output/kind, data.source, 'unused', device='cpu')
        assert bundle.row_step == 10
        json.dumps(bundle.binding)
        obs = snapshots(121.)
        obs['candidate_valid'][0, 4] = False
        predicted = bundle.score(obs)
        assert predicted.shape == (1, 5) and np.isneginf(predicted[0, 4])
        expected = score.predict(score.feature_matrix(obs, kind, model=bundle.model), bundle.parameters)
        np.testing.assert_array_equal(predicted[:, :4], expected[:, :4])
        again = score.load_bundle(output/kind, data.source, 'unused')
        np.testing.assert_array_equal(predicted, again.score(obs))
        assert bundle.parameters['mean'][0] < 121 if kind == 'raw' else True
    weights = output/'raw'/'weights.npz'
    with weights.open('ab') as stream:
        stream.write(b'tamper')
    with pytest.raises(ValueError, match='hash'):
        score.load_bundle(output/'raw', data.source, 'unused')
