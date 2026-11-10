"""Inverse units, unmasked full-column evaluation and unchanged training evidence."""

from dataclasses import fields, replace

import pytest
import torch

from .encoder_v1 import EncoderConfig, MaskedAutoencoderV1
from .native_validation_v1 import NativeMAE, evaluate_full, inverse, prepare_full
from .pretraining_v1 import ActivityStats, prepare_batch
from .shards.contract import FRAMES
from .test_engineering_v1 import inventory


def sample(batch=6, enabled=True):
    values = torch.ones(batch, 64, 18)
    for b in range(batch):
        values[b, :, :] *= 10 ** (b - 2)
    values[..., (4, 5, 10, 11, 16, 17)] = torch.arange(64)[None, :, None].float()
    valid = torch.ones_like(values, dtype=torch.bool)
    valid[0, :4, 6:12] = False
    ends = (
        torch.arange(1, 65)[None].expand(batch, -1) * 1_000_000 + 1_700_000_000_000_000
    )
    stats = ActivityStats(
        torch.tensor([2.0, 1.0, 3.0, 2.0, 3.0, 2.0]),
        torch.tensor([1.0, 2.0, 3.0, 4.0, 3.0, 4.0]),
    )
    frames = torch.tensor(FRAMES)[torch.arange(batch) % 6]
    packed = prepare_batch(
        values,
        valid,
        ends,
        frames,
        stats,
        torch.Generator().manual_seed(73),
        native_validation=enabled,
    )
    return values, valid, stats, packed


def test_inverse_roundtrip_all_heads_and_raw_truth_retained():
    values, valid, _, b = sample()
    raw = inverse(b.native_values, b.native_reference, b.native_stats)
    torch.testing.assert_close(
        raw[b.native_valid],
        values.transpose(1, 2)[b.native_valid].double(),
        rtol=2e-6,
        atol=1e-5,
    )
    assert torch.equal(b.native_targets, values.transpose(1, 2))
    assert torch.equal(b.native_valid, valid.transpose(1, 2))
    assert b.native_values.shape == (6, 18, 64)


def test_full_validation_does_not_draw_masking_rng_and_training_stays_masked():
    values, valid, stats, b = sample(enabled=False)
    ends = torch.arange(1, 65)[None].expand(6, -1) * 1_000_000 + 1_700_000_000_000_000
    a_rng, b_rng = torch.Generator().manual_seed(73), torch.Generator().manual_seed(73)
    plain = prepare_batch(values, valid, ends, torch.tensor(FRAMES), stats, a_rng)
    full = prepare_batch(
        values, valid, ends, torch.tensor(FRAMES), stats, b_rng, native_validation=True
    )
    assert plain.visible.shape[1] < 18 and full.visible.shape == (6, 18, 64)
    assert torch.equal(full.feature_ids, torch.arange(18).expand(6, -1))
    assert full.feature_ids.is_contiguous() and full.feature_ids.stride(0) > 0
    assert torch.equal(full.feature_ids, full.hidden_ids)
    assert torch.equal(full.targets, full.visible)
    assert torch.equal(full.target_valid, full.valid)
    assert torch.equal(b_rng.get_state(), torch.Generator().manual_seed(73).get_state())
    assert not torch.equal(a_rng.get_state(), b_rng.get_state())
    for field in fields(b):
        if field.name.startswith("native_"):
            assert getattr(plain, field.name).numel() == 0
    rng = torch.get_rng_state().clone()
    prepare_full(values, valid, torch.tensor(FRAMES), stats)
    assert torch.equal(rng, torch.get_rng_state())


def test_full_reference_is_all_visible_not_random_mask_reference():
    values, valid, stats, _ = sample()
    values[..., 3], values[..., 9], values[..., 15] = 2.0, 20.0, 200.0
    payload = prepare_full(values, valid, torch.tensor(FRAMES), stats)
    assert torch.equal(
        payload["native_reference"].flatten(),
        torch.full((6,), 20.0, dtype=torch.float64),
    )


def test_known_raw_error_masks_and_timeframe_counts():
    _, _, _, b = sample()
    desired = b.native_targets.double() + 2
    prediction = torch.empty_like(desired)
    prices = (0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15)
    activity = (4, 5, 10, 11, 16, 17)
    prediction[:, prices] = 100 * torch.log(desired[:, prices] / b.native_reference)
    prediction[:, activity] = (
        torch.log1p(desired[:, activity]) - b.native_stats[0, :, None]
    ) / b.native_stats[1, :, None]
    prediction[0, 6, :4] = float("nan")  # Unavailable source evidence is never scored.
    acc = NativeMAE("cpu")
    acc.add(prediction, b)
    m = acc.metrics()
    for key, value in m.items():
        if key.startswith("validation_full_raw_mae/"):
            assert value == pytest.approx(2.0, abs=1e-10)
    assert m["validation_full_raw_mae_counts/100ms/bid_open"] == 60
    assert m["validation_full_raw_mae_counts/1s/trade_close"] == 64
    assert acc.counts.sum().item() == b.native_valid.sum().item()


def test_negative_activity_predictions_are_not_clipped():
    _, _, _, b = sample()
    prediction = b.native_values.double().clone()
    # The source value at bar zero is zero; inverse deliberately predicts -0.5.
    prediction[:, 4, 0] = (
        torch.log(torch.tensor(0.5)) - b.native_stats[0, 0]
    ) / b.native_stats[1, 0]
    decoded = inverse(prediction, b.native_reference, b.native_stats)
    torch.testing.assert_close(
        decoded[:, 4, 0], torch.full((6,), -0.5, dtype=torch.float64), atol=1e-7, rtol=0
    )


def test_overflow_is_reported_without_dropping_denominator_or_clipping():
    _, _, _, b = sample()
    prediction = b.native_values.double().clone()
    prediction[0, 0, 0] = 1e300
    acc = NativeMAE("cpu")
    acc.add(prediction, b)
    m = acc.metrics()
    assert m["validation_full_raw_mae/100ms/trade_open"] is None
    assert m["validation_full_raw_mae_overflows/100ms/trade_open"] == 1
    assert m["validation_full_raw_mae_counts/100ms/trade_open"] == 64


def test_actual_model_full_input_path_has_all_columns_and_no_state_or_rng_change():
    torch.set_num_threads(2)
    _, _, _, b = sample(batch=2)
    config = EncoderConfig(
        width=32,
        heads=4,
        layers=1,
        feedforward=64,
        decoder_width=16,
        decoder_heads=4,
        decoder_layers=1,
        dropout=0.0,
    )
    model = MaskedAutoencoderV1(config).eval()
    tensors = {k: v.clone() for k, v in model.state_dict().items()}
    rng = torch.get_rng_state().clone()
    seen = []
    handle = model.register_forward_pre_hook(lambda _, args: seen.append(args))
    acc = NativeMAE("cpu")
    with torch.inference_mode():
        evaluate_full(model, b, acc, precision="fp32")
    handle.remove()
    assert seen[0][0].shape == (2, 18, 64)
    assert torch.equal(seen[0][2], torch.arange(18).expand(2, -1))
    assert torch.equal(seen[0][2], seen[0][3])
    assert torch.equal(rng, torch.get_rng_state())
    for k, v in model.state_dict().items():
        assert torch.equal(v, tensors[k])
    assert acc.counts.sum().item() == b.native_valid.sum().item()


def test_full_evaluation_rejects_training_mode():
    _, _, _, b = sample(batch=1)
    model = MaskedAutoencoderV1(
        EncoderConfig(
            width=32,
            heads=4,
            layers=1,
            feedforward=64,
            decoder_width=16,
            decoder_heads=4,
            decoder_layers=1,
        )
    )
    with torch.inference_mode(), pytest.raises(ValueError, match="evaluation"):
        evaluate_full(model, b, NativeMAE("cpu"), precision="fp32")


def test_real_engine_publishes_separate_unmasked_metrics_on_cpu(monkeypatch):
    """Run the actual engine/control/reporting path; substitute CUDA transport only."""
    import threading
    from . import engine_v1, native_validation_v1

    _, _, _, batch = sample()

    class Batches:
        def __init__(self, loader):
            self.iterator, self.wait_seconds = iter(loader), 0.0

        def pop(self):
            return next(self.iterator)

        def advance(self):
            pass

    class Event:
        def __init__(self, **_):
            pass

        def record(self):
            pass

        def elapsed_time(self, _):
            return 1.0

    class Model(torch.nn.Module):
        def forward(self, visible, valid, ids, hidden, time, time_valid):
            return torch.empty(0), visible if hidden.shape[1] == 18 else batch.targets

    class Reporter:
        def __init__(self):
            self.rows, self.lock, self.gpu_samples = [], threading.RLock(), []

        def update(self, **_):
            pass

        def log(self, values, step):
            self.rows.append((values, step))

    for name in ("ones", "zeros"):
        original = getattr(torch, name)

        def factory(*args, _original=original, **kwargs):
            if kwargs.get("device") == "cuda":
                kwargs["device"] = "cpu"
            return _original(*args, **kwargs)

        monkeypatch.setattr(torch, name, factory)
    real_accumulator = native_validation_v1.NativeMAE
    monkeypatch.setattr(
        native_validation_v1, "NativeMAE", lambda _: real_accumulator("cpu")
    )
    monkeypatch.setattr(engine_v1, "CudaBatches", Batches)
    monkeypatch.setattr(torch.cuda, "Event", Event)
    for name in ("synchronize", "reset_peak_memory_stats"):
        monkeypatch.setattr(torch.cuda, name, lambda: None)
    for name in ("max_memory_allocated", "max_memory_reserved"):
        monkeypatch.setattr(torch.cuda, name, lambda: 0)
    reporter = Reporter()
    result = engine_v1.run_steps(
        Model(),
        None,
        [batch],
        steps=1,
        precision="fp32",
        training=False,
        reporter=reporter,
        metric_prefix="validation_full",
        metrics_every=1,
        loss_every=1,
    )
    assert result["loss"] == 0
    raw = result["native_validation"]["metrics"]
    assert raw["validation_full_raw_mae/1s/trade_close"] < 1e-5
    assert (
        sum(
            v for k, v in raw.items() if k.startswith("validation_full_raw_mae_counts/")
        )
        == batch.native_valid.sum().item()
    )
    assert any(
        "validation_full/loss" in row
        and "validation_full_raw_mae/1s/trade_close" in row
        for row, _ in reporter.rows
    )


def test_actual_loader_adds_full_payload_only_when_requested(inventory):
    from .data_v1 import EligibleStream

    manifest, stats = inventory
    dataset = EligibleStream(manifest, "validation", stats, 5, seed=17)
    masked = next(iter(dataset))
    assert masked.native_values.numel() == 0
    dataset.full_validation = True
    full = next(iter(dataset))
    assert full.native_values.shape == (5, 18, 64)
    assert full.native_targets.shape == full.native_values.shape
    assert full.visible.shape[1] == full.hidden_ids.shape[1] == 18
    assert masked.visible.shape[1] < 18
    training = next(iter(EligibleStream(manifest, "train", stats, 5, seed=17)))
    assert training.native_values.numel() == 0


def test_wandb_native_series_starts_at_resumed_training_axis_without_backfill():
    import io, json
    from .reporting_v1 import Reporter

    class Run:
        def __init__(self):
            self.rows = []

        def log(self, row):
            self.rows.append(row)

    reporter = Reporter.__new__(Reporter)
    reporter.global_step_offset = 17916
    reporter.journal = io.StringIO()
    reporter.run = Run()
    reporter.log({"validation_full_raw_mae/1s/trade_close": 0.12}, 2317427)
    row = reporter.run.rows[0]
    assert row["optimizer_global_step"] == 2317427
    assert row["global_step"] == 2317427 + 17916
    assert len(reporter.run.rows) == 1
    assert json.loads(reporter.journal.getvalue())["step"] == 2317427


def test_full_input_target_aliases_are_pinned_only_once(monkeypatch):
    _, _, _, batch = sample()
    calls = []

    def pin(tensor):
        calls.append(id(tensor))
        return tensor.clone()

    monkeypatch.setattr(torch.Tensor, "pin_memory", pin)
    pinned = batch.pin_memory()
    assert len(calls) == len({id(getattr(batch, f.name)) for f in fields(batch)})
    assert pinned.visible is pinned.targets is pinned.native_values
    assert pinned.valid is pinned.target_valid is pinned.native_valid
