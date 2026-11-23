"""Information preservation, decoder isolation and actual optimization checks."""
import itertools
import torch
from .model import Autoencoder, Config
from .objectives import objective
from .transforms import coordinates, decode, raw_values, GROUPS


def example(dtype=torch.float64):
    """Include a zero size, extreme volume, tiny price and differing family scales."""
    raw = torch.tensor([[.1, .12, .09, .11, 1e12, 0,
                         .099, .119, .089, .109, 0, 7,
                         .101, .121, .091, .111, 150, 11]], dtype=dtype)
    logs = torch.stack([raw[..., j].log() if j % 6 < 4 else raw[..., j].log1p()
                        for j in range(18)], -1)
    return raw, torch.cat((logs, torch.zeros(1, 9, dtype=dtype)), -1)


def parameters():
    """Identity normalization is for synthetic tests only, not production fitting."""
    return dict(mean=[0.]*27, scale=[1.]*27)


def test_all_activity_missing_patterns_roundtrip():
    raw, values = example()
    # Both triples independently cover all 8 validity patterns, including empty.
    for bits in itertools.product((False, True), repeat=6):
        valid = torch.ones_like(values, dtype=torch.bool)
        for index, bit in zip(sum(GROUPS, ()), bits):
            valid[..., index] = bit
        poisoned = torch.where(valid, values, float('nan'))
        code, mask = coordinates(poisoned[..., :18], valid[..., :18])
        assert torch.isfinite(code).all()
        restored = raw_values(decode(code))
        torch.testing.assert_close(restored[valid[..., :18]], raw[valid[..., :18]],
                                   rtol=1e-12, atol=1e-12)
        for group in GROUPS:
            assert bool(mask[..., list(group)].all()) == any(bool(valid[0, j]) for j in group)


def test_partial_prices_and_unusual_valid_geometry():
    raw, values = example()
    # Keep deliberately unordered valid highs; a prediction projection must not
    # silently rewrite these source values during the transform roundtrip.
    values[0, 1] = values[0, 2]-1
    expected = raw_values(values[..., :18])
    for bits in itertools.product((False, True), repeat=4):
        valid = torch.ones_like(values, dtype=torch.bool)
        valid[..., :4] = torch.tensor(bits)
        code, _ = coordinates(torch.where(valid, values, float('nan'))[..., :18], valid[..., :18])
        restored = raw_values(decode(code))
        torch.testing.assert_close(restored[valid[..., :18]], expected[valid[..., :18]], rtol=1e-12, atol=1e-12)


def test_encoder_is_per_bar_and_decoder_embedding_only():
    torch.manual_seed(3)
    model = Autoencoder(parameters(), Config(chunk_bars=1))
    _, values = example(torch.float32)
    values = values.repeat(2, 1)
    valid = torch.ones_like(values, dtype=torch.bool)
    first = model.encode(values, valid).detach()
    values[1] += 1
    second = model.encode(values, valid).detach()
    torch.testing.assert_close(first[0], second[0])
    out = model.decode(first)
    assert first.shape == (2, 16) and out['logs'].shape == (2, 18)
    assert sum(p.numel() for p in model.decoder.parameters()) == 306
    torch.testing.assert_close(model.reconstruct_raw(first), raw_values(out['logs']))
    restored = Autoencoder(parameters())
    restored.load_state_dict(model.state_dict())
    torch.testing.assert_close(restored.decode(first)['logs'], out['logs'])
    with torch.autocast('cpu', dtype=torch.bfloat16):
        assert model.decode(first)['logs'].dtype == torch.float32
    single = model(values[0], valid[0])
    assert single['logs'].shape == (18,)
    assert torch.isfinite(objective(model, single, values[0], valid[0])[0])


def test_invalid_placeholders_have_finite_gradients_and_empty_loss():
    model = Autoencoder(parameters())
    _, values = example(torch.float32)
    valid = torch.ones_like(values, dtype=torch.bool)
    valid[..., [0, 10, 11, 18]] = False
    values = torch.where(valid, values, float('nan'))
    loss, _ = objective(model, model(values, valid), values, valid)
    loss.backward()
    assert torch.isfinite(loss)
    assert all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters())
    valid.zero_()
    values.fill_(float('nan'))
    loss, _ = objective(model, model(values, valid), values, valid)
    assert loss.item() == 0


def test_tiny_optimizer_learning():
    torch.manual_seed(17)
    torch.set_num_threads(2)
    _, values = example(torch.float32)
    valid = torch.ones_like(values, dtype=torch.bool)
    code, _ = coordinates(values[..., :18], valid[..., :18])
    # Controlled single-bar easy case: train-only calibration, deliberately
    # offset so the decoder must learn rather than start at the answer.
    mean = torch.cat((code[0], values[0, 18:])).tolist()
    mean = [x+.05 for x in mean]
    model = Autoencoder(dict(mean=mean, scale=[1.]*27))
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=0)
    initial = float(objective(model, model(values, valid), values, valid)[0].detach())
    for step in range(240):
        for group in opt.param_groups:
            group['lr'] = 1e-5 + (1e-3-1e-5)*(1-step/239)
        opt.zero_grad(set_to_none=True)
        loss, _ = objective(model, model(values, valid), values, valid)
        loss.backward()
        opt.step()
    final = float(objective(model, model(values, valid), values, valid)[0].detach())
    assert final < initial*.05, (initial, final)
