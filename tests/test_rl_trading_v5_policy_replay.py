import types

import numpy as np
import torch

from research.rl_trading.v1.model_v5 import DynamicMarketPolicy
from research.rl_trading.v1 import v5_policy_replay as runner
from research.rl_trading.v1.v5_policy_replay import (
    execute_and_remember, plan_second, rollout_session)
from research.rl_trading.v1.v5_replay import ReplayAccount, ReplayGrid
from research.rl_trading.v1.features import FEATURE_NAMES
from research.rl_trading.v1.v5_feature_binding import FeatureBinding
from research.rl_trading.v2.config import Config


def _account(*, available=True):
    volume = 1_000. if available else 0.
    grid = ReplayGrid(('A', 'B'),
        close=np.full((2, 3), 10., dtype=np.float32),
        next_open=np.asarray([[10., 10. if available else 0., 10.],
                              [10., 10. if available else 0., 10.]]),
        volume=np.asarray([[1_000., volume, 1_000.],
                           [1_000., volume, 1_000.]]),
        fresh=np.asarray([[True, available, True], [True, available, True]]),
        prior_close=np.asarray([10., 10.]),
        estimated_reference=np.zeros((2, 3)))
    config = Config(initial_cash=100., commission_model='research',
        max_volume_participation=1., base_slippage_ratio=0.,
        impact_ratio=0., volatility_slippage_ratio=0.)
    return ReplayAccount(grid, config)


def _scripted_model(tokens):
    model = DynamicMarketPolicy(features=3, ticker_vocabulary=2,
                                history_seconds=2, width=8)
    selected = iter(tokens)

    def output(self, listings, context, held, history, valid, held_valid, mask):
        token = next(selected)
        assert mask[0, token], 'Script requested an inadmissible order'
        logits = torch.full(mask.shape, -100., device=listings.device)
        logits[0, token] = 100.
        return logits, torch.zeros(1, listings.shape[1], device=listings.device)

    model.order_outputs = types.MethodType(output, model)
    return model


def _inputs(model):
    encoded = torch.zeros(1, 2, model.width)
    ids = torch.tensor([[1, 2]])
    available = torch.ones(1, 2, dtype=torch.bool)
    history = torch.zeros(1, model.width)
    return encoded, ids, available, history


def test_policy_reserves_cash_between_orders_and_remembers_filled_actions():
    account = _account()
    model = _scripted_model([1, 2, 0])
    encoded, ids, available, history = _inputs(model)
    planned, listings = plan_second(model, account, encoded, ids,
                                     available, history)
    assert planned.stopped and [(i.listing, i.cash_fraction)
                                 for i in planned.intents] == [(0, .5), (1, .5)]
    updated, at = execute_and_remember(model, account, planned, listings, history)
    assert at == 1
    assert [account.positions[k].shares for k in sorted(account.positions)] == [5, 2]
    assert account.cash == 30.
    assert not torch.equal(updated, history)


def test_policy_does_not_remember_an_unfilled_order():
    account = _account(available=False)
    model = _scripted_model([1, 0])
    encoded, ids, available, history = _inputs(model)
    planned, listings = plan_second(model, account, encoded, ids,
                                     available, history)
    updated, at = execute_and_remember(model, account, planned, listings, history)
    assert at == -1
    assert torch.equal(updated, history)
    assert account.order_trace[0]['status'] == 'unfilled'
    assert account.cash == 100.


def test_full_rollout_covers_empty_second_and_records_terminal_sale(monkeypatch, tmp_path):
    model = DynamicMarketPolicy(features=len(FEATURE_NAMES), ticker_vocabulary=2,
                                history_seconds=2, width=8)
    selected = iter([1, 0, 0])

    def output(self, listings, context, held, history, valid, held_valid, mask):
        token = next(selected)
        assert mask[0, token]
        logits = torch.full(mask.shape, -100., device=listings.device)
        logits[0, token] = 100.
        return logits, torch.zeros(1, 2, device=listings.device)

    model.order_outputs = types.MethodType(output, model)
    grid = ReplayGrid(('A', 'B'),
        close=np.asarray([[10., 10., 12., 12.], [10., 10., 10., 10.]]),
        next_open=np.asarray([[10., 10., 12., 12.], [10., 10., 10., 10.]]),
        volume=np.full((2, 4), 1_000.),
        fresh=np.full((2, 4), True),
        prior_close=np.asarray([10., 10.]),
        estimated_reference=np.zeros((2, 4)))
    chunk = torch.zeros(1, 2, 2, len(FEATURE_NAMES))
    chunk[:, :, :, FEATURE_NAMES.index('price_available')] = 1.
    monkeypatch.setattr(runner, 'load_execution_grid', lambda *args: grid)
    monkeypatch.setattr(runner, 'feature_chunks',
                        lambda *args, **kwargs: iter([(0, chunk)]))
    binding = FeatureBinding('2026-07-30', ('A', 'B'), 2,
        len(FEATURE_NAMES), tmp_path/'features.npy', 'hash',
        tmp_path/'orders.parquet', 'hash', 'plan')
    account = rollout_session(model, binding, tmp_path,
        np.asarray([1, 2]), _account().config, device=torch.device('cpu'))
    result = account.summary()
    assert result['valid_terminal']
    assert result['terminal_sell_fills'] == 1
    assert result['net_profit'] == 10.
    assert account.position_ledger[0]['exit_kind'] == 'terminal'
