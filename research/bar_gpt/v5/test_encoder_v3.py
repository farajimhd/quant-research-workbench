"""V3 reference, unrestricted masking, inverse and executable loader checks."""

from dataclasses import asdict
import hashlib
import numpy as np
import pytest
import torch

from .pretraining_v1 import PRICES, ACTIVITY, CLOSES
from .pretraining_v3 import (
    LocalReferencePolicy,
    prepare_batch,
    local_references,
    TRANSFORM,
)
from .native_validation_v3 import inverse, NativeMAE
from .encoder_v1 import EncoderConfig as OldConfig, MaskedAutoencoderV1
from .encoder_v3 import EncoderConfig, MaskedAutoencoderV3
from .shards.contract import FRAMES
from .shards.phase1_contract import SAMPLE_DTYPE


def raw(batch=12):
    rng = np.random.default_rng(17)
    rows = np.zeros(batch, dtype=SAMPLE_DTYPE)
    rows["values"] = rng.uniform(1, 100, (batch, 64, 18))
    rows["valid"] = 1
    rows["timeframe_us"] = np.resize(FRAMES, batch)
    for i, f in enumerate(rows["timeframe_us"]):
        rows["end_us"][i] = 1700000000000000 + np.arange(1, 65) * f
    return rows


def pack(rows, seed=17, full=False):
    return prepare_batch(
        torch.from_numpy(rows["values"].copy()),
        torch.from_numpy(rows["valid"].astype(bool)),
        torch.from_numpy(rows["end_us"].copy()),
        torch.from_numpy(rows["timeframe_us"].copy()),
        LocalReferencePolicy(),
        torch.Generator().manual_seed(seed),
        native_validation=full,
    )


def test_references_use_raw_observed_medians_and_legitimate_zeros():
    rows = raw(2)
    rows["values"][:, :, 4] = np.arange(64)
    rows["values"][0, 63, 4] = 1e30
    rows["valid"][0, 63, 4] = 0
    rows["values"][:, :, 11] = 0
    rows["values"][:, :, 16] = 1e30
    rows["valid"][:, :, 16] = 0
    b = pack(rows, full=True)
    assert b.native_stats[0, 0] == 31 and b.native_stats[1, 0] == 31.5
    assert torch.equal(b.native_stats[:, 3], torch.zeros(2, dtype=torch.float64))
    assert torch.equal(b.native_stats[:, 4], torch.zeros(2, dtype=torch.float64))
    expected = np.quantile(
        rows["values"][:, :, [3, 9, 15]].reshape(2, -1).astype(np.float64), 0.5, axis=1
    )
    np.testing.assert_allclose(b.native_reference[:, 0, 0], expected)
    expected_z = np.log1p(10) - np.log1p(31)
    assert b.targets[0, 4, 10].item() == pytest.approx(expected_z, rel=1e-6)
    assert not b.native_valid[:, :, 63][0, 4]


def test_all18_roundtrip_all_six_frames_and_raw_counts():
    rows = raw()
    b = pack(rows, full=True)
    decoded = inverse(b.targets, b.native_reference, b.native_stats)
    torch.testing.assert_close(decoded, b.native_targets.double(), rtol=2e-6, atol=2e-5)
    manual = np.empty_like(decoded.numpy())
    manual[:, PRICES] = b.native_reference.numpy() * np.exp(
        b.targets[:, PRICES].double().numpy() / 100
    )
    manual[:, ACTIVITY] = (1 + b.native_stats.numpy()[:, :, None]) * np.exp(
        b.targets[:, ACTIVITY].double().numpy()
    ) - 1
    np.testing.assert_allclose(decoded.numpy(), manual, rtol=1e-12, atol=1e-12)
    acc = NativeMAE("cpu")
    acc.add(b.targets, b)
    assert acc.counts.sum() == rows["valid"].sum() and acc.overflows.sum() == 0
    assert all(
        v is not None
        for k, v in acc.metrics().items()
        if k.startswith("validation_full_raw_mae/")
    )


def test_uniform_masking_has_no_protected_closes_and_every_feature_is_sampled():
    rows = raw(512)
    b = pack(rows, seed=7)
    hidden = torch.zeros((512, 18), dtype=torch.bool).scatter_(1, b.hidden_ids, True)
    assert hidden[:, CLOSES].all(1).any()  # V1 explicitly rejected these samples.
    rates = hidden.float().mean(0)
    assert ((rates > 0.45) & (rates < 0.75)).all()
    assert b.visible.shape[1] in (7, 8) and b.targets.shape[1] in (10, 11)
    assert b.native_reference.numel() == 0 and b.native_stats.numel() == 0
    for visible, target in zip(b.feature_ids.tolist(), b.hidden_ids.tolist()):
        assert set(visible).isdisjoint(target) and set(visible + target) == set(
            range(18)
        )


def test_full_validation_does_not_consume_mask_rng():
    rows = raw()
    g = torch.Generator().manual_seed(17)
    before = g.get_state().clone()
    b = prepare_batch(
        torch.from_numpy(rows["values"].copy()),
        torch.from_numpy(rows["valid"].astype(bool)),
        torch.from_numpy(rows["end_us"].copy()),
        torch.from_numpy(rows["timeframe_us"].copy()),
        LocalReferencePolicy(),
        g,
        native_validation=True,
    )
    assert torch.equal(before, g.get_state())
    assert b.visible.shape == (12, 18, 64) and b.feature_ids.is_contiguous()


def test_training_and_validation_share_exact_premask_normalization():
    rows = raw(8)
    full = pack(rows, full=True)
    for seed in (1, 7, 42):
        masked = pack(rows, seed)
        a = masked.feature_ids[..., None].expand(-1, -1, 64)
        b = masked.hidden_ids[..., None].expand(-1, -1, 64)
        assert torch.equal(masked.visible, full.visible.gather(1, a))
        assert torch.equal(masked.targets, full.targets.gather(1, b))
        assert torch.equal(masked.time, full.time) and torch.equal(
            masked.time_valid, full.time_valid
        )


def test_all_missing_prices_fail_no_fallback():
    rows = raw(1)
    rows["valid"][:, :, CLOSES] = 0
    with pytest.raises(ValueError, match="closing price"):
        pack(rows)


def test_zero_quantities_decode_without_clipping_and_overflow_is_explicit():
    rows = raw(1)
    rows["values"][:, :, ACTIVITY] = 0
    b = pack(rows, full=True)
    assert torch.equal(b.targets[:, ACTIVITY], torch.zeros_like(b.targets[:, ACTIVITY]))
    decoded = inverse(b.targets, b.native_reference, b.native_stats)
    assert torch.equal(decoded[:, ACTIVITY], torch.zeros_like(decoded[:, ACTIVITY]))
    p = b.targets.clone()
    p[:, 4] = 1000
    acc = NativeMAE("cpu")
    acc.add(p, b)
    m = acc.metrics()
    assert m["validation_full_raw_mae/100ms/trade_volume"] is None
    assert m["validation_full_raw_mae_overflows/100ms/trade_volume"] == 64


def test_architecture_copy_and_real_masked_backward_and_full_forward():
    cfg = EncoderConfig(
        width=32,
        layers=1,
        heads=4,
        feedforward=64,
        decoder_width=16,
        decoder_layers=1,
        decoder_heads=4,
        dropout=0,
    )
    torch.manual_seed(17)
    old = MaskedAutoencoderV1(
        OldConfig(
            **dict(
                {k: v for k, v in asdict(cfg).items() if k != "attention_layout"},
                architecture="column-latest-bar-v1",
            )
        )
    )
    torch.manual_seed(17)
    new = MaskedAutoencoderV3(cfg)
    assert old.state_dict().keys() == new.state_dict().keys()
    for k, v in new.state_dict().items():
        assert torch.equal(v, old.state_dict()[k])
    from .trainer_v1 import reconstruction_loss

    b = pack(raw(4))
    _, prediction = new(
        b.visible, b.valid, b.feature_ids, b.hidden_ids, b.time, b.time_valid
    )
    loss, _, _ = reconstruction_loss(prediction, b)
    loss.backward()
    assert torch.isfinite(loss) and all(
        torch.isfinite(p.grad).all() for p in new.parameters() if p.grad is not None
    )
    new.eval()
    b = pack(raw(4), full=True)
    with torch.inference_mode():
        _, p = new(
            b.visible, b.valid, b.feature_ids, b.hidden_ids, b.time, b.time_valid
        )
    assert p.shape == (4, 18, 64)


def test_real_v3_loader_and_policy_do_not_fit_global_statistics(tmp_path):
    from .data_v3 import EligibleStream, calibrate

    rows = raw(24)
    path = tmp_path / "samples.npy"
    np.save(path, rows, allow_pickle=False)
    item = dict(
        path=str(path),
        start=0,
        stop=len(rows),
        bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    manifest = dict(
        manifest_hash="sealed-split", splits={"train": [item], "validation": [item]}
    )
    policy, descriptor = calibrate(manifest)
    assert (
        descriptor["version"] == TRANSFORM
        and not descriptor["fitted_global_statistics"]
    )
    training = EligibleStream(manifest, "train", policy, 8, seed=17)
    b = next(iter(training))
    assert b.visible.shape[1] in (7, 8)
    validation = EligibleStream(manifest, "validation", policy, 8, seed=17)
    validation.full_validation = True
    b = next(iter(validation))
    assert b.visible.shape == (8, 18, 64) and b.native_stats.shape == (8, 6)


def test_v3_checkpoint_contract_does_not_accept_v1_reference_policy():
    from .campaign_v3 import compatible_resume_contract

    policy = dict(
        model={"architecture": "column-latest-bar-local-reference-v3"},
        preprocessing=TRANSFORM,
    )
    assert compatible_resume_contract(policy, policy)
    assert not compatible_resume_contract(
        dict(model={"architecture": "column-latest-bar-v1"}), policy
    )


def test_real_v3_campaign_routes_masks_and_serializes_policy(tmp_path, monkeypatch):
    """CPU fake-kernel orchestration; actual model/math are tested separately."""
    from types import SimpleNamespace
    from . import campaign_v3, run_encoder_v3
    from .test_campaign_transition_v1 import TinyModel, Reporter

    monkeypatch.setattr(campaign_v3, "MaskedAutoencoderV3", TinyModel)
    monkeypatch.setattr(torch.cuda, "get_rng_state_all", lambda: [])
    monkeypatch.setattr(torch.cuda, "set_rng_state_all", lambda _: None)
    monkeypatch.setattr(campaign_v3, "close_loader", lambda _: None)

    class Loader:
        def __init__(self):
            self.dataset = SimpleNamespace(counters=torch.zeros(1, 3))

        def __len__(self):
            return 1

    monkeypatch.setattr(run_encoder_v3, "loader_for", lambda *a, **k: Loader())
    calls = []

    def steps(model, optimizer, loader, **kw):
        calls.append(
            (kw["training"], loader.dataset.full_validation, kw["metric_prefix"])
        )
        if kw["training"]:
            kw["before_step"](0)
            kw["on_step"](0)
        return dict(
            loss=0.3 if kw["training"] else 0.4,
            exhausted=True,
            consumed_batches=1,
            samples=2,
            samples_per_second=100,
            seconds=0.02,
        )

    monkeypatch.setattr(campaign_v3, "run_steps", steps)
    args = run_encoder_v3.arguments(
        [
            "--mode",
            "train",
            "--output",
            str(tmp_path),
            "--model",
            "small",
            "--batch-size",
            "2",
            "--workers",
            "1",
            "--epochs",
            "1",
            "--scheduler",
            "cosine",
            "--train-steps",
            "1",
            "--validation-steps",
            "1",
            "--guard-probe-steps",
            "1",
            "--learning-guard",
            "--warmup-updates",
            "0",
            "--wandb-mode",
            "disabled",
        ]
    )
    campaign_v3.train_campaign(
        args, dict(manifest_hash="split"), LocalReferencePolicy(), Reporter(tmp_path)
    )
    assert any(training and not full for training, full, _ in calls)
    assert all(full for training, full, _ in calls if not training)
    saved = torch.load(
        tmp_path / "checkpoint_last.pt", map_location="cpu", weights_only=False
    )
    assert saved["contract"]["preprocessing"] == TRANSFORM
    assert saved["contract"]["reference_policy"]["version"] == TRANSFORM
    assert saved["step"] == 1 and saved["scheduler"]["updates"] == 1
    assert (tmp_path / "checkpoint_best.pt").exists()


@pytest.mark.parametrize("length", [10, 11, 12])
def test_batch_first_attention_matches_v1_outputs_and_gradients(length):
    from .encoder_v1 import AttentionBlock as OldBlock
    from .encoder_v3 import AttentionBlock

    torch.manual_seed(17)
    old = OldBlock(32, 4, 64, 0.0, "sdpa", 0.0)
    new = AttentionBlock(32, 4, 64, 0.0, "sdpa", 0.0)
    new.load_state_dict(old.state_dict())
    x = torch.randn(3, length, 32, requires_grad=True)
    y = x.detach().clone().requires_grad_()
    expected = old(x)
    actual = new(y)
    torch.testing.assert_close(actual, expected, rtol=2e-5, atol=2e-6)
    expected.square().mean().backward()
    actual.square().mean().backward()
    torch.testing.assert_close(x.grad, y.grad, rtol=2e-4, atol=2e-6)
    for (_, a), (_, b) in zip(old.named_parameters(), new.named_parameters()):
        torch.testing.assert_close(a.grad, b.grad, rtol=2e-4, atol=2e-6)
    old.eval()
    new.eval()
    with torch.inference_mode():
        torch.testing.assert_close(
            new(x.detach()), old(x.detach()), rtol=2e-5, atol=2e-6
        )
