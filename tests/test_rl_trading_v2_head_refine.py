"""Frozen-context PPO preserves the original policy distribution and encoder."""
import numpy as np
import pytest
import torch
from dataclasses import replace
from datetime import date
from torch.distributions import Beta, Bernoulli, Categorical

from research.rl_trading.v2 import head_refine
from research.rl_trading.v1.common import bounds, digest
from research.rl_trading.v2.config import Config
from research.rl_trading.v2.data import MarketSession, DATA_VERSION
from research.rl_trading.v2.io import read, write, file_hash
from research.rl_trading.v2.model import PortfolioPolicy, collate


@pytest.fixture(autouse=True)
def bounded_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def observation(n=3):
    rng = np.random.default_rng(n)
    position = rng.normal(size=(n, 12)).astype(np.float32)
    position[:, 0] = np.arange(n) % 2
    return dict(ids=np.arange(n), market=rng.normal(size=(n, 4, 3)).astype(np.float32),
        position=position, valid=np.ones(n, dtype=bool), action_mask=np.ones((n, 4), dtype=bool),
        account=rng.normal(size=6).astype(np.float32))


def legacy_forward(policy, batch):
    """Independent pre-extraction forward, so delegation alone cannot pass parity."""
    x = batch['market']
    b, n, h, f = x.shape
    temporal = policy.temporal(x.reshape(b*n, h, f).transpose(1, 2))
    encoded = policy.history(torch.cat((temporal[:, :, -1], temporal.mean(dim=2)), dim=-1)).reshape(b, n, -1)
    encoded = encoded + policy.position(batch['position'])
    account = policy.account(batch['account']).unsqueeze(1)
    mask = torch.cat((torch.zeros((b, 1), dtype=torch.bool), ~batch['valid']), dim=1)
    context = policy.market(torch.cat((account, encoded), dim=1), src_key_padding_mask=mask)
    logits = policy.actor(context[:, 1:]).masked_fill(~batch['action_mask'], -1e9)
    parameters = torch.nn.functional.softplus(policy.size(context[:, 1:])).reshape(b, n, 3, 2) + 1.01
    value = policy.critic(context[:, 0]).squeeze(-1)
    eligible = batch['action_mask'][..., 1:].any(dim=(1, 2))
    gate = policy.trade_gate(context[:, 0]).squeeze(-1).masked_fill(~eligible, -1e9)
    type_mask = batch['action_mask'][..., 1:].any(dim=1)
    listing_logits = logits[..., 1:].transpose(1, 2)
    type_logits = (torch.logsumexp(listing_logits, dim=-1) + policy.action_type(context[:, 0])).masked_fill(~type_mask, -1e9)
    type_probability = torch.softmax(type_logits, dim=-1).masked_fill(~type_mask, 0.)
    listing_mask = batch['action_mask'][..., 1:].transpose(1, 2)
    listing_probability = torch.softmax(listing_logits, dim=-1).masked_fill(~listing_mask, 0.)
    joint = (type_probability.unsqueeze(-1) * listing_probability).transpose(1, 2).reshape(b, -1)
    fallback = torch.nn.functional.one_hot(torch.zeros(b, dtype=torch.long), num_classes=n*3).to(joint.dtype)
    joint = torch.where(eligible.unsqueeze(-1), joint, fallback)
    return Categorical(logits=logits), Beta(parameters[..., 0], parameters[..., 1]), value, Bernoulli(logits=gate), Categorical(probs=joint)


def test_preextraction_forward_and_cached_heads_are_exactly_equal():
    torch.manual_seed(51)
    policy = PortfolioPolicy(3, width=8, heads=2).eval()
    empty = observation(1)
    empty['valid'][:] = False
    empty['action_mask'][:, 1:] = False
    batch = collate([observation(3), observation(2), empty])
    with torch.no_grad():
        original = legacy_forward(policy, batch)
        actual = policy(batch, scheduler=True)
        cached = policy.heads(policy.encode(batch), batch, scheduler=True)
        for result in (actual, cached):
            for index in (0, 3, 4):
                assert torch.equal(result[index].probs, original[index].probs)
            assert torch.equal(result[1].concentration1, original[1].concentration1)
            assert torch.equal(result[1].concentration0, original[1].concentration0)
            assert torch.equal(result[2], original[2])
        torch.manual_seed(82)
        ordinary = policy.action(batch)
        torch.manual_seed(82)
        encoded = policy.action_from_context(policy.encode(batch), batch)
        assert all(torch.equal(a, b) for a, b in zip(ordinary, encoded))


def test_cached_ppo_updates_heads_and_preserves_every_encoder_tensor():
    torch.manual_seed(26)
    policy = PortfolioPolicy(3, width=8, heads=2)
    with torch.no_grad():
        policy.trade_gate.bias.fill_(2.)
    trainable = head_refine.freeze_encoder(policy)
    optimizer = torch.optim.Adam(trainable, lr=1e-3)
    before = {k: v.clone() for k, v in policy.state_dict().items()}
    rows = []
    for index in range(32):
        row = head_refine.cached_action(policy, observation(2 + index % 2), 'cpu')
        row.update(reward=float(np.sin(index) * .03), done=index == 31)
        assert 'market' not in row and 'account' not in row
        assert row['context'].device.type == 'cpu' and not row['context'].requires_grad
        rows.append(row)
    # Preserve actual sampled action/logprob identity through variable-length padding.
    context, batch, modes, sizes = head_refine.collate_cached(rows, 'cpu')
    with torch.no_grad():
        logprob = policy.action_from_context(context, batch, modes, sizes)[2]
    torch.testing.assert_close(logprob, torch.tensor([r['logprob'] for r in rows]), rtol=1e-5, atol=1e-6)
    result = head_refine.update_heads(policy, optimizer, rows, 0., np.random.default_rng(42),
                                      'cpu', epochs=2, batch_size=8)
    assert result['updates'] > 0
    after = policy.state_dict()
    changed = set()
    for name, value in after.items():
        group = name.split('.')[0]
        if group not in head_refine.HEADS:
            assert torch.equal(value, before[name]), name
            assert dict(policy.named_parameters())[name].grad is None
        elif not torch.equal(value, before[name]):
            changed.add(group)
    assert changed == set(head_refine.HEADS)


@pytest.mark.parametrize('dates', [['2026-09-01'], ['2026-08-20', '2026-09-01'],
                                  ['2026-08-20', '2026-08-20']])
def test_runner_rejects_nontraining_dates_before_checkpoint_or_bank_access(dates, monkeypatch):
    manifest = dict(train=[dict(date='2026-08-20', root='unused')])
    monkeypatch.setattr(head_refine, 'read', lambda path: manifest)

    def forbidden(*args, **kwargs):
        pytest.fail('No checkpoint, bank or output access allowed for invalid dates')

    monkeypatch.setattr(head_refine, 'initial_policy', forbidden)
    monkeypatch.setattr(head_refine.MarketSession, 'load', forbidden)
    monkeypatch.setattr(head_refine, 'output_root', forbidden)
    with pytest.raises(ValueError, match='TRAINING'):
        head_refine.main(['--parent', 'unused', '--name', 'rejected', '--dates', *dates])


def test_runner_commits_continuous_synthetic_account_across_rollouts(tmp_path, monkeypatch):
    n, seconds, day = 3, 16, '2026-08-20'
    price = np.full((n, seconds), 5.)
    arrays = dict(features=np.zeros((n, seconds, 3), dtype=np.float32), prices=price,
        execution_open=price.copy(), volume=np.full((n, seconds), 100000.),
        volume_60s=np.full((n, seconds), 100000.), trades_60s=np.full((n, seconds), 100.),
        fresh=np.ones((n, seconds), dtype=bool),
        estimated_reference=np.full((n, seconds), 5., dtype=np.float32),
        prior_close=np.full(n, 5., dtype=np.float32))
    plan = dict(version=DATA_VERSION, date=day, clock='completed_second', step_us=1000000,
        first_us=bounds(date.fromisoformat(day))[0], rows=seconds, segment=True,
        feature_names=['a', 'b', 'c'], teacher_dependency=False,
        listings=[dict(ticker=f'T{i}', listing_id=f'L{i}') for i in range(n)])
    plan['plan_hash'] = digest(plan)
    session = MarketSession(plan, arrays)
    config = replace(Config(), entry_rank=2, hold_rank=3, history_seconds=4,
        liquidation_buffer_seconds=2, min_volume_60s=0, min_trades_60s=0,
        commission_model='research', fee_ratio=.0001, base_slippage_ratio=.0001,
        impact_ratio=0., volatility_slippage_ratio=0., max_volume_participation=1.)
    parent = tmp_path/'parent'
    bank = tmp_path/'synthetic-bank'
    write(bank/'complete.json', {'synthetic': True})
    manifest = dict(config=config.manifest(), model=dict(features=3, width=8, heads=2),
        feature_names=plan['feature_names'], train=[dict(date=day, root=str(bank),
        plan_hash=plan['plan_hash'], complete_hash=file_hash(bank/'complete.json'))])
    manifest['contract_hash'] = digest(manifest)
    write(parent/'run_manifest.json', manifest)
    policy = PortfolioPolicy(**manifest['model'])
    with torch.no_grad():
        policy.trade_gate.bias.fill_(2.)
    original = {k: v.clone() for k, v in policy.state_dict().items()}
    torch.save(dict(policy=original, iteration=1, contract_hash=manifest['contract_hash']),
               parent/'checkpoint_latest.pt')
    opened = []

    def load_bank(path):
        assert str(path) == str(bank)
        opened.append(path)
        return session

    monkeypatch.setattr(head_refine.MarketSession, 'load', load_bank)
    monkeypatch.setattr(head_refine, 'output_root', lambda: tmp_path/'outputs')
    monkeypatch.setattr(head_refine, 'code_identity', lambda: {'synthetic_test': True})
    assert head_refine.main(['--parent', str(parent), '--name', 'synthetic', '--dates', day,
                            '--device', 'cpu', '--rollout', '8', '--batch-size', '4']) == 0
    root = tmp_path/'outputs'/'head-refine-20261103'/'synthetic'
    assert len(opened) == 1
    assert read(root/'status.json')['status'] == 'complete'
    first, second = (read(root/'rollouts'/f'{i:06d}.json') for i in (1, 2))
    assert (first['second'], second['second']) == (8, 15)
    assert second['summary']['valid_terminal']
    assert second['evidence'] == 'changing-policy-training-returns'
    saved = torch.load(root/'checkpoint_latest.pt', weights_only=False)
    assert saved['environment']['t'] == 15 and saved['completed'] == [day]
    for key, value in original.items():
        if key.split('.')[0] not in head_refine.HEADS:
            assert torch.equal(value, saved['policy'][key])
