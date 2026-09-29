import torch

from research.rl_trading.v6.model import (ActualCandleEncoder,
                                          BracketActionDecoder, BracketPolicy)


def test_actual_candle_training_serving_parity_with_clock_gaps():
    torch.manual_seed(17)
    model = ActualCandleEncoder(width=8).eval()
    scalar = torch.randn(5, 37)
    levels = torch.randn(5, 2, 5, 11)
    # Listing 0 has three persisted candles; listing 1 has two. Sparse
    # observations at intervening clock seconds leave each history unchanged.
    a = model.encode_listing(scalar[[0, 2, 4]], levels[[0, 2, 4]])
    b = model.encode_listing(scalar[[1, 3]], levels[[1, 3]])
    state = model.initial_state(2, device=torch.device('cpu'),
                                dtype=torch.float32)
    expected = [a[0], b[0], a[1], b[1], a[2]]
    for row, listing in enumerate([0, 1, 0, 1, 0]):
        previous_other = state.encoded[1-listing].clone()
        state = model.observe(state, torch.tensor([listing]),
                              scalar[row:row+1], levels[row:row+1])
        assert torch.allclose(state.encoded[listing], expected[row], atol=1e-5)
        assert torch.equal(state.encoded[1-listing], previous_other)
    assert state.seen.tolist() == [3, 2]


def test_causal_sequence_ignores_future_candle():
    torch.manual_seed(4)
    model = ActualCandleEncoder(width=4).eval()
    scalar = torch.randn(3, 37)
    levels = torch.randn(3, 2, 5, 11)
    before = model.encode_listing(scalar, levels)
    scalar[-1] *= 100
    after = model.encode_listing(scalar, levels)
    assert torch.equal(before[:2], after[:2])


def test_five_action_decoder_masks_brackets_until_admissible():
    torch.manual_seed(5)
    decoder = BracketActionDecoder(width=8)
    listings = torch.randn(3, 8)
    account = torch.tensor([10_000., 10_000., 0., 0., 0., 0., 0.])
    held_index = torch.tensor([1])
    held_features = torch.zeros(1, 9)
    logits, size, stop, target = decoder(
        listings, account, held_index, held_features,
        enter_allowed=torch.tensor([True, False, True]),
        exit_allowed=torch.tensor([True]),
        stop_allowed=torch.tensor([False]),
        target_allowed=torch.tensor([True]))
    # HOLD, three ENTER listings, one EXIT, one SET_STOP, one SET_TARGET.
    assert logits.shape == (7,) and size.shape == (3,)
    assert stop.shape == target.shape == (1,)
    assert logits[2] == torch.finfo(logits.dtype).min
    assert logits[5] == torch.finfo(logits.dtype).min
    assert torch.isfinite(logits[[0, 1, 3, 4, 6]]).all()
    assert ((size >= 0) & (size <= 1)).all()
    armed = held_features.clone()
    armed[0, 4:8] = torch.tensor([.02, .04, 1., 1.])
    changed = decoder(listings, account, held_index, armed,
        enter_allowed=torch.tensor([True, False, True]),
        exit_allowed=torch.tensor([True]),
        stop_allowed=torch.tensor([False]),
        target_allowed=torch.tensor([True]))[0]
    assert not torch.equal(logits[[4, 6]], changed[[4, 6]])


def test_v6_policy_remembers_actual_execution_and_resets_by_session():
    torch.manual_seed(9)
    policy = BracketPolicy(width=8)
    state = policy.initial_action_state(device=torch.device('cpu'),
                                        dtype=torch.float32)
    listed = torch.randn(3, 8)
    account = torch.tensor([10000., 10000., 0., 0., 0., 0., 0.])
    held_index = torch.empty(0, dtype=torch.long)
    held_features = torch.empty(0, 9)
    masks = dict(enter_allowed=torch.tensor([True, True, False]),
                 exit_allowed=torch.empty(0, dtype=torch.bool),
                 stop_allowed=torch.empty(0, dtype=torch.bool),
                 target_allowed=torch.empty(0, dtype=torch.bool))
    before = policy.decide(listed, account, held_index, held_features,
                           state, **masks)[0]
    held = policy.remember_execution(state, listed[0], action=0,
        requested_fraction=torch.tensor(0.),
        filled_fraction=torch.tensor(0.),
        realized_net_over_equity=torch.tensor(0.))
    assert torch.equal(held.memory, state.memory)
    changed = policy.remember_execution(state, listed[0], action=1,
        requested_fraction=torch.tensor(.4),
        filled_fraction=torch.tensor(.2),
        realized_net_over_equity=torch.tensor(0.))
    after = policy.decide(listed, account, held_index, held_features,
                          changed, **masks)[0]
    assert not torch.equal(before, after)
    after[0].backward()
    assert policy.action_gru.weight_ih.grad is not None
    assert torch.equal(policy.initial_action_state(
        device=torch.device('cpu'), dtype=torch.float32).memory,
        state.memory)
