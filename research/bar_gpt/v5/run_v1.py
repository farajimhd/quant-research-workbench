"""Bounded workstation profiling and explicit resumable MAE training entry point."""

import argparse
from dataclasses import asdict, replace
import gc
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import time

import torch

from research.mlops.env import discover_env_files, load_env_files
from .data_v1 import ShardBatches, build_split, calibrate, close_loader, load_split, make_loader
from .encoder_v1 import EncoderConfig, MaskedAutoencoderV1
from .engine_v1 import run_steps
from .pretraining_v1 import ActivityStats
from .reporting_v1 import Reporter, open_panel, remove_panel_task
from .shards.contract import digest, write_json


CONFIGURATIONS = {
    "small": EncoderConfig(width=128, layers=2, heads=4, feedforward=512,
                           decoder_width=64, decoder_layers=1, decoder_heads=4),
    "base": EncoderConfig(),
    "large": EncoderConfig(width=512, layers=6, heads=8, feedforward=2048,
                           decoder_width=256, decoder_layers=2, decoder_heads=8),
}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("profile", "train"), default="profile")
    parser.add_argument("--dataset", type=Path, default=Path("D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--models", default="small,base,large")
    parser.add_argument("--batches", default="256,1024,4096")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--prefetch", type=int, default=2)
    parser.add_argument("--steps", type=int, default=24, help="Measured profiling steps per configuration.")
    parser.add_argument("--warmup", type=int, default=4)
    parser.add_argument("--seconds", type=int, default=30, help="Measured-seconds cap per profile; stops after current step.")
    parser.add_argument("--precision", choices=("bf16", "fp32"), default="bf16")
    parser.add_argument("--attention", choices=("sdpa", "flex"), default="sdpa")
    parser.add_argument("--attention-dropout", type=float)
    parser.add_argument("--model", choices=tuple(CONFIGURATIONS), default="base")
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--train-steps", type=int, default=1000, help="Explicit bounded training scope per epoch.")
    parser.add_argument("--validation-steps", type=int, default=8)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--wandb-mode", choices=("online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-project", default="bargpt-column-mae-v1")
    parser.add_argument("--panel", action="store_true")
    parser.add_argument("--refine", action="store_true", help="Also compare worker counts and FP32 for the fastest configuration.")
    args = parser.parse_args(argv)
    if min(args.steps, args.seconds, args.batch_size, args.epochs, args.train_steps, args.validation_steps) < 1 or args.warmup < 0:
        parser.error("Step, duration, epoch, and batch limits must be positive.")
    if not 0 <= args.workers <= 32 or not 1 <= args.prefetch <= 4:
        parser.error("workers must be 0..32; prefetch must be 1..4.")
    if args.resume and args.mode != "train":
        parser.error("Resume applies only to explicit training runs.")
    return args


def main(argv=None):
    args = arguments(argv)
    torch.set_num_threads(2)
    root = Path("D:/TradingML/runtimes").resolve()
    args.output = args.output.resolve()
    if not root.is_dir() or not args.output.is_relative_to(root):
        raise ValueError("Output must be beneath the available D:/TradingML/runtimes root.")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Use a fresh output directory; --resume reads a prior checkpoint without overwriting evidence.")
    if not torch.cuda.is_available():
        raise RuntimeError("This engineering runner requires CUDA; core tests support CPU separately.")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("Requested BF16 is not supported on this device.")
    args.output.mkdir(parents=True)
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(args.output / "inductor-cache")
    os.environ["TRITON_CACHE_DIR"] = str(args.output / "triton-cache")
    (args.output / "workers").mkdir()
    repository = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repository))
    revision = args.source_revision or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    config = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}
    config.update(source_revision=revision, host=socket.gethostname(), python=platform.python_version(),
                  torch=torch.__version__, gpu=torch.cuda.get_device_name(),
                  gpu_memory_gib=torch.cuda.get_device_properties(0).total_memory/2**30,
                  cpu_count=os.cpu_count(), mask_rate=0.6)
    write_json(args.output / "manifest.json", config)
    reporter = Reporter(args.output, config, args.wandb_mode, args.wandb_project)
    panel = None
    try:
        if args.panel:
            panel = open_panel(args.output)
        manifest = load_split(args.split_manifest) if args.split_manifest else build_split(
            args.dataset, args.output / "split.json", reporter.update,
        )
        if args.calibration:
            calibration = json.loads(args.calibration.read_text())
            if calibration["split_hash"] != manifest["manifest_hash"]:
                raise ValueError("Calibration belongs to a different split.")
            stats = ActivityStats(torch.tensor(calibration["median"]), torch.tensor(calibration["scale"]))
        else:
            reporter.update(stage="fitting training-only activity scales", done=0, total=64)
            stats, calibration = calibrate(manifest)
        write_json(args.output / "calibration.json", calibration)
        config.update(split_hash=manifest["manifest_hash"], split_counts=manifest["counts"],
                      cutoff_day=manifest["cutoff_day"], purged_crossing=manifest["purged_crossing"],
                      calibration_hash=digest(calibration))
        write_json(args.output / "manifest.json", config)
        if reporter.run:
            reporter.run.config.update(config, allow_val_change=True)
        result = profile(args, manifest, stats, reporter) if args.mode == "profile" else train(args, manifest, stats, reporter)
        write_json(args.output / "results.json", result)
        stopped = (args.output / "STOP").exists()
        reporter.finish("stopped" if stopped else "complete")
        return 0
    except BaseException as error:
        reporter.finish("stopped" if isinstance(error, KeyboardInterrupt) else "failed", str(error))
        raise
    finally:
        remove_panel_task(panel)


def loader_for(args, manifest, stats, split, batch, workers, epoch=0, cursor=0):
    """Create an explicit data-order epoch; caller owns worker lifetime and resume cursor."""
    dataset = ShardBatches(manifest, split, stats, batch, args.seed, epoch, cursor,
                           telemetry=str(args.output / "workers"))
    return make_loader(dataset, workers, args.prefetch)


def profile(args, manifest, stats, reporter):
    """Compare short real-data training windows; never treat these losses as model qualification."""
    names, batches = args.models.split(","), list(map(int, args.batches.split(",")))
    if any(name not in CONFIGURATIONS for name in names) or any(batch < 1 or batch > 32768 for batch in batches):
        raise ValueError("Unknown model or batch outside 1..32768.")
    plans = [(name, batch, args.workers, args.precision) for name in names for batch in batches]
    if len(plans) > 12:
        raise ValueError("Initial profiling grid is bounded to twelve configurations.")
    results = []
    index = 0
    while index < len(plans):
        if (args.output / "STOP").exists():
            break
        name, batch, workers, precision = plans[index]
        label = f"{name}-b{batch}-w{workers}-{precision}"
        reporter.update(stage="starting loader/model", trial=index+1, trials=len(plans), done=0,
                        total=args.warmup+args.steps,
                        config=dict(name=name, batch_size=batch, workers=workers, precision=precision, prefetch=args.prefetch))
        torch.manual_seed(args.seed)
        model = MaskedAutoencoderV1(replace(CONFIGURATIONS[name], attention=args.attention,
                                           attention_dropout=args.attention_dropout)).cuda()
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=.01, fused=True)
        loader = loader_for(args, manifest, stats, "train", batch, workers)
        entry = dict(label=label, model=name, batch_size=batch, workers=workers, precision=precision,
                     attention=args.attention, attention_dropout=model.config.attention_dropout,
                     parameters=sum(p.numel() for p in model.parameters()),
                     encoder_parameters=sum(p.numel() for p in model.encoder.parameters()))
        try:
            metrics = run_steps(model, optimizer, loader, steps=args.steps, warmup=args.warmup,
                                precision=precision, reporter=reporter, log_base=index*10000,
                                seconds_limit=args.seconds, stop_path=args.output / "STOP")
            entry.update(metrics, status="complete")
        except torch.cuda.OutOfMemoryError:
            entry.update(status="out_of_memory", peak_gib=torch.cuda.max_memory_allocated()/2**30)
        finally:
            close_loader(loader)
            del loader, optimizer, model
            gc.collect()
            torch.cuda.empty_cache()
        results.append(entry)
        write_json(args.output / "results.json", dict(kind="short-profiling-not-quality-evaluation", results=results))
        reporter.update(results=results)
        reporter.log({"profile/"+key: value for key, value in entry.items() if isinstance(value, (int, float))}, index*10000+9999)
        index += 1
        if index == len(names)*len(batches) and args.refine:
            successful = [result for result in results if result["status"] == "complete"]
            best = max(successful, key=lambda row: row["samples_per_second"])
            plans.extend((best["model"], best["batch_size"], count, best["precision"]) for count in (4, 16) if count != args.workers)
            plans.append((best["model"], best["batch_size"], best["workers"], "fp32"))
            # Repeat the best initial candidate to expose warm-cache/short-window variability.
            plans.append((best["model"], best["batch_size"], best["workers"], best["precision"]))
    return dict(kind="short-profiling-not-quality-evaluation", results=results,
                note="Warmup excluded; payload verification/startup reported separately; no accuracy ranking implied.")


def train(args, manifest, stats, reporter):
    """Explicit bounded epochs with atomic checkpoints and deterministic data/masking resume."""
    torch.manual_seed(args.seed)
    model = MaskedAutoencoderV1(replace(CONFIGURATIONS[args.model], attention=args.attention,
                                       attention_dropout=args.attention_dropout)).cuda()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=.01, fused=True)
    epoch, cursor, step = 0, 0, 0
    contract = dict(split_hash=manifest["manifest_hash"], model=asdict(model.config), batch=args.batch_size,
                    precision=args.precision, seed=args.seed, median=stats.median.tolist(), scale=stats.scale.tolist())
    if args.resume:
        checkpoint = torch.load(args.resume, map_location="cpu", weights_only=False)
        if checkpoint["contract"] != contract:
            raise ValueError("Resume data/model/scaling contract mismatch.")
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        epoch, cursor, step = checkpoint["epoch"], checkpoint["cursor"], checkpoint["step"]
        torch.set_rng_state(checkpoint["rng"])
        torch.cuda.set_rng_state_all(checkpoint["cuda_rng"])
    history = []
    while epoch < args.epochs and not (args.output / "STOP").exists():
        loader = loader_for(args, manifest, stats, "train", args.batch_size, args.workers, epoch, cursor)
        reporter.update(config=dict(name=args.model, batch_size=args.batch_size, workers=args.workers,
                                    precision=args.precision, prefetch=args.prefetch), stage="training", epoch=epoch)
        remaining = min(args.train_steps-cursor, len(loader))
        try:
            metrics = run_steps(model, optimizer, loader, steps=remaining, precision=args.precision,
                                reporter=reporter, log_base=step, seconds_limit=10**9, stop_path=args.output / "STOP")
        finally:
            close_loader(loader)
        cursor += metrics["consumed_batches"]
        step += metrics["consumed_batches"]
        completed = cursor >= min(args.train_steps, loader.dataset.total)
        if completed:
            epoch, cursor = epoch+1, 0
        checkpoint = dict(contract=contract, model=model.state_dict(), optimizer=optimizer.state_dict(),
                          epoch=epoch, cursor=cursor, step=step, rng=torch.get_rng_state(),
                          cuda_rng=torch.cuda.get_rng_state_all())
        temporary = args.output / "checkpoint.partial"
        torch.save(checkpoint, temporary)
        temporary.replace(args.output / "checkpoint.pt")
        validation = loader_for(args, manifest, stats, "validation", args.batch_size, args.workers)
        try:
            validation_metrics = run_steps(model, optimizer, validation, steps=args.validation_steps,
                                           precision=args.precision, training=False, reporter=reporter,
                                           log_base=step, seconds_limit=60)
        finally:
            close_loader(validation)
        step += validation_metrics["consumed_batches"]
        history.append(dict(epoch=epoch, training=metrics, validation=validation_metrics))
        write_json(args.output / "results.json", dict(history=history))
    return dict(history=history, checkpoint=str(args.output / "checkpoint.pt"))


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(main())
