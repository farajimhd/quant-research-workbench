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
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "research" / "masked_event_model" / "v2").exists()
    ),
    Path(__file__).resolve().parents[3],
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import polars as pl
import torch

from research.masked_event_model.v2.config import DataConfig, ModelConfig, ProbeConfig, TrainConfig
from research.masked_event_model.v2.data import discover_chunk_files, target_horizons_from_columns
from research.masked_event_model.v2.model import MaskedEventAutoencoder
from research.masked_event_model.v2.probe import run_linear_probe
from research.masked_event_model.v2.schema import CHUNK_SUMMARY_COLUMNS, QUOTE_FEATURE_COLUMNS, TRADE_FEATURE_COLUMNS


EXPERIMENT_VERSION = "mem-v2-linear-probe"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    data_defaults = DataConfig()
    train_defaults = TrainConfig()
    probe_defaults = ProbeConfig()
    parser = argparse.ArgumentParser(description="Train a linear probe on a frozen masked event v2 checkpoint.")
    parser.add_argument("--checkpoint-path", default="")
    parser.add_argument("--output-root", default=str(train_defaults.output_root))
    parser.add_argument("--pretrain-run-name", default=train_defaults.wandb_run_name)
    parser.add_argument("--cache-root", default="")
    parser.add_argument("--canonical-root", default="")
    parser.add_argument("--train-start-date", default="")
    parser.add_argument("--train-end-date", default="")
    parser.add_argument("--validation-start-date", default="")
    parser.add_argument("--validation-end-date", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--train-steps", type=int, default=probe_defaults.train_steps)
    parser.add_argument("--train-windows", type=int, default=probe_defaults.train_windows)
    parser.add_argument("--val-windows", type=int, default=probe_defaults.val_windows)
    parser.add_argument("--hidden-dim", type=int, default=probe_defaults.hidden_dim)
    parser.add_argument("--learning-rate", type=float, default=probe_defaults.learning_rate)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--wandb-project", default=train_defaults.wandb_project)
    parser.add_argument("--wandb-entity", default=train_defaults.wandb_entity)
    parser.add_argument("--wandb-run-name", default="")
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--wandb-init-timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_dotenv_files(discover_dotenv_paths())
    set_seed(args.seed)
    checkpoint_path = resolve_checkpoint_path(args)
    checkpoint_dir = checkpoint_path.parent
    metrics_path = checkpoint_dir / "linear_probe_metrics.jsonl"
    config_json = read_checkpoint_config_json(checkpoint_dir)
    print(f"Linear probe checkpoint: {checkpoint_path}", flush=True)
    print(f"Linear probe metrics: {metrics_path}", flush=True)
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    checkpoint_step = int(checkpoint.get("step", 0))
    checkpoint_config = checkpoint.get("config", {})
    raw_config = config_json.get("config") if config_json else checkpoint_config
    data_config = data_config_from_raw(raw_config.get("data", {}), args)
    model_config = model_config_from_raw(raw_config.get("model", {}))
    horizon_count = int(config_json.get("horizon_count", 0)) if config_json else 0
    if horizon_count <= 0:
        horizon_count = infer_horizon_count(data_config)
    probe_config = ProbeConfig(
        enabled=True,
        train_steps=args.train_steps,
        train_windows=args.train_windows,
        val_windows=args.val_windows,
        batch_size=args.batch_size,
        hidden_dim=args.hidden_dim,
        learning_rate=args.learning_rate,
    )
    run_name = args.wandb_run_name or default_probe_run_name(checkpoint_dir.name, checkpoint_step, probe_config)
    run = init_wandb(args, run_name, checkpoint_path, checkpoint_step, data_config, model_config, probe_config, horizon_count)
    write_probe_config(checkpoint_dir, args, checkpoint_path, checkpoint_step, data_config, model_config, probe_config, horizon_count)
    print(
        "Linear probe config "
        f"train_windows={probe_config.train_windows:,} val_windows={probe_config.val_windows:,} "
        f"batch={probe_config.batch_size:,} train_steps={probe_config.train_steps:,} "
        f"device={args.device}",
        flush=True,
    )
    if args.dry_run:
        print("Dry run complete; checkpoint/config resolved but probe not trained.", flush=True)
        return
    device = torch.device(args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu")
    model = MaskedEventAutoencoder(
        quote_feature_count=len(QUOTE_FEATURE_COLUMNS),
        trade_feature_count=len(TRADE_FEATURE_COLUMNS),
        summary_feature_count=len(CHUNK_SUMMARY_COLUMNS),
        context_chunks=data_config.context_chunks,
        max_quote_events=data_config.max_quote_events,
        max_trade_events=data_config.max_trade_events,
        max_total_events=data_config.max_total_events,
        horizon_count=horizon_count,
        target_bit_count=data_config.target_bit_count,
        config=model_config,
    )
    model.load_state_dict(checkpoint["model"])
    model.to(device)
    model.eval()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    started = time.perf_counter()
    metrics = run_linear_probe(
        encoder=model,
        data_config=data_config,
        probe_config=probe_config,
        device=device,
        num_workers=args.num_workers,
        seed=args.seed + checkpoint_step,
        log_progress=True,
    )
    metrics.update(
        {
            "probe/checkpoint_step": float(checkpoint_step),
            "probe/elapsed_seconds": time.perf_counter() - started,
            "probe/model_d_model": float(model_config.d_model),
            "probe/horizon_count": float(horizon_count),
        }
    )
    log_metrics(metrics, metrics_path, run, checkpoint_step)
    print("LINEAR PROBE METRICS " + json.dumps(metrics, sort_keys=True), flush=True)
    if run is not None:
        run.finish()


def resolve_checkpoint_path(args: argparse.Namespace) -> Path:
    if args.checkpoint_path:
        path = Path(args.checkpoint_path)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {path}")
        return path
    run_path = Path(args.output_root) / args.pretrain_run_name / "checkpoint_last.pt"
    if run_path.exists():
        return run_path
    candidates = sorted(Path(args.output_root).glob("*/checkpoint_last.pt"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No checkpoint_last.pt found under {args.output_root}.")
    return candidates[0]


def read_checkpoint_config_json(checkpoint_dir: Path) -> dict[str, Any]:
    path = checkpoint_dir / "config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def data_config_from_raw(raw: dict[str, Any], args: argparse.Namespace) -> DataConfig:
    values = dataclass_kwargs(DataConfig, raw)
    if "cache_root" in values:
        values["cache_root"] = Path(values["cache_root"])
    if "canonical_root" in values:
        values["canonical_root"] = Path(values["canonical_root"])
    if "tickers" in values:
        values["tickers"] = tuple(values["tickers"])
    overrides = {
        "cache_root": args.cache_root,
        "canonical_root": args.canonical_root,
        "train_start_date": args.train_start_date,
        "train_end_date": args.train_end_date,
        "validation_start_date": args.validation_start_date,
        "validation_end_date": args.validation_end_date,
    }
    for key, value in overrides.items():
        if value:
            values[key] = Path(value) if key.endswith("_root") else value
    if args.tickers:
        values["tickers"] = tuple(part.strip().upper() for part in args.tickers.split(",") if part.strip()) or ("ALL",)
    return DataConfig(**values)


def model_config_from_raw(raw: dict[str, Any]) -> ModelConfig:
    return ModelConfig(**dataclass_kwargs(ModelConfig, raw))


def dataclass_kwargs(cls: type[Any], raw: dict[str, Any]) -> dict[str, Any]:
    valid = {field.name for field in fields(cls)}
    return {key: value for key, value in raw.items() if key in valid}


def infer_horizon_count(config: DataConfig) -> int:
    files = discover_chunk_files(config, start_date=config.train_start_date, end_date=config.validation_end_date)
    if not files:
        raise FileNotFoundError(f"No chunk files found under {config.cache_root}.")
    schema = pl.scan_parquet(str(files[0].path)).collect_schema()
    horizons = target_horizons_from_columns(schema.names())
    if not horizons:
        raise ValueError(f"No target_mid_h* columns found in {files[0].path}.")
    return len(horizons)


def default_probe_run_name(pretrain_run_name: str, checkpoint_step: int, probe_config: ProbeConfig) -> str:
    return (
        f"linear-probe-{pretrain_run_name}-step{checkpoint_step}"
        f"-w{probe_config.train_windows}-b{probe_config.batch_size}"
    )


def init_wandb(
    args: argparse.Namespace,
    run_name: str,
    checkpoint_path: Path,
    checkpoint_step: int,
    data_config: DataConfig,
    model_config: ModelConfig,
    probe_config: ProbeConfig,
    horizon_count: int,
) -> Any | None:
    if not args.wandb_project or args.wandb_project.lower() in {"off", "none", "disabled"}:
        print("*** WANDB project disabled; writing linear_probe_metrics.jsonl only.", flush=True)
        return None
    mode = resolve_wandb_mode(args)
    if mode == "disabled":
        print("*** WANDB explicitly disabled; writing linear_probe_metrics.jsonl only.", flush=True)
        return None
    os.environ.setdefault("WANDB_INIT_TIMEOUT", str(args.wandb_init_timeout))
    os.environ.setdefault("WANDB_LOGIN_TIMEOUT", str(min(args.wandb_init_timeout, 30)))
    os.environ["WANDB_MODE"] = mode
    if not os.environ.get("WANDB_API_KEY") and mode == "online":
        raise RuntimeError("WANDB_API_KEY is required for --wandb-mode online.")
    print(
        "*** WANDB INIT | "
        f"entity={args.wandb_entity or '<none>'} project={args.wandb_project} "
        f"run={run_name} mode={mode} api_key_present={bool(os.environ.get('WANDB_API_KEY'))}",
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
            run = wandb.init(
                entity=args.wandb_entity or None,
                project=args.wandb_project,
                name=run_name,
                config={
                    "version": EXPERIMENT_VERSION,
                    "checkpoint_path": str(checkpoint_path),
                    "checkpoint_step": checkpoint_step,
                    "horizon_count": horizon_count,
                    "data": dataclass_tree(data_config),
                    "model": dataclass_tree(model_config),
                    "probe": dataclass_tree(probe_config),
                },
                dir=str(checkpoint_path.parent),
                resume="allow",
                mode=mode,
                settings=wandb.Settings(
                    init_timeout=max(1, int(args.wandb_init_timeout)),
                    login_timeout=max(1, min(int(args.wandb_init_timeout), 30)),
                ),
            )
            result_queue.put(("ok", run))
        except Exception:
            result_queue.put(("error", traceback.format_exc()))

    thread = threading.Thread(target=init_worker, name="wandb-linear-probe-init", daemon=True)
    thread.start()
    thread.join(timeout=max(1, int(args.wandb_init_timeout)))
    if thread.is_alive():
        raise TimeoutError("W&B init timed out before returning.")
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


def write_probe_config(
    checkpoint_dir: Path,
    args: argparse.Namespace,
    checkpoint_path: Path,
    checkpoint_step: int,
    data_config: DataConfig,
    model_config: ModelConfig,
    probe_config: ProbeConfig,
    horizon_count: int,
) -> None:
    payload = {
        "version": EXPERIMENT_VERSION,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_step": checkpoint_step,
        "horizon_count": horizon_count,
        "args": vars(args),
        "data": dataclass_tree(data_config),
        "model": dataclass_tree(model_config),
        "probe": dataclass_tree(probe_config),
    }
    (checkpoint_dir / "linear_probe_config.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


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
        print("*** FATAL masked_event_model.v2.train_linear_probe error. Full traceback:", flush=True)
        traceback.print_exc()
        raise
