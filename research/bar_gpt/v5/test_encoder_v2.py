"""Runnable z-score/architecture/loader contract checks, independent of CUDA."""

from dataclasses import asdict, replace
import json
import numpy as np
import pytest
import torch

from .encoder_v1 import EncoderConfig as V1Config, MaskedAutoencoderV1
from .encoder_v2 import EncoderConfig, MaskedAutoencoderV2
from .pretraining_v1 import ActivityStats, prepare_batch as old_prepare
from .pretraining_v2 import ZScoreStats, prepare_batch
from .native_validation_v2 import inverse, NativeMAE
from .zscore_calibration_v2 import moments, merge, load_calibration, VERSION
from .shards.contract import FRAMES, digest
from .shards.phase1_contract import SAMPLE_DTYPE, CHANNELS
from .trainer_v1 import reconstruction_loss


def records():
    rows = np.zeros(12, dtype=SAMPLE_DTYPE)
    rng = np.random.default_rng(5)
    rows["values"] = rng.uniform(1, 10000, (12, 64, 18))
    rows["valid"] = 1
    rows["timeframe_us"] = np.repeat(FRAMES, 2)
    for i, f in enumerate(rows["timeframe_us"]):
        rows["end_us"][i] = 1700000000000000 + np.arange(1, 65) * f
    rows["valid"][0, 3, 10] = 0
    rows["values"][
        0, 3, 10
    ] = 1e20  # Invalid source observation must not enter statistics.
    rows["values"][0, 4, 11] = 0  # Valid zero must enter statistics and scoring.
    return rows


def stats_for(rows):
    n, mu, m2 = moments(rows, 3)
    std = np.sqrt(m2 / n)
    return ZScoreStats(torch.tensor(mu), torch.tensor(std))


def packed(rows, full=False, seed=17):
    return prepare_batch(
        torch.from_numpy(rows["values"].copy()),
        torch.from_numpy(rows["valid"].astype(bool)),
        torch.from_numpy(rows["end_us"].copy()),
        torch.from_numpy(rows["timeframe_us"].copy()),
        stats_for(rows),
        torch.Generator().manual_seed(seed),
        native_validation=full,
    )


def test_vectorized_population_stats_and_merge():
    rows = records()
    n, mu, m2 = moments(rows, 3)
    merged = merge(moments(rows[:5]), moments(rows[5:]))
    for actual, expected in zip(merged, (n, mu, m2)):
        np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-8)
    for f, frame in enumerate(FRAMES):
        part = rows[rows["timeframe_us"] == frame]
        for c in range(18):
            truth = part["values"][:, :, c][part["valid"][:, :, c].astype(bool)].astype(
                np.float64
            )
            assert n[f, c] == len(truth)
            np.testing.assert_allclose(mu[f, c], truth.mean(), rtol=1e-12)
            np.testing.assert_allclose(
                m2[f, c] / n[f, c], truth.var(ddof=0), rtol=1e-12
            )


def test_all18_linear_roundtrip_and_unmasked_rng():
    rows = records()
    generator = torch.Generator().manual_seed(17)
    before = generator.get_state().clone()
    b = prepare_batch(
        torch.from_numpy(rows["values"].copy()),
        torch.from_numpy(rows["valid"].astype(bool)),
        torch.from_numpy(rows["end_us"].copy()),
        torch.from_numpy(rows["timeframe_us"].copy()),
        stats_for(rows),
        generator,
        native_validation=True,
    )
    assert torch.equal(before, generator.get_state())
    assert b.visible.shape == (12, 18, 64) and torch.equal(b.feature_ids, b.hidden_ids)
    raw = inverse(b.targets, b.native_reference, b.native_stats)
    torch.testing.assert_close(
        raw[b.native_valid],
        b.native_targets.double()[b.native_valid],
        rtol=1e-5,
        atol=0.001,
    )
    acc = NativeMAE("cpu")
    acc.add(b.targets, b)
    assert acc.counts.sum() == b.native_valid.sum()
    assert acc.overflows.sum() == 0


@pytest.mark.parametrize("seed", [1, 17, 42])
def test_training_random_mask_matches_v1(seed):
    rows = records()
    b = packed(rows, seed=seed)
    old = old_prepare(
        torch.from_numpy(rows["values"].copy()),
        torch.from_numpy(rows["valid"].astype(bool)),
        torch.from_numpy(rows["end_us"].copy()),
        torch.from_numpy(rows["timeframe_us"].copy()),
        ActivityStats(torch.zeros(6), torch.ones(6)),
        torch.Generator().manual_seed(seed),
    )
    assert torch.equal(b.feature_ids, old.feature_ids) and torch.equal(
        b.hidden_ids, old.hidden_ids
    )
    assert torch.equal(b.time, old.time) and torch.equal(b.time_valid, old.time_valid)
    assert b.visible.shape[1] in (7, 8) and b.targets.shape[1] in (10, 11)
    assert b.native_targets.numel() == 0


def test_cloned_architecture_and_real_forward_backward():
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
    oldcfg = V1Config(**dict(asdict(cfg), architecture="column-latest-bar-v1"))
    torch.manual_seed(7)
    old = MaskedAutoencoderV1(oldcfg)
    torch.manual_seed(7)
    new = MaskedAutoencoderV2(cfg)
    assert old.state_dict().keys() == new.state_dict().keys()
    for name, x in new.state_dict().items():
        assert torch.equal(x, old.state_dict()[name])
    b = packed(records())
    _, prediction = new(
        b.visible, b.valid, b.feature_ids, b.hidden_ids, b.time, b.time_valid
    )
    loss, _, _ = reconstruction_loss(prediction, b)
    loss.backward()
    assert torch.isfinite(loss) and all(
        torch.isfinite(p.grad).all() for p in new.parameters() if p.grad is not None
    )
    new.eval()
    b = packed(records(), True)
    with torch.inference_mode():
        _, prediction = new(
            b.visible, b.valid, b.feature_ids, b.hidden_ids, b.time, b.time_valid
        )
    assert prediction.shape == (12, 18, 64)


def test_seal_rejects_wrong_split_and_incomplete_statistics(tmp_path):
    rows = records()
    n, mu, m2 = moments(rows)
    manifest = {
        "manifest_hash": "split-A",
        "counts": {"train": 12},
        "splits": {"train": [{}]},
    }
    result = dict(
        version=VERSION,
        split_hash="split-A",
        channels=list(CHANNELS),
        frames=list(FRAMES),
        training_samples=12,
        completed_files=1,
        ddof=0,
        counts=n.tolist(),
        mean=mu.tolist(),
        std=np.sqrt(m2 / n).tolist(),
    )
    result["hash"] = digest(result)
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(result))
    load_calibration(p, manifest)
    with pytest.raises(ValueError):
        load_calibration(p, dict(manifest, manifest_hash="split-B"))
    result["training_samples"] = 11
    result["hash"] = digest({k: v for k, v in result.items() if k != "hash"})
    p.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        load_calibration(p, manifest)


def test_full_statistics_ledger_resume_and_training_only(tmp_path, monkeypatch):
    """Exercise actual file hashes, reducer, ledger and sealed calibration path."""
    from types import SimpleNamespace
    import psutil
    from . import zscore_calibration_v2 as module

    rows = records()
    path = tmp_path / "train.npy"
    np.save(path, rows, allow_pickle=False)
    import hashlib

    entry = dict(
        path=str(path),
        start=0,
        stop=len(rows),
        bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    manifest = dict(
        manifest_hash="train-only",
        counts={"train": 12},
        splits={"train": [entry], "validation": [{"path": "MUST-NOT-READ"}]},
    )
    monkeypatch.setattr(module, "load_split", lambda _: manifest)
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(available=128 * 2**30)
    )
    directory = tmp_path / "statistics"
    first = module.fit(tmp_path / "split.json", directory, workers=1)
    assert first["training_samples"] == 12 and first["completed_files"] == 1
    assert first["source_bytes_hash_verified"] == path.stat().st_size
    # A completed ledger must not reread/refit files each time it is opened.
    monkeypatch.setattr(module, "payload", lambda _: pytest.fail("Unexpected refit"))
    second = module.fit(tmp_path / "split.json", directory, workers=1)
    assert first == second
    load_calibration(directory / "calibration.json", manifest)


def test_fresh_v2_command_preserves_science_except_transform_and_peak():
    from .run_zscore_upgrade_v2 import training_command
    from .run_encoder_v2 import arguments

    parent = dict(
        dataset="D:/data",
        split_manifest="D:/split.json",
        model="large",
        precision="bf16",
        attention="sdpa",
        attention_dropout=0.0,
        batch_size=4096,
        workers=32,
        prefetch=2,
        epochs=80,
        train_steps=0,
        validation_steps=0,
        scheduler="cosine",
        min_learning_rate=5e-5,
        warmup_updates=0,
        metrics_every=1000,
        loss_every=10,
        checkpoint_every=0,
        seed=17,
        wandb_mode="online",
        wandb_project="bargpt-v6-latest-bar-overnight",
        guard_probe_steps=32,
        overfit_patience=8,
        learning_guard=True,
        panel=True,
    )
    command = training_command(parent, "D:/new-run", "D:/stats.json", "revision")
    args = arguments(command[5:])
    assert args.resume is None and args.learning_rate == 1e-3 and args.epochs == 80
    assert args.workers == 32 and args.batch_size == 4096 and args.warmup_updates == 0
    assert (
        args.overfit_patience == 8
        and args.validation_steps == 0
        and args.train_steps == 0
    )
    assert args.learning_guard and args.panel
