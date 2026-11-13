from types import SimpleNamespace
import torch
import pytest
from research.bar_gpt.v5.encoder_v4 import EncoderConfig, MaskedAutoencoderV4
from research.bar_gpt.v5.encoder_v3 import MaskedAutoencoderV3
from research.bar_gpt.v5.initialization_v4 import initialize_encoder
from research.bar_gpt.v5.balanced_release_v4 import require_release


def config():
    return EncoderConfig(width=32, layers=1, heads=4, feedforward=64,
                         decoder_width=16, decoder_heads=4, decoder_layers=1)


def test_encoder_transfer_leaves_decoder_fresh_and_records_epoch(tmp_path):
    parent = MaskedAutoencoderV3(config())
    model = MaskedAutoencoderV4(config())
    before = {k: v.clone() for k, v in model.decoder.state_dict().items()}
    path = tmp_path / 'epoch50.pt'
    torch.save(dict(model=parent.state_dict(), epoch=50), path)
    proof = initialize_encoder(model, path)
    assert proof['source_epoch'] == 50
    assert all(torch.equal(v, model.encoder.state_dict()[k]) for k, v in parent.encoder.state_dict().items())
    assert all(torch.equal(v, model.decoder.state_dict()[k]) for k, v in before.items())
    assert sum(p.numel() for p in model.decoder.parameters()) < sum(p.numel() for p in parent.decoder.parameters())


def test_mlp_queries_independent_and_masked_loss_reaches_encoder_embedding():
    from research.bar_gpt.v5.trainer_v1 import reconstruction_loss
    model = MaskedAutoencoderV4(config())
    z = torch.randn(2, 32, requires_grad=True)
    ids = torch.tensor([[1, 4], [2, 5]])
    time = torch.zeros(2, 64, 9); valid = torch.ones_like(time, dtype=torch.bool)
    prediction = model.decoder(z, ids, time, valid)
    assert prediction.shape == (2, 2, 64)
    assert torch.allclose(prediction[:, :1], model.decoder(z, ids[:, :1], time, valid), atol=1e-6)
    batch = SimpleNamespace(targets=torch.randn_like(prediction), target_valid=torch.ones_like(prediction, dtype=torch.bool), hidden_ids=ids)
    loss, sums, counts = reconstruction_loss(prediction, batch)
    loss.backward()
    assert z.grad.abs().sum() > 0
    assert counts[0] == 0 and counts[1] == 64


def test_unreleased_dataset_fails_before_training(tmp_path):
    with pytest.raises(ValueError, match='completed audited balanced release'):
        require_release(tmp_path)


def test_launcher_uses_v4_model_and_explicit_checkpoint():
    from research.bar_gpt.v5 import run_encoder_v4, campaign_v4
    assert run_encoder_v4.MaskedAutoencoderV4 is MaskedAutoencoderV4
    assert campaign_v4.MaskedAutoencoderV4 is MaskedAutoencoderV4
    args = run_encoder_v4.arguments(['--output', 'D:/TradingML/runtimes/test', '--encoder-checkpoint', 'epoch50.pt'])
    assert args.encoder_checkpoint.name == 'epoch50.pt'
    assert args.dataset.name == 'phase1-v6-cohort-balanced-v4'


def test_actual_encoder_decoder_backward():
    model = MaskedAutoencoderV4(config())
    visible = torch.randn(2, 7, 64)
    ids = torch.arange(7).expand(2, -1)
    hidden = torch.arange(7, 18).expand(2, -1)
    time = torch.zeros(2, 64, 9)
    z, prediction = model(visible, torch.ones_like(visible, dtype=torch.bool),
                          ids, hidden, time, torch.ones_like(time, dtype=torch.bool))
    assert z.shape == (2, 32) and prediction.shape == (2, 11, 64)
    prediction.square().mean().backward()
    assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in model.encoder.parameters())


def test_foreign_encoder_rejected(tmp_path):
    model = MaskedAutoencoderV4(config())
    path = tmp_path / 'bad.pt'
    torch.save({'model': {'encoder.foreign': torch.zeros(1)}}, path)
    with pytest.raises(ValueError, match='architecture differs'):
        initialize_encoder(model, path)
