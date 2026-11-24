"""Bounded kernel qualification from a pinned checkpoint; never modifies its run."""

import os

os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")

import argparse
import copy
import json
import time
import traceback
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import torch

from .model import Autoencoder, Config
from .data import forward_batch
from .objectives import objective
from .metrics import Metrics
from .execution import Replay, backward
from ..phase1_v7.full_data import prepare_raw
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.phase1_contract import write_json, file_hash
from ..v5.data_v3 import load_split, close_loader
from ..v5.loader_index_v1 import attach
from ..phase1_v11.pipeline import loader, DeviceBatches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--chunk-bars", type=int, default=0)
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Profile real full-population loader and GPU transfers",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--matmul-precision", choices=("highest", "high"), default="highest")
    parser.add_argument(
        "--modes",
        default="sdpa-fp32-eager,math-fp32-eager,sdpa-fp32-graph,math-fp32-graph,sdpa-bf16-graph,math-bf16-graph,sdpa-fp16-graph",
    )
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(Path("D:/TradingML/runtimes").resolve()):
        raise ValueError("Runtime output required")
    if not 1 <= args.steps <= 300 or not 1 <= args.batch_size <= 8192:
        raise ValueError("Bounded profiling only")
    args.output.mkdir(exist_ok=False, parents=True)
    torch.set_num_threads(4)
    torch.cuda.set_per_process_memory_fraction(0.85)
    peek = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    saved = load_checkpoint(args.checkpoint, peek["contract"])
    del peek
    root = Path("D:/TradingML/runtimes/bar_gpt")
    rows = np.load(root / "phase1-v8-learned-mae-20261117/dataset/train.npy", mmap_mode="r")
    batch = prepare_raw(rows[: args.batch_size], native=True).to("cuda")
    parameters = dict(
        mean=saved["model"]["mean"], scale=saved["model"]["scale"], **saved["model_extra"]
    )
    initial = {**saved["model"], "_extra_state": saved["model_extra"]}

    def create():
        config = Config(**saved["contract"]["config"])
        if args.chunk_bars:
            config = replace(config, chunk_bars=args.chunk_bars)
        model = Autoencoder(parameters, config).cuda()
        model.load_state_dict(initial)
        return model

    model = create()
    reference_loss, _, reference_logs, _ = backward(model, batch)
    reference_gradients = [p.grad.detach().clone() for p in model.parameters()]
    reference_logs = reference_logs.clone()
    reference_loss = float(reference_loss)
    del model
    results = []
    for mode in args.modes.split(","):
        attention, precision, graph = mode.split("-")
        model = create()
        model.configure_execution(attention, precision, args.summary_only)
        torch.set_float32_matmul_precision(args.matmul_precision)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0.0, fused=True)
        optimizer.load_state_dict(copy.deepcopy(saved["optimizer"]))
        replay = source = device = None
        startup = 0.0
        try:
            if args.evaluate:
                development = np.load(
                    root / "phase1-v8-learned-mae-20261117/dataset/development.npy", mmap_mode="r"
                )
                metric = Metrics("cuda")
                with torch.no_grad():
                    for start in range(0, len(development), 256):
                        dev_batch = prepare_raw(development[start : start + 256], native=True).to(
                            "cuda"
                        )
                        out, values, valid = forward_batch(model, dev_batch)
                        metric.add(model, out, values, valid, dev_batch)
                write_json(args.output / (mode + "-development.json"), metric.result())
                del out, values, valid, dev_batch, metric
            measured_loss, norm, logs, _ = backward(model, batch)
            difference = sum(
                (p.grad.double() - g.double()).square().sum()
                for p, g in zip(model.parameters(), reference_gradients)
            )
            denominator = sum(g.double().square().sum() for g in reference_gradients)
            drift = dict(
                loss_relative=abs(float(measured_loss) - reference_loss)
                / max(abs(reference_loss), 1e-12),
                gradient_relative_l2=float((difference / denominator.clamp_min(1e-30)).sqrt()),
                max_log_difference=float((logs - reference_logs).abs().max()),
            )
            if not bool(torch.isfinite(measured_loss) & torch.isfinite(norm)):
                raise FloatingPointError("Nonfinite matched forward/gradient")
            if graph == "graph":
                gradients = [p.grad.detach().clone() for p in model.parameters()]
                replay = Replay(model, batch)
                captured = replay()
                torch.testing.assert_close(captured[0], measured_loss, rtol=1e-4, atol=1e-4)
                for p, expected in zip(model.parameters(), gradients):
                    torch.testing.assert_close(p.grad, expected, rtol=3e-3, atol=2e-5)
            torch.cuda.reset_peak_memory_stats()
            if args.stream:
                manifest = load_split(root / "phase1-v6-cohort-balanced-v4/split.json")
                manifest = attach(manifest, Path(manifest["dataset"]))
                manifest["splits"] = {}
                stream_args = SimpleNamespace(
                    output=args.output / mode,
                    batch_size=args.batch_size,
                    workers=args.workers,
                    prefetch=4,
                    seed=17,
                    preparation="raw_gpu",
                    shard_prefetch=16,
                    shard_prefetch_mib=64,
                )
                started = time.perf_counter()
                source = loader(manifest, stream_args, "train", 0)
                device = DeviceBatches(iter(source), "cuda")
                startup = time.perf_counter() - started
            for step in range(args.steps + 10):
                if step == 10:
                    torch.cuda.synchronize()
                    began = time.perf_counter()
                current = device.pop() if device is not None else batch
                if replay is None:
                    model.zero_grad(set_to_none=True)
                    loss, norm, _, _ = backward(model, current)
                else:
                    loss, norm, _, _ = replay(current)
                if not bool(torch.isfinite(loss) & torch.isfinite(norm)):
                    raise FloatingPointError("Nonfinite benchmark update")
                optimizer.step()
                if device is not None:
                    device.advance()
            torch.cuda.synchronize()
            seconds = time.perf_counter() - began
            row = dict(
                mode=mode,
                seconds=seconds,
                windows_per_second=args.steps * args.batch_size / seconds,
                peak_mib=torch.cuda.max_memory_allocated() / 2**20,
                reserved_mib=torch.cuda.max_memory_reserved() / 2**20,
                loader_startup_seconds=startup,
                loader_wait_seconds=device.wait_seconds if device is not None else None,
                matched_drift=drift,
                status="profiled_not_quality_approved",
            )
        except Exception as error:
            row = dict(mode=mode, status="rejected", error=repr(error))
            (args.output / (mode + "-error.txt")).write_text(traceback.format_exc())
        finally:
            if device is not None:
                device.close()
            if source is not None:
                close_loader(source)
        results.append(row)
        write_json(
            args.output / "profiles.json",
            dict(
                source=args.source_revision,
                checkpoint_sha=file_hash(args.checkpoint),
                batch_size=args.batch_size,
                steps=args.steps,
                chunk_bars=args.chunk_bars or saved["contract"]["config"]["chunk_bars"],
                summary_only=args.summary_only,
                matmul_precision=args.matmul_precision,
                results=results,
                scope=(
                    "full-data loader plus GPU updates"
                    if args.stream
                    else "cached real-data GPU updates"
                ),
            ),
        )
        print(json.dumps(row), flush=True)
        del model, optimizer, replay
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
