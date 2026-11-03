"""Cached forecast/action parity and committed-batch PPO restart contracts."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import torch

from research.rl_trading.v7.experiments import batched_ppo as driver
from research.rl_trading.v7.experiments.predictive_model import PredictivePolicy
from research.rl_trading.v7.model import tensor_batch
from research.rl_trading.v7.experiments.cost_aware import CostEnvironment
from research.rl_trading.v7.config import Config


@pytest.fixture(autouse=True)
def single_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def tiny_session(day):
    n, seconds, decisions = 2, 1000, 16
    mark = np.full((seconds, n), 5.)
    clocks = np.arange(1800000000, 1800000000+seconds, dtype=np.int64)
    execution = dict(mark=mark, bid=mark-.01, ask=mark+.01,
        bid_size=np.full(mark.shape, 1000.), ask_size=np.full(mark.shape, 1000.),
        quote_us=np.broadcast_to(clocks[:, None]*1000000-1, mark.shape).copy(),
        quote_valid=np.ones(mark.shape, bool))
    selection = np.full((decisions, 5), -1, np.int32)
    selection[:, :2] = [0, 1]
    features = np.zeros((decisions, 5, 98), np.float32)
    features[:, :2, 7:14] = 1.
    return SimpleNamespace(day=day, tickers=['A', 'B'], clocks=clocks,
        selection=selection, features=features, execution=execution)


def initial_policy():
    torch.manual_seed(9)
    policy = PredictivePolicy()
    with torch.no_grad():
        policy.control.bias.zero_()
        policy.buy.bias.fill_(12.)
    return policy


def test_cached_logits_value_and_sampling_exactly_match_original():
    policy = initial_policy().eval()
    observations = [CostEnvironment(tiny_session(day)).observe() for day in ('2026-01-05', '2026-01-06')]
    batch = tensor_batch(observations)
    with torch.no_grad():
        original, original_value = policy(**batch)
        tokens, offset = driver.cached_inputs(policy, batch)
        actual, value = driver.head_forward(policy, tokens, offset, batch['action_mask'])
        torch.testing.assert_close(actual.logits, original.logits, rtol=0, atol=0)
        torch.testing.assert_close(value, original_value, rtol=0, atol=0)
        torch.manual_seed(81)
        expected = original.sample()
        torch.manual_seed(81)
        assert torch.equal(actual.sample(), expected)


def test_complete_account_updates_change_only_heads():
    policy = initial_policy()
    optimizer = torch.optim.Adam(driver.freeze(policy), lr=1e-4)
    before = {k: v.clone() for k, v in policy.state_dict().items()}
    rows, results = driver.collect(policy, [tiny_session('2026-01-05')], Config(), 'cpu', lambda *a: None)
    assert results[0]['valid_terminal'] and results[0]['fills'] > 0
    assert set(rows[0]) == {'tokens', 'offset', 'mask', 'action', 'logprob', 'value', 'reward', 'done', 'count'}
    update = driver.update(policy, optimizer, rows, np.random.default_rng(31), 'cpu', 256, 3, lambda *a: None)
    assert update['updates'] > 0
    changed = set()
    for name, value in policy.state_dict().items():
        if not torch.equal(value, before[name]):
            changed.add(name.split('.')[0])
        if name.split('.')[0] not in driver.HEADS:
            assert torch.equal(value, before[name]), name
    assert changed and changed <= set(driver.HEADS)


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    class Dataset:
        opens = []
        days = ['2026-01-05', '2026-01-06']
        binding = {'synthetic_training': True}
        items = {day: {'selected': ['A', 'B']} for day in days}

        def __init__(self, root):
            pass

        def load(self, day):
            assert day in self.days
            self.opens.append(day)
            return tiny_session(day)

    saved = dict(policy=initial_policy().state_dict(), contract_hash='synthetic_forecast')
    monkeypatch.setattr(driver, 'TrainingDataset', Dataset)
    monkeypatch.setattr(driver, 'load_forecast', lambda *a: (saved, 'synthetic_forecast_hash'))
    return Dataset, ['--dataset', 'unused', '--forecast', 'unused.pt', '--device', 'cpu',
                     '--environments', '1', '--prepare-workers', '1', '--seed', '17']


def tree_equal(actual, expected):
    if isinstance(expected, torch.Tensor):
        assert torch.equal(actual, expected)
    elif isinstance(expected, np.ndarray):
        np.testing.assert_array_equal(actual, expected)
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            tree_equal(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected):
            tree_equal(a, b)
    else:
        assert actual == expected


def test_actual_main_resume_from_batch_boundary_and_repair_publications(synthetic, monkeypatch, tmp_path):
    dataset, arguments = synthetic
    whole, interrupted = tmp_path/'whole', tmp_path/'interrupted'
    assert driver.main([*arguments, '--output', str(whole)]) == 0
    expected = torch.load(whole/'checkpoint_latest.pt', weights_only=False)
    original_publish = driver.publish

    def stop_after_first(root, state):
        original_publish(root, state)
        if root == interrupted and state['index'] == 1:
            (root/'STOP').write_text('test stop')

    monkeypatch.setattr(driver, 'publish', stop_after_first)
    assert driver.main([*arguments, '--output', str(interrupted)]) == 2
    assert torch.load(interrupted/'checkpoint_latest.pt', weights_only=False)['index'] == 1
    (interrupted/'STOP').rename(interrupted/'STOP.handled')
    monkeypatch.setattr(driver, 'publish', original_publish)
    driver.write_json(interrupted/'batches'/'000002.json', {'uncommitted': True})
    before = len(dataset.opens)
    with pytest.raises(ValueError, match='Exact run contract'):
        driver.main([*arguments, '--output', str(interrupted), '--resume', '--learning-rate', '0.0002'])
    assert len(dataset.opens) == before
    assert driver.main([*arguments, '--output', str(interrupted), '--resume']) == 0
    actual = torch.load(interrupted/'checkpoint_latest.pt', weights_only=False)
    for key in ('policy', 'optimizer', 'index', 'results', 'torch_rng', 'numpy_rng', 'python_rng', 'shuffle_rng'):
        tree_equal(actual[key], expected[key])
    assert actual['metrics'][1]['optimization'] == expected['metrics'][1]['optimization']
    assert len(list((interrupted/'uncommitted').glob('*/000002.json'))) == 1
    (interrupted/'batches'/'000002.json').rename(interrupted/'metric.saved.json')
    (interrupted/'results.json').rename(interrupted/'results.saved.json')
    before = len(dataset.opens)
    assert driver.main([*arguments, '--output', str(interrupted), '--resume']) == 0
    assert len(dataset.opens) == before
    assert json.loads((interrupted/'results.json').read_text()) == actual['results']
    assert (interrupted/'batches'/'000002.json').exists()
    # Frozen replay may change concurrency/decision rule, but never update weights.
    frozen = tmp_path/'frozen'
    assert driver.main([*arguments, '--output', str(frozen), '--evaluate', str(whole/'checkpoint_latest.pt'),
                        '--environments', '2', '--greedy']) == 0
    replayed = torch.load(frozen/'checkpoint_latest.pt', weights_only=False)
    tree_equal(replayed['policy'], expected['policy'])
    assert replayed['contract']['decision_rule'] == 'greedy'


def test_validation_day_rejected_before_market_or_forecast_read(synthetic, monkeypatch, tmp_path):
    dataset, arguments = synthetic
    monkeypatch.setattr(driver, 'load_forecast', lambda *a: pytest.fail('Forecast must not open for invalid dates'))
    with pytest.raises(ValueError, match='TRAINING'):
        driver.main([*arguments, '--output', str(tmp_path/'invalid'), '--dates', '2026-12-01'])
    assert not dataset.opens


def test_insufficient_memory_fails_before_market_loading(synthetic, tmp_path):
    dataset, arguments = synthetic
    with pytest.raises(MemoryError, match='budget insufficient'):
        driver.main([*arguments, '--output', str(tmp_path/'insufficient'), '--memory-gib', '.1'])
    assert not dataset.opens


def test_actual_available_ram_is_checked_before_preparation(synthetic, monkeypatch, tmp_path):
    dataset, arguments = synthetic
    monkeypatch.setattr(driver.psutil, 'virtual_memory', lambda: SimpleNamespace(available=1))
    root = tmp_path/'unavailable-ram'
    with pytest.raises(MemoryError, match='available RAM'):
        driver.main([*arguments, '--output', str(root)])
    assert not dataset.opens
    assert json.loads((root/'status.json').read_text())['status'] == 'failed'
    assert torch.load(root/'checkpoint_latest.pt', weights_only=False)['index'] == 0
