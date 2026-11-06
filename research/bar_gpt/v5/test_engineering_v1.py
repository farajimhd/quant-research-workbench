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
from research.bar_gpt.v5.shards.contract import SAMPLE_DTYPE, digest, file_hash, write_json
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
        rows["values"][..., 18:21] = 0
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


def test_loader_resume_and_tail_no_drops(inventory):
    manifest, stats = inventory
    dataset = ShardBatches(manifest, "train", stats, 25, seed=4)
    assert len(dataset) == 6
    assert sum(len(dataset[i].visible) for i in range(len(dataset))) == 128
    resumed = ShardBatches(manifest, "train", stats, 25, seed=4, start_batch=2)
    torch.testing.assert_close(dataset[2].visible, resumed[0].visible)
    torch.testing.assert_close(dataset[2].hidden_ids, resumed[0].hidden_ids)


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
        assert batch.visible.shape[1] in (8, 9)
        if torch.cuda.is_available():
            assert batch.visible.is_pinned()
    finally:
        close_loader(loader)
    assert all(not worker.is_alive() for worker in loader._iterator._workers)


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
