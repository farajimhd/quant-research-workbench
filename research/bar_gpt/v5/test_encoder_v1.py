"""Behavioral checks for column masking, scaling, the bottleneck, and actual training."""

from copy import deepcopy

import pytest
import torch

from research.bar_gpt.v5.encoder_v1 import EncoderConfig, MaskedAutoencoderV1
from research.bar_gpt.v5.pretraining_v1 import ACTIVITY, PRICES, fit_activity_stats, prepare_batch
from research.bar_gpt.v5.trainer_v1 import MAETrainerV1, RawBatch, reconstruction_loss


def raw_batch(batch=4):
    """Synthetic positive market evidence, not a claim of real-data training quality."""
    generator = torch.Generator().manual_seed(41)
    values = torch.rand((batch, 64, 18), generator=generator) + 10
    values[..., PRICES] = 20 + torch.arange(64)[None, :, None] * 0.01
    valid = torch.ones_like(values, dtype=torch.bool)
    ends = (1_700_000_000_000_000 + torch.arange(64) * 1_000_000).expand(batch, -1).clone()
    return RawBatch(values, valid, ends, 1_000_000)


def small_model(dropout=0):
    """Small instance of the same runnable architecture keeps unit tests bounded."""
    return MaskedAutoencoderV1(EncoderConfig(
        width=32, layers=1, heads=4, feedforward=64,
        decoder_width=16, decoder_layers=1, decoder_heads=2, dropout=dropout,
    ))


def prepared(raw, stats, seed=0, rate=0.6):
    """Use a reproducible mask independent of global model/dropout randomness."""
    return prepare_batch(raw.values, raw.valid, raw.end_us, raw.timeframe_us,
                         stats, torch.Generator().manual_seed(seed), mask_rate=rate)


def test_scaling_and_shared_activity_statistics():
    raw = raw_batch()
    stats = fit_activity_stats(raw.values, raw.valid)
    assert stats.median[2] == stats.median[4] and stats.scale[3] == stats.scale[5]
    batch = prepared(raw, stats, rate=0)
    restored = torch.empty_like(raw.values.transpose(1, 2)).scatter_(
        1, batch.feature_ids[..., None].expand(-1, -1, 64), batch.visible,
    ).transpose(1, 2)
    reference = raw.values[..., (3, 9, 15)].flatten(1).quantile(0.5, dim=1)[:, None, None]
    torch.testing.assert_close(restored[..., PRICES], 100 * (raw.values[..., PRICES]/reference).log(), atol=1e-5, rtol=1e-4)
    torch.testing.assert_close(restored[..., ACTIVITY], (raw.values[..., ACTIVITY].log1p()-stats.median)/stats.scale)
    assert batch.targets.shape == (4, 0, 64)


def test_hidden_values_cannot_influence_visible_inputs_or_embedding():
    raw = raw_batch()
    stats = fit_activity_stats(raw.values, raw.valid)
    before = prepared(raw, stats)
    altered = raw.values.clone().transpose(1, 2)
    hidden = torch.zeros((4, 18), dtype=torch.bool).scatter_(1, before.hidden_ids, True)
    # Alter every hidden market column; visible inputs must stay identical.
    alterable = hidden.clone()
    altered = torch.where(alterable[..., None], altered * 3, altered).transpose(1, 2)
    after = prepared(RawBatch(altered, raw.valid, raw.end_us, raw.timeframe_us), stats)
    for name in ("visible", "valid", "feature_ids", "hidden_ids", "time", "time_valid"):
        torch.testing.assert_close(getattr(before, name), getattr(after, name))
    assert not torch.equal(before.targets, after.targets)
    model = small_model().eval()
    with torch.no_grad():
        z1 = model.encoder(before.visible, before.valid, before.feature_ids, before.time, before.time_valid)
        z2 = model.encoder(after.visible, after.valid, after.feature_ids, after.time, after.time_valid)
    torch.testing.assert_close(z1, z2)


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda", marks=pytest.mark.skipif(
    not torch.cuda.is_available(), reason="CUDA unavailable",
))])
def test_packed_attention_shapes_and_gradients(device):
    raw = raw_batch()
    batch = prepared(raw, fit_activity_stats(raw.values, raw.valid)).to(device)
    model = small_model().to(device)
    lengths = []
    handle = model.encoder.blocks[0].register_forward_pre_hook(lambda module, args: lengths.append(args[0].shape[1]))
    embedding, predictions = model(batch.visible, batch.valid, batch.feature_ids, batch.hidden_ids, batch.time, batch.time_valid)
    handle.remove()
    assert lengths == [batch.visible.shape[1] + 2]
    assert lengths[0] in (9, 10) and embedding.shape == (4, 32)
    assert predictions.shape == batch.targets.shape
    loss, _, _ = reconstruction_loss(predictions, batch)
    loss.backward()
    assert model.encoder.summary.grad.abs().sum() > 0
    assert torch.isfinite(model.encoder.values.weight.grad).all()


def test_mask_rate_and_missing_close_fail_closed():
    raw = raw_batch(1)
    stats = fit_activity_stats(raw.values, raw.valid)
    rates = [prepared(raw, stats, seed=seed).hidden_ids.numel()/18 for seed in range(100)]
    assert abs(sum(rates)/len(rates) - 0.6) < 0.015
    raw.valid[..., (3, 9, 15)] = False
    with pytest.raises(ValueError, match="closing price"):
        prepared(raw, stats)


def test_trainer_updates_validation_is_fixed_and_resume_matches():
    torch.manual_seed(10)
    raw = raw_batch()
    stats = fit_activity_stats(raw.values, raw.valid)
    trainer = MAETrainerV1(small_model(), stats, learning_rate=0.003)
    before = trainer.evaluate([raw])["loss"]
    initial_rng = trainer.generator.get_state().clone()
    assert trainer.evaluate([raw])["loss"] == before
    torch.testing.assert_close(trainer.generator.get_state(), initial_rng)
    trainer.train_epoch([raw] * 25)
    assert trainer.evaluate([raw])["loss"] < before
    checkpoint = deepcopy(trainer.state_dict())
    expected = trainer.train_epoch([raw])
    expected_weights = deepcopy(trainer.model.state_dict())
    resumed = MAETrainerV1(small_model(), stats)
    resumed.load_state_dict(checkpoint)
    actual = resumed.train_epoch([raw])
    assert actual == expected
    for name, value in resumed.model.state_dict().items():
        torch.testing.assert_close(value, expected_weights[name], rtol=0, atol=0)


def test_loss_ignores_missing_targets():
    raw = raw_batch()
    batch = prepared(raw, fit_activity_stats(raw.values, raw.valid))
    batch.target_valid[:, :, 0] = False
    predictions = torch.zeros_like(batch.targets)
    first, _, _ = reconstruction_loss(predictions, batch)
    predictions[:, :, 0] = 1000
    second, _, _ = reconstruction_loss(predictions, batch)
    torch.testing.assert_close(first, second)


def test_default_architecture_and_full_column_inference():
    """The actual default model exports one 256-vector with either masked or full input."""
    raw = raw_batch(2)
    stats = fit_activity_stats(raw.values, raw.valid)
    model = MaskedAutoencoderV1().eval()
    masked, complete = prepared(raw, stats), prepared(raw, stats, rate=0)
    with torch.no_grad():
        embedding, reconstruction = model(
            masked.visible, masked.valid, masked.feature_ids, masked.hidden_ids, masked.time, masked.time_valid,
        )
        full_embedding = model.encoder(
            complete.visible, complete.valid, complete.feature_ids, complete.time, complete.time_valid,
        )
    assert embedding.shape == full_embedding.shape == (2, 256)
    assert reconstruction.shape == masked.targets.shape
    # Column identities make storage order irrelevant; this also checks shuffle-at-inference behavior.
    with torch.no_grad():
        permuted = model.encoder(
            complete.visible.flip(1), complete.valid.flip(1), complete.feature_ids.flip(1),
            complete.time, complete.time_valid,
        )
    # FP32 attention reductions change summation order under permutation across four layers.
    torch.testing.assert_close(full_embedding, permuted, rtol=1e-5, atol=5e-6)
