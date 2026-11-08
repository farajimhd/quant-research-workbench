"""Bounded real-loader and resident-GPU probes using a disposable model copy.

No checkpoint is promoted, no scheduler is advanced, and no validation selection
is performed. All probes use the existing frozen split/calibration and model.
Run only while the campaign trainer is paused; this guard prevents GPU overlap.
"""

import argparse
from dataclasses import fields
import json
import os
from pathlib import Path
import pickle
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import psutil
import torch

from .data_v1 import EligibleStream, close_loader, load_split, make_loader
from .encoder_v1 import EncoderConfig, MaskedAutoencoderV1
from .engine_v1 import run_steps
from .loader_index_v1 import attach
from .pretraining_v1 import ActivityStats, PreparedBatch
from .reporting_v1 import Reporter
from .shards.contract import write_json, digest
from .trainer_v1 import reconstruction_loss


class Capture:
    """Capture one pinned [B,...] prepared batch while streaming real loader data."""

    def __init__(self, loader):
        self.loader, self.dataset, self.first = loader, loader.dataset, None

    def __iter__(self):
        for batch in self.loader:
            if self.first is None:
                self.first = batch
            yield batch


def resident(model, batch, inference, steps):
    """Measure forward/loss/metric kernels without CPU loader or H2D starvation."""
    model.eval()
    manager = torch.inference_mode() if inference else torch.no_grad()
    with manager, torch.autocast("cuda", dtype=torch.bfloat16):
        for _ in range(4):
            _, output = model(
                batch.visible,
                batch.valid,
                batch.feature_ids,
                batch.hidden_ids,
                batch.time,
                batch.time_valid,
            )
        torch.cuda.synchronize()
        started = time.perf_counter()
        for _ in range(steps):
            _, output = model(
                batch.visible,
                batch.valid,
                batch.feature_ids,
                batch.hidden_ids,
                batch.time,
                batch.time_valid,
            )
            reconstruction_loss(output, batch)
        torch.cuda.synchronize()
        seconds = time.perf_counter() - started
        return (
            dict(
                seconds=seconds,
                samples_per_second=len(batch.visible) * steps / seconds,
                step_ms=seconds * 1000 / steps,
            ),
            output.detach().clone(),
        )


def validation_delivery(model, manifest, stats, args):
    """Compare complete identical small target sets, not differing fast prefixes.

    This diagnostic metadata prefix holds about steps*4096 actual samples.
    Both delivery modes consume all worker tails. The original split is
    untouched and training-only calibration is reused without fitting.
    """
    rows, samples = [], 0
    for item in manifest["splits"]["validation"]:
        rows.append(item)
        samples += item["stop"] - item["start"]
        if samples >= args.steps * 4096:
            break
    subset = dict(manifest)
    subset.pop("loader_index", None)
    subset["splits"] = dict(train=rows, validation=rows)
    subset["manifest_hash"] = digest(
        [manifest["manifest_hash"], "delivery-probe", len(rows)]
    )
    attach(subset, args.output)
    results = []
    for ordered in (True, False):
        dataset = EligibleStream(subset, "validation", stats, 4096, seed=17)
        loader = make_loader(dataset, args.workers, 2, ordered=ordered)
        directory = args.output / ("ordered" if ordered else "ready-batch")
        directory.mkdir(exist_ok=True)
        reporter = Reporter(directory, {}, mode="disabled")
        try:
            result = run_steps(
                model,
                None,
                loader,
                steps=len(loader) + 1,
                training=False,
                reporter=reporter,
                seconds_limit=60,
                metrics_every=1000,
                loss_every=10,
            )
            if result["samples"] != samples:
                raise ValueError(
                    "Delivery probe did not consume its complete target set"
                )
            results.append(result)
        finally:
            close_loader(loader)
            reporter.finish()
    if results[0]["target_counts"] != results[1]["target_counts"]:
        raise ValueError("Ready delivery changed validation target masks")
    for name in ("feature_loss", "feature_mae", "feature_rmse"):
        torch.testing.assert_close(
            torch.tensor(results[0][name]),
            torch.tensor(results[1][name]),
            rtol=1e-6,
            atol=1e-8,
        )
    return dict(
        ordered=results[0],
        ready_batch=results[1],
        identical_full_target_set=True,
        metadata_entries=len(rows),
        samples=samples,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=48)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--delivery-only", action="store_true")
    args = parser.parse_args()
    if not 8 <= args.steps <= 128:
        raise ValueError("Performance probes are bounded to 8..128 updates")
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        command = " ".join(process.info["cmdline"] or [])
        if process.pid != os.getpid() and " -m research.bar_gpt.v5.run_v1 " in command:
            raise RuntimeError(f"Pause the campaign trainer first (PID {process.pid})")
    torch.set_num_threads(1)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = load_split(args.campaign / "split.json")
    calibration = json.loads((args.campaign / "calibration.json").read_text())
    stats = ActivityStats(
        torch.tensor(calibration["median"]), torch.tensor(calibration["scale"])
    )
    legacy = EligibleStream(manifest, "train", stats, 4096, seed=17, epoch=8)
    started = time.perf_counter()
    legacy_bytes = len(pickle.dumps(legacy))
    legacy_seconds = time.perf_counter() - started
    attach(manifest, args.campaign)
    compact = EligibleStream(manifest, "train", stats, 4096, seed=17, epoch=8)
    started = time.perf_counter()
    compact_bytes = len(pickle.dumps(compact))
    evidence = dict(
        worker_pickle=dict(
            legacy_bytes=legacy_bytes,
            compact_bytes=compact_bytes,
            legacy_seconds=legacy_seconds,
            compact_seconds=time.perf_counter() - started,
        ),
        split_hash=manifest["manifest_hash"],
    )
    saved = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    evidence["checkpoint"] = dict(
        epoch=saved["epoch"], phase=saved["phase"], step=saved["step"]
    )
    if saved["contract"]["split_hash"] != manifest["manifest_hash"]:
        raise ValueError("Performance checkpoint and split disagree")
    model = MaskedAutoencoderV1(EncoderConfig(**saved["contract"]["model"])).cuda()
    if args.delivery_only:
        model.load_state_dict(saved["model"])
        evidence["validation_delivery"] = validation_delivery(
            model, manifest, stats, args
        )
        write_json(args.output / "performance.json", evidence)
        print(json.dumps(evidence))
        return
    cached = None
    for split in ("train", "validation"):
        model.load_state_dict(saved["model"])
        optimizer = torch.optim.AdamW(model.parameters(), fused=True)
        optimizer.load_state_dict(saved["optimizer"])
        dataset = EligibleStream(
            manifest,
            split,
            stats,
            4096,
            seed=17,
            epoch=saved["epoch"] if split == "train" else 0,
        )
        loader = make_loader(dataset, args.workers, 2)
        capture = Capture(loader)
        output = args.output / split
        output.mkdir(exist_ok=True)
        reporter = Reporter(output, {}, mode="disabled")
        try:
            evidence[split] = run_steps(
                model,
                optimizer if split == "train" else None,
                capture,
                steps=args.steps,
                warmup=4,
                training=split == "train",
                reporter=reporter,
                metrics_every=1000,
                loss_every=10,
                seconds_limit=60,
            )
            if split == "validation":
                cached = PreparedBatch(
                    **{
                        field.name: getattr(capture.first, field.name).to("cuda")
                        for field in fields(capture.first)
                    }
                )
        finally:
            close_loader(loader)
            reporter.finish()
        write_json(args.output / "performance.json", evidence)
    model.load_state_dict(saved["model"])
    baseline, original = resident(model, cached, False, args.steps)
    optimized, result = resident(model, cached, True, args.steps)
    torch.testing.assert_close(original, result, rtol=1e-4, atol=1e-5)
    evidence["resident_validation"] = dict(
        no_grad=baseline, inference_mode=optimized, equivalent_predictions=True
    )
    evidence["validation_delivery"] = validation_delivery(model, manifest, stats, args)
    # A four-step operator trace confirms the actual attention backend. Its
    # overhead is excluded from every latency/throughput comparison above.
    with torch.profiler.profile(
        activities=[
            torch.profiler.ProfilerActivity.CPU,
            torch.profiler.ProfilerActivity.CUDA,
        ]
    ) as trace:
        resident(model, cached, True, 4)
    trace.export_chrome_trace(str(args.output / "validation-trace.json"))
    evidence["attention_operators"] = [
        event.key for event in trace.key_averages() if "attention" in event.key.lower()
    ]
    write_json(args.output / "performance.json", evidence)
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
