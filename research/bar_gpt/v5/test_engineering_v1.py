"""Exercise real NPY loading, split integrity, multiprocessing, and the CUDA runner."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json

import numpy as np
import pytest
from rich.console import Console
import torch

from research.bar_gpt.v5.data_v1 import EligibleStream, ShardBatches, build_split, calibrate, close_loader, load_split, make_loader
from research.bar_gpt.v5.encoder_v1 import AttentionBlock
from research.bar_gpt.v5.engine_v1 import run_steps
from research.bar_gpt.v5.progress_v1 import render
from research.bar_gpt.v5.shards.phase1_contract import SAMPLE_DTYPE, digest, file_hash, write_json
from research.bar_gpt.v5.test_encoder_v1 import small_model


@pytest.fixture
def inventory(tmp_path):
    """Ten equal-sized dates give a known 80/20 boundary and actual mmap payloads."""
    for offset in range(10):
        day = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=offset)
        path = tmp_path / "dataset" / "jobs" / "example" / day.date().isoformat()
        path.mkdir(parents=True)
        rows = np.zeros(16, dtype=SAMPLE_DTYPE)
        rows["values"] = 10
        rows["valid"] = 1
        rows["timeframe_us"] = 1_000_000
        rows["end_us"] = int(day.timestamp()*1e6) + 10*3600*1_000_000 + np.arange(64)*1_000_000
        file = path / "samples-0000.npy"
        np.save(file, rows, allow_pickle=False)
        receipt = dict(day=day.date().isoformat(), samples=len(rows), frames={"1000000": len(rows)},
                       files=[dict(name=file.name, bytes=file.stat().st_size, sha256=file_hash(file))])
        receipt["receipt_hash"] = digest(receipt)
        write_json(path / "receipt.json", receipt)
    manifest = build_split(tmp_path / "dataset", tmp_path / "split.json")
    stats, _ = calibrate(manifest, maximum=64)
    return manifest, stats


def test_chronological_split_and_manifest_seal(inventory, tmp_path):
    manifest, stats = inventory
    assert manifest["counts"] == {"train": 128, "validation": 32}
    assert manifest["train_fraction"] == .8
    assert max(item["day"] for item in manifest["splits"]["train"]) < min(item["day"] for item in manifest["splits"]["validation"])
    assert load_split(tmp_path / "split.json")["manifest_hash"] == manifest["manifest_hash"]
    manifest["counts"]["train"] += 1
    write_json(tmp_path / "bad.json", manifest)
    with pytest.raises(ValueError, match="modified"):
        load_split(tmp_path / "bad.json")


def test_split_purges_observed_window_with_gap_across_boundary(inventory, tmp_path):
    """A 64-bar context with an overnight gap cannot leak across the shared cutoff."""
    from research.bar_gpt.v5.shards.storage import BAR_DTYPE, Budget, publish
    directory = tmp_path / "dataset/jobs/extra/coarse"
    rows = np.zeros(3, dtype=SAMPLE_DTYPE)
    rows["values"] = 10
    rows["valid"] = 1
    rows["timeframe_us"] = 1_000_000
    jan8 = int(datetime(2024, 1, 8, 10, tzinfo=timezone.utc).timestamp()*1e6)
    jan9 = int(datetime(2024, 1, 9, 10, tzinfo=timezone.utc).timestamp()*1e6)
    rows["end_us"][0] = jan8 + np.arange(64)*1_000_000
    rows["end_us"][1] = np.r_[jan8+np.arange(32)*1_000_000, jan9+np.arange(32)*1_000_000]
    rows["end_us"][2] = jan9 + np.arange(64)*1_000_000
    publish(directory, rows, np.empty(0, dtype=BAR_DTYPE),
            dict(identity="gap-split", day="coarse"), Budget(10**7), 1024**2)
    result = build_split(tmp_path / "dataset", tmp_path / "split-with-gap.json")
    assert result["counts"] == {"train": 129, "validation": 33}
    assert result["purged_crossing"] == 1


def test_loader_resume_and_tail_no_drops(inventory):
    manifest, stats = inventory
    dataset = ShardBatches(manifest, "train", stats, 25, seed=4)
    assert len(dataset) == 6
    assert sum(len(dataset[i].visible) for i in range(len(dataset))) == 128
    resumed = ShardBatches(manifest, "train", stats, 25, seed=4, start_batch=2)
    torch.testing.assert_close(dataset[2].visible, resumed[0].visible)
    torch.testing.assert_close(dataset[2].hidden_ids, resumed[0].hidden_ids)


def test_zero_padding_cannot_put_validation_sample_in_training(inventory, tmp_path):
    """The last array slot is padding; split assignment must use the last real clock."""
    from research.bar_gpt.v5.shards.storage import BAR_DTYPE, Budget, publish

    rows = np.zeros(2, dtype=SAMPLE_DTYPE)
    rows["values"][:, :2], rows["valid"][:, :2] = 10., 1
    rows["timeframe_us"] = 1_000_000
    for i, day in enumerate((8, 9)):
        start = int(datetime(2024, 1, day, 10, tzinfo=timezone.utc).timestamp()*1e6)
        rows["end_us"][i, :2] = start + np.arange(2)*1_000_000
    publish(tmp_path / "dataset/jobs/padded/coarse", rows, np.empty(0, dtype=BAR_DTYPE),
            dict(identity="padded-split", day="coarse"), Budget(10**7), 1024**2)
    result = build_split(tmp_path / "dataset", tmp_path / "split-padded.json")
    assert result["counts"] == {"train": 129, "validation": 33}
    assert result["purged_crossing"] == 0


def test_payload_mutation_fails(inventory):
    manifest, stats = inventory
    dataset = ShardBatches(manifest, "validation", stats, 8)
    with open(dataset.entries[0]["path"], "r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"x")
    with pytest.raises(ValueError, match="payload"):
        dataset[0]


def test_eligible_stream_packs_full_batches_and_reports_exclusions(inventory, tmp_path):
    manifest, stats = inventory
    for item in manifest["splits"]["train"]:
        array = np.load(item["path"], allow_pickle=False)
        array["valid"][:8, :, [3, 9, 15]] = 0
        np.save(item["path"], array, allow_pickle=False)
        item["sha256"] = file_hash(item["path"])
    telemetry = tmp_path / "workers"
    telemetry.mkdir()
    dataset = EligibleStream(manifest, "train", stats, 25, telemetry=str(telemetry))
    batches = list(dataset)
    assert [len(batch.visible) for batch in batches] == [25, 25, 14]
    report = json.loads((telemetry / "worker-00.json").read_text())
    assert report["scanned_candidates"] == 128 and report["excluded_no_price"] == 64


def test_spawn_workers_and_cleanup(inventory):
    manifest, stats = inventory
    loader = make_loader(ShardBatches(manifest, "train", stats, 32), workers=2)
    try:
        batch = next(iter(loader))
        assert batch.visible.shape[0] == 32
        assert batch.visible.shape[1] in (7, 8)
        if torch.cuda.is_available():
            assert batch.visible.is_pinned()
    finally:
        close_loader(loader)
    assert all(not worker.is_alive() for worker in loader._iterator._workers)


def test_every_eligible_worker_tail_is_retained(inventory):
    manifest, stats = inventory
    loader = make_loader(EligibleStream(manifest, "train", stats, 25), workers=4)
    try:
        assert len(loader) == 8  # Four lanes each yield 25+7, rather than six global batches.
        assert sum(len(batch.visible) for batch in loader) == 128
    finally:
        close_loader(loader)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_actual_cuda_training_path(inventory):
    manifest, stats = inventory
    loader = make_loader(ShardBatches(manifest, "train", stats, 16), workers=0)
    model = small_model().cuda()
    optimizer = torch.optim.AdamW(model.parameters(), fused=True)
    try:
        result = run_steps(model, optimizer, loader, steps=3, warmup=1, precision="bf16")
    finally:
        close_loader(loader)
    assert result["samples"] == 48 and result["consumed_batches"] == 4
    assert result["samples_per_second"] > 0 and result["peak_gib"] > 0
    assert sum(result["target_counts"]) > 0


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_training_entrypoint_checkpoint_and_resume(inventory, tmp_path):
    """Exercise the actual run module, checkpoint publication, validation, and next-epoch resume."""
    from research.bar_gpt.v5.run_v1 import main
    common = ["--mode", "train", "--split-manifest", str(tmp_path / "split.json"),
              "--model", "small", "--batch-size", "16", "--workers", "0", "--train-steps", "2",
              "--validation-steps", "1", "--checkpoint-every", "1", "--wandb-mode", "disabled",
              "--source-revision", "synthetic-test"]
    assert main(common + ["--output", str(tmp_path / "run")]) == 0
    checkpoint = tmp_path / "run" / "checkpoint.pt"
    saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
    assert saved["epoch"] == 1 and saved["cursor"] == 0
    assert main(common + ["--output", str(tmp_path / "resumed"), "--resume", str(checkpoint), "--epochs", "2"]) == 0
    resumed = torch.load(tmp_path / "resumed" / "checkpoint.pt", map_location="cpu", weights_only=False)
    assert resumed["epoch"] == 2 and resumed["step"] > saved["step"]


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_trace_entrypoint(inventory, tmp_path):
    """Verify actual operator-table and Chrome-trace publication, not merely CLI parsing."""
    from research.bar_gpt.v5.run_v1 import main
    output = tmp_path / "trace-run"
    assert main(["--output", str(output), "--split-manifest", str(tmp_path / "split.json"),
                 "--models", "small", "--batches", "16", "--workers", "0", "--warmup", "4",
                 "--steps", "4", "--trace", "--wandb-mode", "disabled", "--source-revision", "synthetic-test"]) == 0
    assert (output / "small-b16-w0-bf16-trace.json").stat().st_size > 0
    assert "CUDA" in (output / "small-b16-w0-bf16-operators.txt").read_text()


def test_epoch_cosine_endpoints_and_resume():
    """Warmup and final-epoch LR must survive serialization exactly."""
    from research.bar_gpt.v5.campaign_v1 import EpochCosine
    schedule = EpochCosine(1e-3, 5e-5, 50, 1000)
    assert 5e-5 < schedule.value() < 1e-3
    schedule.updates = 999
    assert schedule.value() == pytest.approx(1e-3)
    schedule.updates, schedule.epoch = 1000, 49
    assert schedule.value() == pytest.approx(5e-5)
    restored = EpochCosine(1, 1, 1, 1)
    restored.load_state_dict(schedule.state_dict())
    assert restored.value() == schedule.value()


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_full_campaign_coverage_and_resume(inventory, tmp_path):
    """Two tiny full epochs exercise validation, best/last files, LR state and exact epoch resume."""
    from research.bar_gpt.v5.run_v1 import main
    common = ["--mode", "train", "--split-manifest", str(tmp_path / "split.json"),
              "--model", "small", "--batch-size", "16", "--workers", "0",
              "--train-steps", "0", "--validation-steps", "0", "--epochs", "2",
              "--scheduler", "cosine", "--learning-rate", ".001", "--warmup-updates", "2",
              "--metrics-every", "4", "--loss-every", "2", "--wandb-mode", "disabled",
              "--source-revision", "synthetic-test"]
    assert main(common + ["--output", str(tmp_path / "campaign")]) == 0
    original = torch.load(tmp_path / "campaign/checkpoint_last.pt", weights_only=False)
    assert original["epoch"] == 2 and original["step"] == 16
    assert original["scheduler"]["updates"] == 16
    assert original["history"][0]["training"]["samples"] == 128
    assert original["history"][0]["validation"]["samples"] == 32
    assert (tmp_path / "campaign/checkpoint_best.pt").is_file()
    assert main(common + ["--output", str(tmp_path / "campaign-resume"), "--resume",
                          str(tmp_path / "campaign/checkpoint_epoch_001.pt")]) == 0
    resumed = torch.load(tmp_path / "campaign-resume/checkpoint_last.pt", weights_only=False)
    assert resumed["scheduler"] == original["scheduler"]
    for name, tensor in original["model"].items():
        torch.testing.assert_close(tensor, resumed["model"][name], rtol=0, atol=0)


def test_flex_block_layout_matches_sdpa(monkeypatch):
    """Verify QKV/layout/gradient equivalence independently of optional compiler installation."""
    from research.bar_gpt.v5 import encoder_v1
    monkeypatch.setattr(encoder_v1, "compiled_flex", lambda: torch.nn.functional.scaled_dot_product_attention)
    sdpa = AttentionBlock(32, 4, 64, 0., "sdpa", 0.)
    flex = AttentionBlock(32, 4, 64, 0., "flex", 0.)
    flex.load_state_dict(sdpa.state_dict())
    x1 = torch.randn(2, 11, 32, requires_grad=True)
    x2 = x1.detach().clone().requires_grad_()
    y1, y2 = sdpa(x1), flex(x2)
    torch.testing.assert_close(y1, y2, atol=1e-6, rtol=1e-5)
    y1.square().mean().backward()
    y2.square().mean().backward()
    torch.testing.assert_close(x1.grad, x2.grad, atol=1e-6, rtol=1e-5)


@pytest.mark.parametrize("width", [60, 110])
def test_progress_compact_and_failure_render(width):
    stream = io.StringIO()
    console = Console(file=stream, width=width, force_terminal=False)
    console.print(render(dict(state="failed", stage="validation", done=2, total=4, error="example failure"), width))
    assert "FAILED" in stream.getvalue() and "example failure" in stream.getvalue()
