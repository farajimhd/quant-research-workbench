from __future__ import annotations

import argparse
import json
import os
import queue
import random
import sys
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "research" / "masked_event_model" / "v1").exists()
    ),
    Path(__file__).resolve().parents[3],
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import polars as pl
import torch
from torch.utils.data import DataLoader

from research.masked_event_model.v1.config import DataConfig, ExperimentConfig, MaskConfig, ModelConfig, ProbeConfig, TrainConfig
from research.masked_event_model.v1.data import EventChunkDataset, discover_chunk_files, target_horizons_from_columns
from research.masked_event_model.v1.losses import masked_autoencoder_loss
from research.masked_event_model.v1.masking import build_structured_masks
from research.masked_event_model.v1.model import MaskedEventAutoencoder
from research.masked_event_model.v1.model_artifacts import save_model_architecture_artifacts
from research.masked_event_model.v1.probe import run_linear_probe
from research.masked_event_model.v1.schema import CHUNK_SUMMARY_COLUMNS, QUOTE_FEATURE_COLUMNS, TRADE_FEATURE_COLUMNS


EXPERIMENT_VERSION = "mem-v1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    data_defaults = DataConfig()
    train_defaults = TrainConfig()
    mask_defaults = MaskConfig()
    model_defaults = ModelConfig()
    probe_defaults = ProbeConfig()
    parser = argparse.ArgumentParser(description="Train masked event autoencoder v1.")
    parser.add_argument("--cache-root", default=str(data_defaults.cache_root))
    parser.add_argument("--canonical-root", default=str(data_defaults.canonical_root))
    parser.add_argument("--output-root", default=str(train_defaults.output_root))
    parser.add_argument("--train-start-date", default=data_defaults.train_start_date)
    parser.add_argument("--train-end-date", default=data_defaults.train_end_date)
    parser.add_argument("--validation-start-date", default=data_defaults.validation_start_date)
    parser.add_argument("--validation-end-date", default=data_defaults.validation_end_date)
    parser.add_argument("--test-start-date", default=data_defaults.test_start_date)
    parser.add_argument("--test-end-date", default=data_defaults.test_end_date)
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--context-seconds", type=int, default=data_defaults.context_seconds)
    parser.add_argument("--chunk-ms", type=int, default=data_defaults.chunk_ms)
    parser.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    parser.add_argument("--epochs", type=int, default=train_defaults.epochs)
    parser.add_argument("--max-steps", type=int, default=train_defaults.max_steps)
    parser.add_argument("--num-workers", type=int, default=train_defaults.num_workers)
    parser.add_argument("--prefetch-factor", type=int, default=train_defaults.prefetch_factor)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, default=train_defaults.seed)
    parser.add_argument("--learning-rate", type=float, default=train_defaults.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train_defaults.weight_decay)
    parser.add_argument("--grad-clip-norm", type=float, default=train_defaults.grad_clip_norm)
    parser.add_argument("--logging-steps", type=int, default=train_defaults.logging_steps)
    parser.add_argument("--checkpoint-steps", type=int, default=train_defaults.checkpoint_steps)
    parser.add_argument("--mask-ratio", type=float, default=mask_defaults.mask_ratio)
    parser.add_argument("--d-model", type=int, default=model_defaults.d_model)
    parser.add_argument("--n-heads", type=int, default=model_defaults.n_heads)
    parser.add_argument("--quote-event-layers", type=int, default=model_defaults.quote_event_layers)
    parser.add_argument("--trade-event-layers", type=int, default=model_defaults.trade_event_layers)
    parser.add_argument("--temporal-layers", type=int, default=model_defaults.temporal_layers)
    parser.add_argument("--decoder-layers", type=int, default=model_defaults.decoder_layers)
    parser.add_argument("--ffn-mult", type=int, default=model_defaults.ffn_mult)
    parser.add_argument("--dropout", type=float, default=model_defaults.dropout)
    parser.add_argument("--probe-every-steps", type=int, default=probe_defaults.every_steps)
    parser.add_argument("--probe-train-steps", type=int, default=probe_defaults.train_steps)
    parser.add_argument("--probe-train-windows", type=int, default=probe_defaults.train_windows)
    parser.add_argument("--probe-val-windows", type=int, default=probe_defaults.val_windows)
    parser.add_argument("--disable-probe", action="store_true")
    parser.add_argument("--wandb-project", default=train_defaults.wandb_project)
    parser.add_argument("--wandb-entity", default=train_defaults.wandb_entity)
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="auto")
    parser.add_argument("--wandb-init-timeout", type=int, default=60)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--resume-latest", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_dotenv_files(discover_dotenv_paths())
    set_seed(args.seed)
    config = build_config(args)
    output_dir = resolve_output_dir(config, args)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.jsonl"
    print(f"Output directory: {output_dir}", flush=True)
    print("Inferring available target horizons from chunk cache...", flush=True)
    horizon_count = infer_horizon_count(config.data)
    print(f"Horizons available: {horizon_count}", flush=True)
    run = init_wandb(args, config, output_dir, horizon_count)
    write_config(output_dir, config, args, horizon_count)
    print("Building masked event model...", flush=True)
    model = MaskedEventAutoencoder(
        quote_feature_count=len(QUOTE_FEATURE_COLUMNS),
        trade_feature_count=len(TRADE_FEATURE_COLUMNS),
        summary_feature_count=len(CHUNK_SUMMARY_COLUMNS),
        context_chunks=config.data.context_chunks,
        max_quote_events=config.data.max_quote_events,
        max_trade_events=config.data.max_trade_events,
        max_total_events=config.data.max_total_events,
        horizon_count=horizon_count,
        target_bit_count=config.data.target_bit_count,
        config=config.model,
    )
    device = torch.device(args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu")
    model.to(device)
    print(f"Model moved to device={device}", flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay)
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and device.type == "cuda")
    global_step = maybe_resume(model, optimizer, output_dir, fresh_start=args.fresh_start)
    try:
        architecture_info = save_model_architecture_artifacts(
            model=model,
            data_config=config.data,
            output_dir=output_dir,
            version=EXPERIMENT_VERSION,
            torch_module=torch,
            wandb_run=run,
            summary_batch_size=1,
            summary_depth=8,
            graph_depth=3,
        )
        print(f"Model architecture artifacts: {architecture_info.get('architecture_dir')}", flush=True)
    except Exception as exc:
        print(f"Model architecture artifact generation skipped: {exc}", flush=True)

    print(f"Inputs: quote={config.data.max_quote_events}x{len(QUOTE_FEATURE_COLUMNS)} trade={config.data.max_trade_events}x{len(TRADE_FEATURE_COLUMNS)} summary={len(CHUNK_SUMMARY_COLUMNS)}", flush=True)
    print(f"Model: d={config.model.d_model} heads={config.model.n_heads} qlayers={config.model.quote_event_layers} tlayers={config.model.temporal_layers} decoder={config.model.decoder_layers}", flush=True)
    print(f"Training configuration ready; mask_ratio={config.masks.mask_ratio}", flush=True)
    if args.dry_run:
        batch = next(iter(make_loader(config.data, "train", config.train.batch_size, config.train.num_workers, config.train.prefetch_factor, args.seed)))
        batch = move_batch(batch, device)
        masks = build_structured_masks(quote_values=batch["quote_values"], trade_values=batch["trade_values"], chunk_summary=batch["chunk_summary"], event_kinds=batch["event_kinds"], config=config.masks)
        with torch.no_grad():
            output = model(batch["quote_values"], batch["trade_values"], batch["event_kinds"], batch["event_indices"], batch["chunk_summary"], masks)
            loss, metrics = masked_autoencoder_loss(output, batch, masks, config.losses)
        print(f"Dry run loss={float(loss):.6f} batch={batch['quote_values'].shape}", flush=True)
        print(json.dumps(metrics, indent=2), flush=True)
        return

    model.train()
    for epoch in range(config.train.epochs):
        loader = make_loader(config.data, "train", config.train.batch_size, config.train.num_workers, config.train.prefetch_factor, args.seed + epoch)
        for batch in loader:
            global_step += 1
            batch = move_batch(batch, device)
            masks = build_structured_masks(quote_values=batch["quote_values"], trade_values=batch["trade_values"], chunk_summary=batch["chunk_summary"], event_kinds=batch["event_kinds"], config=config.masks)
            with torch.amp.autocast("cuda", enabled=config.train.amp and device.type == "cuda"):
                output = model(batch["quote_values"], batch["trade_values"], batch["event_kinds"], batch["event_indices"], batch["chunk_summary"], masks)
                loss, metrics = masked_autoencoder_loss(output, batch, masks, config.losses)
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
            scaler.step(optimizer)
            scaler.update()
            if global_step % config.train.logging_steps == 0:
                metrics.update({"train/epoch": float(epoch + 1), "train/step": float(global_step), "train/lr": float(optimizer.param_groups[0]["lr"])})
                log_metrics(metrics, metrics_path, run, global_step)
                print(format_metrics(global_step, epoch + 1, metrics), flush=True)
            if config.probe.enabled and global_step % config.probe.every_steps == 0:
                probe_metrics = run_linear_probe(
                    encoder=model,
                    data_config=config.data,
                    probe_config=config.probe,
                    device=device,
                    num_workers=max(0, min(2, config.train.num_workers)),
                    seed=args.seed + global_step,
                )
                log_metrics(probe_metrics, metrics_path, run, global_step)
                print("PROBE " + json.dumps(probe_metrics, sort_keys=True), flush=True)
            if global_step % config.train.checkpoint_steps == 0:
                save_checkpoint(output_dir, model, optimizer, global_step, config, args)
            if config.train.max_steps > 0 and global_step >= config.train.max_steps:
                save_checkpoint(output_dir, model, optimizer, global_step, config, args)
                return
    save_checkpoint(output_dir, model, optimizer, global_step, config, args)


def build_config(args: argparse.Namespace) -> ExperimentConfig:
    return ExperimentConfig(
        data=DataConfig(
            cache_root=Path(args.cache_root),
            canonical_root=Path(args.canonical_root),
            train_start_date=args.train_start_date,
            train_end_date=args.train_end_date,
            validation_start_date=args.validation_start_date,
            validation_end_date=args.validation_end_date,
            test_start_date=args.test_start_date,
            test_end_date=args.test_end_date,
            tickers=tuple(part.strip().upper() for part in args.tickers.split(",") if part.strip()) or ("ALL",),
            chunk_ms=args.chunk_ms,
            context_seconds=args.context_seconds,
        ),
        masks=MaskConfig(mask_ratio=args.mask_ratio),
        model=ModelConfig(
            d_model=args.d_model,
            n_heads=args.n_heads,
            quote_event_layers=args.quote_event_layers,
            trade_event_layers=args.trade_event_layers,
            temporal_layers=args.temporal_layers,
            decoder_layers=args.decoder_layers,
            ffn_mult=args.ffn_mult,
            dropout=args.dropout,
        ),
        probe=ProbeConfig(
            enabled=not args.disable_probe,
            every_steps=args.probe_every_steps,
            train_steps=args.probe_train_steps,
            train_windows=args.probe_train_windows,
            val_windows=args.probe_val_windows,
        ),
        train=TrainConfig(
            output_root=Path(args.output_root),
            batch_size=args.batch_size,
            epochs=args.epochs,
            max_steps=args.max_steps,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
            grad_clip_norm=args.grad_clip_norm,
            logging_steps=args.logging_steps,
            checkpoint_steps=args.checkpoint_steps,
            num_workers=args.num_workers,
            prefetch_factor=args.prefetch_factor,
            seed=args.seed,
            wandb_project=args.wandb_project,
            wandb_entity=args.wandb_entity,
            wandb_run_name=args.wandb_run_name or default_run_name(args),
        ),
    )


def make_loader(data_config: DataConfig, split: str, batch_size: int, num_workers: int, prefetch_factor: int, seed: int) -> DataLoader:
    dataset = EventChunkDataset(config=data_config, split=split, batch_size=batch_size, seed=seed)
    kwargs: dict[str, Any] = {"batch_size": None, "num_workers": max(0, num_workers), "pin_memory": True}
    if num_workers > 0:
        kwargs.update({"prefetch_factor": max(1, prefetch_factor), "persistent_workers": True})
    return DataLoader(dataset, **kwargs)


def infer_horizon_count(config: DataConfig) -> int:
    files = discover_chunk_files(config, start_date=config.train_start_date, end_date=config.validation_end_date)
    if not files:
        raise FileNotFoundError(f"No chunk files found under {config.cache_root}. Build phase 3 chunks first.")
    schema = pl.scan_parquet(str(files[0].path)).collect_schema()
    horizons = target_horizons_from_columns(schema.names())
    if not horizons:
        raise ValueError(f"No target_mid_h* columns found in {files[0].path}.")
    return len(horizons)


def move_batch(batch: dict[str, Any], device: torch.device) -> dict[str, Any]:
    moved = dict(batch)
    for key in ("quote_values", "trade_values", "event_kinds", "event_indices", "chunk_summary", "targets", "target_bps"):
        moved[key] = batch[key].to(device, non_blocking=True)
    return moved


def save_checkpoint(output_dir: Path, model: torch.nn.Module, optimizer: torch.optim.Optimizer, step: int, config: ExperimentConfig, args: argparse.Namespace) -> None:
    payload = {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "step": step, "config": dataclass_tree(config), "args": vars(args)}
    path = output_dir / "checkpoint_last.pt"
    torch.save(payload, path)
    print(f"Saved checkpoint: {path}", flush=True)


def maybe_resume(model: torch.nn.Module, optimizer: torch.optim.Optimizer, output_dir: Path, *, fresh_start: bool) -> int:
    path = output_dir / "checkpoint_last.pt"
    if fresh_start or not path.exists():
        return 0
    checkpoint = torch.load(path, map_location="cpu")
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    print(f"Resumed checkpoint: {path} step={checkpoint.get('step', 0)}", flush=True)
    return int(checkpoint.get("step", 0))


def resolve_output_dir(config: ExperimentConfig, args: argparse.Namespace) -> Path:
    return config.train.output_root / config.train.wandb_run_name


def default_run_name(args: argparse.Namespace) -> str:
    return f"mem-v1-d{args.d_model}-e{args.quote_event_layers}-t{args.temporal_layers}-d{args.decoder_layers}-mask{int(args.mask_ratio * 100)}-chunk{args.chunk_ms}-nov2025"


def init_wandb(args: argparse.Namespace, config: ExperimentConfig, output_dir: Path, horizon_count: int) -> Any | None:
    if not args.wandb_project or args.wandb_project.lower() in {"off", "none", "disabled"}:
        print("*** WANDB project disabled; writing metrics.jsonl only.", flush=True)
        return None
    mode = resolve_wandb_mode(args)
    if mode == "disabled":
        print("*** WANDB explicitly disabled; writing metrics.jsonl only.", flush=True)
        return None
    os.environ.setdefault("WANDB_INIT_TIMEOUT", str(args.wandb_init_timeout))
    os.environ.setdefault("WANDB_LOGIN_TIMEOUT", str(min(args.wandb_init_timeout, 30)))
    if mode == "offline":
        os.environ["WANDB_MODE"] = "offline"
    elif mode == "online":
        os.environ["WANDB_MODE"] = "online"
    print(
        "*** WANDB INIT | "
        f"entity={args.wandb_entity or '<none>'} "
        f"project={args.wandb_project} "
        f"run={config.train.wandb_run_name} "
        f"mode={mode} "
        f"api_key_present={bool(os.environ.get('WANDB_API_KEY'))} "
        f"timeout_seconds={args.wandb_init_timeout}",
        flush=True,
    )
    if not os.environ.get("WANDB_API_KEY") and mode == "online":
        raise RuntimeError("WANDB_API_KEY is required for --wandb-mode online.")
    if not os.environ.get("WANDB_API_KEY") and mode == "offline":
        print(
            "*** WANDB_API_KEY was not found after .env discovery; using WANDB_MODE=offline. "
            "Set WANDB_API_KEY in an environment variable or a discovered .env file for online logging.",
            flush=True,
        )
    try:
        import wandb
    except ModuleNotFoundError:
        raise RuntimeError("wandb is not installed, but W&B logging is enabled.") from None
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def init_worker() -> None:
        try:
            api_key = os.environ.get("WANDB_API_KEY")
            if api_key and mode == "online":
                wandb.login(key=api_key, relogin=False)
            settings = wandb.Settings(
                init_timeout=max(1, int(args.wandb_init_timeout)),
                login_timeout=max(1, min(int(args.wandb_init_timeout), 30)),
            )
            run = wandb.init(
                entity=args.wandb_entity or None,
                project=args.wandb_project,
                name=config.train.wandb_run_name,
                config={"horizon_count": horizon_count, **dataclass_tree(config)},
                dir=str(output_dir),
                resume="allow",
                mode=mode,
                settings=settings,
            )
            result_queue.put(("ok", run))
        except Exception:
            result_queue.put(("error", traceback.format_exc()))

    thread = threading.Thread(target=init_worker, name="wandb-init", daemon=True)
    thread.start()
    thread.join(timeout=max(1, int(args.wandb_init_timeout)))
    if thread.is_alive():
        raise TimeoutError(
            "W&B init timed out before returning. The backend did not start cleanly. "
            "This usually indicates a W&B service/startup issue, not a model/data issue. "
            "Try a larger --wandb-init-timeout only if the workstation network is slow."
        )
    if result_queue.empty():
        raise RuntimeError("W&B init thread returned no result.")
    status, payload = result_queue.get()
    if status == "ok":
        print(f"*** WANDB READY | mode={mode} dir={getattr(payload, 'dir', '<unknown>')}", flush=True)
        return payload
    raise RuntimeError(f"W&B init failed:\n{payload}")


def resolve_wandb_mode(args: argparse.Namespace) -> str:
    if args.wandb_mode != "auto":
        return args.wandb_mode
    env_mode = os.environ.get("WANDB_MODE", "").strip().lower()
    if env_mode in {"online", "offline", "disabled"}:
        return env_mode
    return "online" if os.environ.get("WANDB_API_KEY") else "offline"


def log_metrics(metrics: dict[str, float], metrics_path: Path, run: Any | None, step: int) -> None:
    row = {"step": step, "ts": datetime.now().isoformat(timespec="seconds"), **metrics}
    with metrics_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    if run is not None:
        run.log(metrics, step=step)


def format_metrics(step: int, epoch: int, metrics: dict[str, float]) -> str:
    keys = ["pretrain/loss_total", "pretrain/loss_quote", "pretrain/loss_trade", "pretrain/loss_summary", "pretrain/event_kind_acc_pct", "mask/ratio_actual"]
    parts = " ".join(f"{key}={metrics[key]:.4f}" for key in keys if key in metrics)
    return f"step={step:,} epoch={epoch} {parts}"


def write_config(output_dir: Path, config: ExperimentConfig, args: argparse.Namespace, horizon_count: int) -> None:
    payload = {"version": EXPERIMENT_VERSION, "horizon_count": horizon_count, "args": vars(args), "config": dataclass_tree(config)}
    (output_dir / "config.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def dataclass_tree(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {field: dataclass_tree(getattr(value, field)) for field in value.__dataclass_fields__}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def discover_dotenv_paths() -> list[Path]:
    paths: list[Path] = []
    for raw in os.environ.get("DOTENV_PATHS", "").split(os.pathsep):
        if raw.strip():
            paths.append(Path(raw.strip()))
    for base in [Path.cwd(), REPO_ROOT, *REPO_ROOT.parents]:
        paths.append(base / ".env")
    paths.append(Path("D:/TradingCodes/quant-research-workbench/.env"))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def load_dotenv_files(paths: list[Path]) -> None:
    loaded: list[str] = []
    for path in paths:
        if path.exists():
            load_dotenv(path)
            loaded.append(str(path))
    if loaded:
        print(f"Loaded .env files: {'; '.join(loaded)}", flush=True)
    else:
        print("No .env file found in discovered locations.", flush=True)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("*** FATAL masked_event_model.v1.train error. Full traceback:", flush=True)
        traceback.print_exc()
        raise
