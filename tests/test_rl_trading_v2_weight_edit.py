"""Training-only weight edits preserve tensors and stochastic execution semantics."""
from dataclasses import replace
from datetime import date
import json

import numpy as np
import pytest
import torch

from research.rl_trading.v1.common import bounds, digest
from research.rl_trading.v2 import weight_edit
from research.rl_trading.v2.config import Config
from research.rl_trading.v2.data import DATA_VERSION, MarketSession
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.model import PortfolioPolicy, collate


def synthetic_session(seconds=24):
    n, day = 3, '2026-08-20'
    price = np.broadcast_to(5. + np.arange(seconds)[None, :] * .001,
                            (n, seconds)).copy()
    arrays = dict(features=np.zeros((n, seconds, 3), dtype=np.float32),
        prices=price, execution_open=price.copy(),
        volume=np.full((n, seconds), 100000.),
        volume_60s=np.broadcast_to(np.arange(n, 0, -1)[:, None] * 100000.,
                                  (n, seconds)).copy(),
        trades_60s=np.full((n, seconds), 100.), fresh=np.ones((n, seconds), dtype=bool),
        estimated_reference=np.full((n, seconds), 5., dtype=np.float32),
        prior_close=np.full(n, 5., dtype=np.float32))
    arrays['features'][:, :, 0] = np.log(price)
    plan = dict(version=DATA_VERSION, date=day, clock='completed_second', step_us=1000000,
        first_us=bounds(date.fromisoformat(day))[0], rows=seconds, segment=True,
        feature_names=['log_price', 'x', 'y'], teacher_dependency=False,
        listings=[dict(ticker=f'T{i:03d}', listing_id=f'L{i:03d}') for i in range(n)])
    plan['plan_hash'] = digest(plan)
    return MarketSession(plan, arrays)


def test_edit_changes_only_requested_bias_entries_and_does_not_alias_source():
    state = PortfolioPolicy(3, width=8, heads=2).state_dict()
    original = {key: value.clone() for key, value in state.items()}
    edited = weight_edit.edited_state(state, close_bias=2., reduce_bias=-1., gate_bias=.5)
    expected = {key: value.clone() for key, value in original.items()}
    expected['action_type.bias'][2] += 2.
    expected['action_type.bias'][1] -= 1.
    expected['trade_gate.bias'][0] += .5
    assert edited.keys() == original.keys()
    for key in original:
        assert torch.equal(edited[key], expected[key]), key
        assert torch.equal(state[key], original[key]), key
        assert edited[key].data_ptr() != state[key].data_ptr(), key
    assert sum(int((edited[key] != original[key]).sum()) for key in original) == 3


@pytest.mark.parametrize('field', ['close_bias', 'reduce_bias', 'gate_bias'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), 8.01, -8.01])
def test_edit_rejects_invalid_offsets(field, value):
    with pytest.raises(ValueError, match='finite'):
        weight_edit.edited_state({}, **{field: value})


@pytest.mark.parametrize('means', [(0.1, 0.5, 0.9), (0.0021, 0.9979, 0.3)])
def test_fixed_sizing_means_are_legal_and_independent_of_context(means):
    policy = PortfolioPolicy(3, width=8, heads=2)
    original = {key: value.clone() for key, value in policy.state_dict().items()}
    edited = weight_edit.edited_state(original, close_bias=1.5, gate_bias=-.25,
                                      size_means=means)
    permitted = {'size.weight', 'size.bias', 'action_type.bias', 'trade_gate.bias'}
    for key in original:
        if key not in permitted:
            assert torch.equal(edited[key], original[key]), key
        assert torch.equal(policy.state_dict()[key], original[key]), key
    expected_type = original['action_type.bias'].clone()
    expected_type[2] += 1.5
    assert torch.equal(edited['action_type.bias'], expected_type)
    assert torch.equal(edited['trade_gate.bias'], original['trade_gate.bias'] - .25)
    assert torch.count_nonzero(edited['size.weight']) == 0
    policy.load_state_dict(edited)
    # Exercise the model's actual sizing head over dissimilar arbitrary contexts.
    contexts = torch.cat((torch.zeros(1, 8), torch.randn(7, 8) * 1000.), dim=0)
    parameters = torch.nn.functional.softplus(policy.size(contexts)).reshape(8, 3, 2) + 1.01
    distribution = torch.distributions.Beta(parameters[..., 0], parameters[..., 1])
    expected_means = torch.tensor(means).expand(8, -1)
    torch.testing.assert_close(distribution.mean, expected_means, rtol=1e-6, atol=1e-7)
    torch.testing.assert_close(parameters.sum(dim=-1), torch.full((8, 3), 1000.))
    assert torch.all(parameters > 1.01)
    draws = distribution.sample((32,))
    assert torch.all((draws > 0) & (draws < 1))


@pytest.mark.parametrize('means', [[], [.5, .5], [.5] * 4, [[.5, .5, .5]],
    [.002, .5, .5], [.5, .998, .5], [0., .5, .5], [1., .5, .5],
    [float('nan'), .5, .5], [.5, float('inf'), .5], [.5, .5, -float('inf')]])
def test_fixed_sizing_rejects_invalid_means(means):
    state = PortfolioPolicy(3, width=8, heads=2).state_dict()
    with pytest.raises(ValueError, match='three interior'):
        weight_edit.edited_state(state, size_means=means)


@pytest.mark.parametrize('seed', [17, 1917])
def test_replay_matches_policy_action_and_environment(seed, tmp_path, monkeypatch):
    session = synthetic_session()
    config = replace(Config(), entry_rank=2, hold_rank=3, history_seconds=4,
        liquidation_buffer_seconds=2, min_volume_60s=0, min_trades_60s=0,
        commission_model='research', fee_ratio=.0001, base_slippage_ratio=.0001,
        impact_ratio=0., volatility_slippage_ratio=0., max_volume_participation=1.)
    torch.manual_seed(29)
    policy = PortfolioPolicy(3, width=8, heads=2).eval()
    with torch.no_grad():
        policy.trade_gate.weight.zero_()
        policy.trade_gate.bias.fill_(2.)
    expected = TradingEnv(session, config)
    expected_actions, expected_fills = [], []
    torch.manual_seed(seed)
    np.random.seed(seed)
    with torch.inference_mode():
        while not expected.done:
            modes, sizes, *_ = policy.action(collate([expected.observe()]))
            mode, size = modes[0].numpy().copy(), sizes[0].numpy().copy()
            expected_actions.append((mode, size))
            expected.step(mode, size)
            expected_fills.extend(expected.last_fills)
    actual_actions = []

    class RecordingEnv(TradingEnv):
        def step(self, modes, sizes):
            actual_actions.append((modes.copy(), sizes.copy()))
            return super().step(modes, sizes)

    monkeypatch.setattr(weight_edit, 'TradingEnv', RecordingEnv)
    result = weight_edit.replay(policy, session, config, 'cpu', seed, tmp_path)
    assert result['full_session']
    assert result['summary'] == expected.summary()
    assert result['summary']['filled_orders'] > 0
    assert len(actual_actions) == len(expected_actions)
    for actual, wanted in zip(actual_actions, expected_actions):
        np.testing.assert_array_equal(actual[0], wanted[0])
        np.testing.assert_array_equal(actual[1], wanted[1])
    actual_fills = [json.loads(line) for line in (tmp_path / 'fills.jsonl').read_text().splitlines()]
    assert actual_fills == expected_fills
    assert all(not module._forward_hooks for module in policy.modules())


@pytest.mark.parametrize('requested', [['2026-09-01'], ['2026-10-01'],
    ['2026-08-20', '2026-09-01'], ['2026-08-20', '2026-08-20']])
def test_runner_rejects_nontraining_and_duplicate_dates_before_opening_banks(requested, monkeypatch):
    manifest = dict(train=[dict(date='2026-08-20', root='unused-training-bank')],
                    validation=[dict(date='2026-09-01', root='never-open-validation')])
    reads = []

    def read_manifest(path):
        reads.append(path.name)
        assert path.name == 'run_manifest.json'
        return manifest

    def forbidden(*args, **kwargs):
        pytest.fail('Rejected dates must not open banks, checkpoints, or output roots')

    monkeypatch.setattr(weight_edit, 'read', read_manifest)
    monkeypatch.setattr(weight_edit.MarketSession, 'load', forbidden)
    monkeypatch.setattr(weight_edit.torch, 'load', forbidden)
    monkeypatch.setattr(weight_edit, 'output_root', forbidden)
    monkeypatch.setattr(weight_edit.torch, 'set_num_threads', lambda *args: None)
    monkeypatch.setattr(weight_edit.torch, 'use_deterministic_algorithms', lambda *args: None)
    with pytest.raises(ValueError, match='TRAINING'):
        weight_edit.main(['--parent', 'unused-parent', '--name', 'rejected',
                          '--device', 'cpu', '--dates', *requested])
    assert reads == ['run_manifest.json']


def test_interrupted_replay_resumes_exactly_and_truncates_uncommitted_fills(tmp_path, monkeypatch):
    session = synthetic_session(seconds=640)
    config = replace(Config(), entry_rank=2, hold_rank=3, history_seconds=4,
        liquidation_buffer_seconds=2, min_volume_60s=0, min_trades_60s=0,
        commission_model='research', fee_ratio=.0001, base_slippage_ratio=.0001,
        impact_ratio=0., volatility_slippage_ratio=0., max_volume_participation=1.)
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        torch.manual_seed(29)
        policy = PortfolioPolicy(3, width=8, heads=2).eval()
        with torch.no_grad():
            policy.trade_gate.weight.zero_()
            policy.trade_gate.bias.fill_(2.)
        uninterrupted = tmp_path / 'uninterrupted'
        interrupted = tmp_path / 'interrupted'
        uninterrupted.mkdir()
        interrupted.mkdir()
        expected = weight_edit.replay(policy, session, config, 'cpu', 1917, uninterrupted)

        class InterruptingEnv(TradingEnv):
            def step(self, modes, sizes):
                if self.t == 615:
                    raise InterruptedError('Synthetic interruption after committed checkpoint')
                return super().step(modes, sizes)

        monkeypatch.setattr(weight_edit, 'TradingEnv', InterruptingEnv)
        with pytest.raises(InterruptedError, match='Synthetic interruption'):
            weight_edit.replay(policy, session, config, 'cpu', 1917, interrupted)
        saved = torch.load(interrupted / 'replay_state.pt', weights_only=False)
        assert saved['environment']['t'] == 600
        fill_path = interrupted / 'fills.jsonl'
        before_resume = [json.loads(line) for line in fill_path.read_text().splitlines()]
        assert any(fill['fill_second'] > 600 for fill in before_resume)
        assert fill_path.stat().st_size > saved['fills_offset']
        assert not (interrupted / 'result.json').exists()
        assert all(not module._forward_hooks for module in policy.modules())
        monkeypatch.setattr(weight_edit, 'TradingEnv', TradingEnv)
        resumed = weight_edit.replay(policy, session, config, 'cpu', 1917, interrupted, resume=True)
        assert resumed['full_session']
        assert resumed['summary'] == expected['summary']
        assert resumed['samples'] == expected['samples']
        assert fill_path.read_bytes() == (uninterrupted / 'fills.jsonl').read_bytes()
        assert all(not module._forward_hooks for module in policy.modules())
    finally:
        torch.set_num_threads(previous_threads)
