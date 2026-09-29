import polars as pl
import torch
import numpy as np
import pytest

from research.rl_trading.v1.dynamic_supervision import order_labels
from research.rl_trading.v1.model_v5 import DynamicMarketPolicy
from research.rl_trading.v1.objective_v5 import teacher_loss
from research.rl_trading.v1.v5_feature_binding import (
    FEATURE_BANK_SECONDS, FeatureBinding, feature_chunks)
from research.rl_trading.v1.v5_order_adapter import (
    order_seconds, empty_stop_seconds, stop_second_indices)
from research.rl_trading.v1.features import FEATURE_NAMES


def test_streaming_120_second_context_matches_full_causal_sequence():
    torch.manual_seed(7)
    model = DynamicMarketPolicy(features=3, ticker_vocabulary=4,
                                history_seconds=120, width=16).eval()
    seconds = torch.randn(1, 125, 2, 3)
    state = model.initial_state(1, 2, device=seconds.device, dtype=seconds.dtype)
    with torch.no_grad():
        full = model.encode_sequence(seconds)
        streamed = []
        for tick in range(seconds.shape[1]):
            encoded, state = model.advance(seconds[:, tick], state)
            streamed.append(encoded)
        streamed = torch.stack(streamed, 1)
        assert torch.allclose(full, streamed, atol=2e-6)
        chunk_state = model.initial_state(1, 2, device=seconds.device, dtype=seconds.dtype)
        chunks = []
        for start, end in ((0, 17), (17, 120), (120, 125)):
            part, chunk_state = model.encode_chunk(seconds[:, start:end], chunk_state)
            chunks.append(part)
        assert torch.allclose(full, torch.cat(chunks, 1), atol=2e-6)
        altered = seconds.clone()
        altered[:, 124] += 100
        assert torch.allclose(full[:, :124], model.encode_sequence(altered)[:, :124])
        altered = seconds.clone()
        altered[:, 0] += 100
        assert torch.allclose(full[:, 120:], model.encode_sequence(altered)[:, 120:], atol=2e-6)


def test_teacher_order_and_size_are_remembered_across_seconds():
    torch.manual_seed(8)
    model = DynamicMarketPolicy(features=2, ticker_vocabulary=3,
                                history_seconds=4, width=16).eval()
    state = model.initial_state(1, 2, device=torch.device('cpu'), dtype=torch.float32)
    common = dict(ticker_id=torch.tensor([[1, 2]]), valid=torch.tensor([[True, True]]),
                  held_index=torch.tensor([[0]]), held_valid=torch.tensor([[True]]),
                  held_features=torch.zeros(1, 1, 4),
                  account_by_order=torch.ones(1, 2, 5),
                  action_mask=torch.ones(1, 2, 4, dtype=torch.bool),
                  teacher_tokens=torch.tensor([[1, 0]]),
                  order_valid=torch.tensor([[True, True]]))
    with torch.no_grad():
        logits, sizes, after = model.teacher_forced_second(
            torch.ones(1, 2, 2), state, teacher_sizes=torch.tensor([[.2, 0.]]), **common)
        _, _, other = model.teacher_forced_second(
            torch.ones(1, 2, 2), state, teacher_sizes=torch.tensor([[.8, 0.]]), **common)
        assert logits.shape == (1, 2, 4)
        assert sizes.shape == (1, 2, 2)
        assert not torch.allclose(after.actions, other.actions)
        next_logits, _, _ = model.teacher_forced_second(
            torch.ones(1, 2, 2), after, teacher_sizes=torch.tensor([[.2, 0.]]), **common)
        other_logits, _, _ = model.teacher_forced_second(
            torch.ones(1, 2, 2), other, teacher_sizes=torch.tensor([[.2, 0.]]), **common)
        assert not torch.allclose(next_logits, other_logits)
        listings = torch.ones(1, 2, 16)
        held = torch.ones(1, 1, 16)
        unchanged = model.remember_action(state.actions, listings, held,
                                          torch.tensor([0]), torch.tensor([0.]))
        assert torch.equal(unchanged, state.actions)


def test_teacher_forced_encoded_second_updates_holdings_within_second():
    torch.manual_seed(9)
    model = DynamicMarketPolicy(features=2, ticker_vocabulary=3,
                                history_seconds=4, width=16).eval()
    state = model.initial_state(1, 2, device=torch.device('cpu'), dtype=torch.float32)
    encoded, state = model.advance(torch.ones(1, 2, 2), state)
    # The first token sells the only holding; the next order must see its slot
    # as absent even though both decisions share the same completed second.
    masks = torch.tensor([[[True, True, True, True],
                           [True, True, True, False]]])
    with torch.no_grad():
        actions, _, after = model.teacher_forced_encoded_second(
            encoded, state, ticker_id=torch.tensor([[1, 2]]),
            valid=torch.tensor([[True, True]]),
            held_index_by_order=torch.tensor([[[0], [0]]]),
            held_valid_by_order=torch.tensor([[[True], [False]]]),
            held_features_by_order=torch.zeros(1, 2, 1, 4),
            account_by_order=torch.tensor([[[50., 100., 0., .5, 1.],
                                            [110., 110., 10., 0., 0.]]]),
            action_mask=masks, teacher_tokens=torch.tensor([[3, 0]]),
            teacher_sizes=torch.zeros(1, 2),
            order_valid=torch.tensor([[True, True]]))
    assert actions.shape == (1, 2, 4)
    assert actions[0, 1, 3] == torch.finfo(actions.dtype).min
    assert not torch.equal(after.actions, state.actions)


def test_dynamic_supervision_orders_sells_first_and_sizes_remaining_cash():
    trajectory = pl.DataFrame(dict(time_us=[0, 1, 2], cash=[50., 40., 120.],
                                   profit_bank=[0., 0., 0.], bought=[1, 2, 0],
                                   sold=[0, 1, 2]))
    positions = pl.DataFrame(dict(
        entry_us=[0, 1, 1], exit_us=[1, 2, 2], ticker=['A', 'B', 'C'],
        episode_uid=['A:1', 'B:1', 'C:1'], quantity=[5., 3., 4.],
        entry_price=[10., 10., 10.], exit_price=[12., 35./3., 11.25],
        entry_fee=[0., 0., 0.], exit_fee=[0., 0., 0.],
        net_pnl=[10., 5., 5.], forced_terminal=[False, False, False]))
    labels = order_labels(trajectory, positions, 100.)
    same_second = labels.filter(pl.col('time_us') == 1)
    assert same_second['action'].to_list() == ['sell', 'buy', 'buy']
    assert same_second['order_index'].to_list() == [0, 1, 2]
    assert same_second['allocation_weight'].to_list()[1:] == [30/110, 40/110]
    assert same_second['remaining_cash_weight'].to_list()[1:] == [30/110, 40/80]


def test_v5_loss_learns_buy_size_and_excludes_padded_orders():
    logits = torch.randn(1, 4, 9, requires_grad=True)
    sizes = torch.randn(1, 4, 3, requires_grad=True)
    tokens = torch.tensor([[5, 2, 0, 0]])  # sell slot 1, buy slot 2, STOP, pad
    weights = torch.tensor([[0., .4, 0., 0.]])
    valid = torch.tensor([[True, True, True, False]])
    loss, metrics = teacher_loss(logits, sizes, tokens, weights, valid)
    assert torch.isfinite(loss)
    assert metrics['active_orders'] == 3
    assert metrics['buy_orders'] == 1
    loss.backward()
    assert sizes.grad[0, 1, 1] != 0
    assert sizes.grad[0, 3].abs().sum() == 0
    assert logits.grad[0, 3].abs().sum() == 0


def test_feature_chunks_stop_at_teacher_cutoff_without_window_duplication(tmp_path):
    path = tmp_path / 'features.npy'
    bank = np.lib.format.open_memmap(path, mode='w+', dtype=np.float32,
                                     shape=(2, FEATURE_BANK_SECONDS, 3))
    bank[0, :7, 0] = np.arange(7)
    bank[1, :7, 0] = np.arange(7) + 10
    bank.flush()
    del bank
    bound = FeatureBinding('2026-07-30', ('A', 'B'), 7, 3, path, 'hash',
                           tmp_path / 'orders.parquet', 'hash', 'plan')
    chunks = list(feature_chunks(bound, seconds_per_chunk=3, device=torch.device('cpu')))
    assert [start for start, _ in chunks] == [0, 3, 6]
    assert [chunk.shape[1] for _, chunk in chunks] == [3, 3, 1]
    assert torch.cat([chunk for _, chunk in chunks], 1)[0, :, 0, 0].tolist() == list(range(7))


def test_v5_order_adapter_reconstructs_sells_sweep_buys_and_stop():
    trajectory = pl.DataFrame(dict(
        time_us=[0, 1_000_000, 2_000_000, 3_000_000],
        cash=[50., 50., 100., 100.],
        profit_bank=[0., 10., 18., 18.],
        realized_net_pnl=[0., 10., 18., 18.],
        bought=[1, 2, 0, 0], sold=[0, 1, 2, 0], open_lots=[1, 2, 0, 0]))
    positions = pl.DataFrame(dict(
        entry_us=[0, 1_000_000, 1_000_000],
        exit_us=[1_000_000, 2_000_000, 2_000_000],
        ticker=['A', 'B', 'C'], episode_uid=['A:1', 'B:1', 'C:1'],
        quantity=[5., 3., 2.], entry_price=[10., 10., 10.],
        exit_price=[12., 12., 11.], entry_fee=[0., 0., 0.],
        exit_fee=[0., 0., 0.], net_pnl=[10., 6., 2.],
        forced_terminal=[False, True, True]))
    orders = order_labels(trajectory, positions, 100.)
    bank = np.zeros((3, FEATURE_BANK_SECONDS, len(FEATURE_NAMES)), dtype=np.float32)
    bank[:, :4, FEATURE_NAMES.index('price_available')] = 1
    for second, prices in enumerate(((10., 10., 10.),
                                     (12., 10., 10.),
                                     (12., 12., 11.))):
        bank[:, second, FEATURE_NAMES.index('log_price')] = np.log(prices)
    steps = list(order_seconds(orders, trajectory, positions, bank,
                               ('A', 'B', 'C'), initial_cash=100.))
    assert [step.token.tolist() for step in steps] == [
        [1, 0], [4, 2, 3, 0], [4, 4, 0]]
    assert steps[1].account[:, 0].tolist() == [50., 100., 70., 50.]
    assert steps[1].held_valid[:, :2].tolist() == [
        [True, False], [False, False], [True, False], [True, True]]
    assert steps[1].size[1:3].tolist() == pytest.approx([.3, 20/70])
    assert stop_second_indices(np.asarray([0, 1, 2]), 4,
                               radius=1, background_stride=60).tolist() == [3]
    empty = list(empty_stop_seconds(np.asarray([3]), trajectory,
                                    positions, bank, ('A', 'B', 'C')))
    assert len(empty) == 1 and empty[0].token.tolist() == [0]
    assert empty[0].account[0, :3].tolist() == [100., 118., 18.]
    assert not empty[0].held_valid.any()
