from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch import nn

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_user
from research.mlops.clickhouse_events import PersistentClickHouseBytesClient
from research.mlops.env import discover_env_files, load_env_files, secret_status
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.seeds import set_seed
from research.mlops.wandb_utils import init_wandb
from research.temporal_event_model.v2.config import (
    JOB_TYPE,
    MODEL_FAMILY,
    MODEL_VERSION,
    DataConfig,
    EncoderConfig,
    ExperimentConfig,
    LossConfig,
    ModelConfig,
    TrainConfig,
)
from research.temporal_event_model.v2.data import (
    build_fixed_validation_blocks,
    iter_block_batches,
    iter_fixed_validation_batches,
    load_random_return_block,
    load_temporal_index_rows,
    normalized_data_config,
)
from research.temporal_event_model.v2.losses import masked_return_loss, return_metrics
from research.temporal_event_model.v2.model import MarketTemporalReturnPredictor
from research.temporal_event_model.v2.progress import TemporalTrainingReporter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train temporal event model v2 return horizon predictor.")
    parser.add_argument("--run-name", default="", help="Run name. Defaults to a timestamped v2 name.")
    parser.add_argument("--output-root", default="", help="Run output directory.")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--blocks-per-epoch", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--context-chunks", type=int, default=64)
    parser.add_argument("--window-days", type=int, default=15)
    parser.add_argument("--future-event-horizons", default="8,16,32,64,128,256,512,1024")
    parser.add_argument("--train-stride-choices", default="16,32,64,128")
    parser.add_argument("--validation-stride-choices", default="16,32,64,128")
    parser.add_argument("--encoder-version", default="v20")
    parser.add_argument("--encoder-checkpoint", default="latest")
    parser.add_argument("--encoder-checkpoint-search-root", default=str(EncoderConfig().checkpoint_search_root))
    parser.add_argument("--fine-tune-encoder", action="store_true")
    parser.add_argument("--encoder-batch-size", type=int, default=4096)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--scheduler-t0-steps", type=int, default=1000)
    parser.add_argument("--scheduler-eta-min", type=float, default=1e-6)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "off"), default="bf16")
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--wandb-project", default=TrainConfig().wandb_project)
    parser.add_argument("--wandb-mode", default="auto")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--validation-frequency-steps", type=int, default=500)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--detailed-metrics-steps", type=int, default=100)
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--no-rich", action="store_true")
    return parser.parse_args()


def main(argv: list[str] | None = None) -> int:
    del argv
    loaded_env = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    run_name = args.run_name or f"v2-return-horizon-{time.strftime('%Y%m%d-%H%M%S')}"
    horizons = parse_int_tuple(args.future_event_horizons)
    train_config = TrainConfig(
        output_root=Path(args.output_root) if args.output_root else default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, run_name),
        batch_size=int(args.batch_size),
        epochs=int(args.epochs),
        blocks_per_epoch=int(args.blocks_per_epoch),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        scheduler_t0_steps=int(args.scheduler_t0_steps),
        scheduler_eta_min=float(args.scheduler_eta_min),
        logging_steps=int(args.logging_steps),
        detailed_metrics_steps=int(args.detailed_metrics_steps),
        validation_frequency_steps=int(args.validation_frequency_steps),
        amp_dtype=str(args.amp_dtype),
        compile_model=bool(args.compile_model),
        wandb_project=str(args.wandb_project),
        wandb_run_name=run_name,
        wandb_mode=str(args.wandb_mode),
    )
    data_config = normalized_data_config(
        DataConfig(
            clickhouse_url=str(args.clickhouse_url),
            tickers=parse_tickers(args.tickers),
            context_chunks=int(args.context_chunks),
            window_days=int(args.window_days),
            train_stride_choices=parse_int_tuple(args.train_stride_choices),
            validation_stride_choices=parse_int_tuple(args.validation_stride_choices),
            future_event_horizons=horizons,
        )
    )
    encoder_checkpoint = (
        Path(str(args.encoder_checkpoint))
        if args.print_only
        else resolve_encoder_checkpoint(str(args.encoder_checkpoint), Path(args.encoder_checkpoint_search_root))
    )
    encoder_config = EncoderConfig(
        version=str(args.encoder_version),
        checkpoint=encoder_checkpoint,
        checkpoint_search_root=Path(args.encoder_checkpoint_search_root),
        freeze=not bool(args.fine_tune_encoder),
        encoder_batch_size=int(args.encoder_batch_size),
    )
    model_config = ModelConfig(embedding_dim=encoder_config.embedding_dim)
    loss_config = LossConfig()
    experiment = ExperimentConfig(data=data_config, encoder=encoder_config, model=model_config, losses=loss_config, train=train_config)
    command = " ".join([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    if args.print_only:
        print(command)
        print(json.dumps(config_to_dict(experiment), indent=2, default=str))
        return 0

    set_seed(train_config.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    paths = RunPaths.create(train_config.output_root)
    manifest = {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "job_type": JOB_TYPE,
        "run_name": run_name,
        "command": command,
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(["WANDB_API_KEY", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "CLICKHOUSE_WORKSTATION_USER", "CLICKHOUSE_WORKSTATION_PASSWORD"]),
        "config": config_to_dict(experiment),
    }
    paths.manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    wandb_run = init_wandb(
        entity=train_config.wandb_entity,
        project=train_config.wandb_project,
        run_name=run_name,
        config=manifest,
        run_dir=paths.wandb_dir,
        mode=train_config.wandb_mode,
        timeout_seconds=train_config.wandb_timeout_seconds,
    )
    metric_logger = JsonlMetricLogger(paths.metrics_path, wandb_run=wandb_run)
    reporter = TemporalTrainingReporter(enabled=not args.no_rich)
    amp_dtype = resolve_amp_dtype(train_config.amp_dtype)

    with reporter:
        reporter.update(run_name=run_name, device=str(device), epochs=train_config.epochs, blocks_per_epoch=train_config.blocks_per_epoch, batch_size=train_config.batch_size)
        reporter.message(f"Loading market encoder {encoder_config.version} from {encoder_config.checkpoint}")
        market_encoder = build_market_encoder(encoder_config, device)
        temporal_model = MarketTemporalReturnPredictor(context_chunks=data_config.context_chunks, horizons=horizons, config=model_config).to(device)
        if train_config.compile_model:
            temporal_model = torch.compile(temporal_model)
        trainable_modules: list[nn.Module] = [temporal_model]
        if not encoder_config.freeze:
            trainable_modules.append(market_encoder)
        optimizer = torch.optim.AdamW((p for module in trainable_modules for p in module.parameters() if p.requires_grad), lr=train_config.learning_rate, weight_decay=train_config.weight_decay)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer,
            T_0=max(1, int(train_config.scheduler_t0_steps)),
            T_mult=max(1, int(train_config.scheduler_t_mult)),
            eta_min=float(train_config.scheduler_eta_min),
        )
        text_client = ClickHouseHttpClient(data_config.clickhouse_url, default_clickhouse_user(), default_clickhouse_password())
        bytes_client = PersistentClickHouseBytesClient(data_config.clickhouse_url, default_clickhouse_user(), default_clickhouse_password())
        index_rows = load_temporal_index_rows(text_client, data_config, split="train")
        validation_blocks = build_fixed_validation_blocks(data_config, seed=train_config.seed + 10_000)
        validation_batches = list(
            iter_fixed_validation_batches(
                data_config,
                validation_blocks,
                batch_size=train_config.batch_size,
                seed=train_config.seed + 20_000,
            )
        )
        if not validation_batches:
            raise RuntimeError("No validation batches could be materialized.")
        reporter.message(f"Materialized fixed validation set with {len(validation_batches)} batches")
        rng = random.Random(train_config.seed)
        best_validation_loss = math.inf
        global_step = 0
        try:
            for epoch in range(1, train_config.epochs + 1):
                for block_idx in range(1, train_config.blocks_per_epoch + 1):
                    block = load_random_return_block(bytes_client, index_rows, data_config, rng)
                    if block is None:
                        reporter.message("WARN skipped empty block after repeated attempts")
                        continue
                    batch_iter = iter_block_batches(block, data_config, train_config.batch_size)
                    for batch in batch_iter:
                        global_step += 1
                        metrics = train_step(
                            temporal_model=temporal_model,
                            market_encoder=market_encoder,
                            optimizer=optimizer,
                            scheduler=scheduler,
                            batch=batch,
                            config=experiment,
                            device=device,
                            amp_dtype=amp_dtype,
                        )
                        if global_step % train_config.logging_steps == 0:
                            train_metrics = {f"training_pretrain/{key}": value for key, value in metrics.items() if isinstance(value, float)}
                            metric_logger.log(train_metrics, global_step)
                        if global_step % train_config.checkpoint_latest_steps == 0:
                            save_checkpoint(paths, temporal_model, market_encoder, optimizer, scheduler, experiment, global_step, "latest")
                        if global_step % train_config.checkpoint_archive_steps == 0:
                            save_checkpoint(paths, temporal_model, market_encoder, optimizer, scheduler, experiment, global_step, f"step_{global_step:09d}")
                        if global_step % train_config.validation_frequency_steps == 0:
                            validation_metrics = evaluate(
                                temporal_model=temporal_model,
                                market_encoder=market_encoder,
                                config=experiment,
                                validation_batches=validation_batches,
                                device=device,
                                amp_dtype=amp_dtype,
                            )
                            metric_logger.log({f"validation_pretrain/{key}": value for key, value in validation_metrics.items()}, global_step)
                            if validation_metrics.get("loss", math.inf) < best_validation_loss:
                                best_validation_loss = validation_metrics["loss"]
                                save_checkpoint(paths, temporal_model, market_encoder, optimizer, scheduler, experiment, global_step, "best_val")
                            reporter.update(
                                val_loss=float(validation_metrics.get("loss", 0.0)),
                                val_mae_bps=float(validation_metrics.get("mae_bps", 0.0)),
                                val_sign_accuracy=float(validation_metrics.get("sign_accuracy", 0.0)),
                            )
                        if global_step % train_config.detailed_metrics_steps == 0:
                            reporter.message(f"step={global_step:,} loss={metrics['loss']:.6f} mae={metrics['mae_bps']:.3f}bps")
                        reporter.update(
                            epoch=epoch,
                            block=block_idx,
                            step=global_step,
                            lr=float(optimizer.param_groups[0]["lr"]),
                            train_loss=float(metrics["loss"]),
                            train_mae_bps=float(metrics["mae_bps"]),
                            train_sign_accuracy=float(metrics["sign_accuracy"]),
                            step_seconds=float(metrics["profile/step_seconds"]),
                            data_seconds=float(metrics["profile/data_seconds"]),
                            encode_seconds=float(metrics["profile/encode_seconds"]),
                            train_seconds=float(metrics["profile/train_seconds"]),
                            samples_per_second=float(metrics["profile/samples_per_second"]),
                            gpu_allocated_gib=gpu_allocated_gib(device),
                            gpu_reserved_gib=gpu_reserved_gib(device),
                        )
                save_checkpoint(paths, temporal_model, market_encoder, optimizer, scheduler, experiment, global_step, f"epoch_{epoch:03d}")
            save_checkpoint(paths, temporal_model, market_encoder, optimizer, scheduler, experiment, global_step, "final")
        finally:
            bytes_client.close()
            if wandb_run is not None:
                wandb_run.finish()
    return 0


def train_step(
    *,
    temporal_model: nn.Module,
    market_encoder: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    batch: dict[str, object],
    config: ExperimentConfig,
    device: torch.device,
    amp_dtype: torch.dtype | None,
) -> dict[str, float]:
    step_started = time.perf_counter()
    data_started = time.perf_counter()
    headers = batch["context_header_uint8"].to(device, non_blocking=True)
    events = batch["context_events_uint8"].to(device, non_blocking=True)
    target_norm = batch["target_return_norm"].to(device, non_blocking=True).float()
    valid_mask = batch["target_valid_mask"].to(device, non_blocking=True)
    data_seconds = time.perf_counter() - data_started
    encode_started = time.perf_counter()
    context_embeddings = encode_context_embeddings(
        market_encoder,
        headers,
        events,
        encoder_batch_size=config.encoder.encoder_batch_size,
        amp_dtype=amp_dtype,
        freeze=config.encoder.freeze,
    )
    encode_seconds = time.perf_counter() - encode_started
    train_started = time.perf_counter()
    temporal_model.train()
    if config.encoder.freeze:
        market_encoder.eval()
    else:
        market_encoder.train()
    optimizer.zero_grad(set_to_none=True)
    autocast_context = autocast_for(device, amp_dtype)
    with autocast_context:
        output = temporal_model(context_embeddings)
        loss = masked_return_loss(output.return_prediction_norm, target_norm, valid_mask, loss_name=config.losses.loss_name, huber_beta=config.losses.huber_beta)
    loss.backward()
    if config.train.grad_clip_norm > 0:
        torch.nn.utils.clip_grad_norm_(temporal_model.parameters(), float(config.train.grad_clip_norm))
    optimizer.step()
    scheduler.step()
    train_seconds = time.perf_counter() - train_started
    metrics = return_metrics(
        output.return_prediction_norm.detach(),
        target_norm.detach(),
        valid_mask.detach(),
        return_bps_scale=config.data.return_bps_scale,
        horizon_names=horizon_names(config.data.future_event_horizons),
        prefix="batch",
    )
    metrics["loss"] = float(loss.detach().item())
    metrics["mae_bps"] = metrics.get("batch/mae_bps", 0.0)
    metrics["sign_accuracy"] = metrics.get("batch/sign_accuracy", 0.0)
    step_seconds = time.perf_counter() - step_started
    metrics["profile/data_seconds"] = data_seconds
    metrics["profile/encode_seconds"] = encode_seconds
    metrics["profile/train_seconds"] = train_seconds
    metrics["profile/step_seconds"] = step_seconds
    metrics["profile/samples_per_second"] = float(config.train.batch_size / max(step_seconds, 1e-9))
    return metrics


@torch.no_grad()
def evaluate(
    *,
    temporal_model: nn.Module,
    market_encoder: nn.Module,
    config: ExperimentConfig,
    validation_batches: list[dict[str, object]],
    device: torch.device,
    amp_dtype: torch.dtype | None,
) -> dict[str, float]:
    temporal_model.eval()
    market_encoder.eval()
    losses: list[float] = []
    merged: dict[str, list[float]] = {}
    for batch in validation_batches:
        headers = batch["context_header_uint8"].to(device, non_blocking=True)
        events = batch["context_events_uint8"].to(device, non_blocking=True)
        target_norm = batch["target_return_norm"].to(device, non_blocking=True).float()
        valid_mask = batch["target_valid_mask"].to(device, non_blocking=True)
        context_embeddings = encode_context_embeddings(
            market_encoder,
            headers,
            events,
            encoder_batch_size=config.encoder.encoder_batch_size,
            amp_dtype=amp_dtype,
            freeze=True,
        )
        with autocast_for(device, amp_dtype):
            output = temporal_model(context_embeddings)
            loss = masked_return_loss(output.return_prediction_norm, target_norm, valid_mask, loss_name=config.losses.loss_name, huber_beta=config.losses.huber_beta)
        losses.append(float(loss.item()))
        batch_metrics = return_metrics(
            output.return_prediction_norm,
            target_norm,
            valid_mask,
            return_bps_scale=config.data.return_bps_scale,
            horizon_names=horizon_names(config.data.future_event_horizons),
            prefix="return",
        )
        for key, value in batch_metrics.items():
            merged.setdefault(key, []).append(float(value))
    result = {key.split("/", 1)[-1]: float(sum(values) / len(values)) for key, values in merged.items() if values}
    result["loss"] = float(sum(losses) / max(1, len(losses)))
    return result


def encode_context_embeddings(
    market_encoder: nn.Module,
    headers: torch.Tensor,
    events: torch.Tensor,
    *,
    encoder_batch_size: int,
    amp_dtype: torch.dtype | None,
    freeze: bool,
) -> torch.Tensor:
    batch_size, context_chunks = headers.shape[:2]
    flat_headers = headers.reshape(batch_size * context_chunks, headers.shape[-1])
    flat_events = events.reshape(batch_size * context_chunks, events.shape[-2], events.shape[-1])
    outputs: list[torch.Tensor] = []
    grad_context = torch.no_grad() if freeze else nullcontext()
    with grad_context:
        for start in range(0, flat_headers.shape[0], max(1, int(encoder_batch_size))):
            end = min(flat_headers.shape[0], start + max(1, int(encoder_batch_size)))
            with autocast_for(flat_headers.device, amp_dtype):
                outputs.append(market_encoder(flat_headers[start:end], flat_events[start:end]))
    return torch.cat(outputs, dim=0).reshape(batch_size, context_chunks, -1)


def build_market_encoder(config: EncoderConfig, device: torch.device) -> nn.Module:
    version = config.version.lower().strip()
    model_module = importlib.import_module(f"research.masked_event_model.{version}.model")
    config_module = importlib.import_module(f"research.masked_event_model.{version}.config")
    model_config = config_module.ModelConfig(
        d_byte=config.d_byte,
        d_model=config.d_model,
        embedding_dim=config.embedding_dim,
        n_heads=config.n_heads,
        encoder_layers=config.encoder_layers,
        decoder_layers=config.decoder_layers,
        ffn_mult=config.ffn_mult,
        dropout=config.dropout,
    )
    autoencoder = model_module.EventTokenMaskedAutoencoder(events_per_chunk=128, config=model_config)
    payload = torch.load(config.checkpoint, map_location="cpu")
    state = payload.get("model_state_dict") or payload.get("model") or payload.get("state_dict") if isinstance(payload, dict) else payload
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint does not contain a model state dict: {config.checkpoint}")
    missing, unexpected = autoencoder.load_state_dict(state, strict=False)
    if unexpected:
        print(f"WARN encoder load unexpected keys={len(unexpected)}", flush=True)
    if missing:
        print(f"WARN encoder load missing keys={len(missing)}", flush=True)
    encoder = autoencoder.build_encoder_model().to(device)
    if config.freeze:
        encoder.eval()
        for parameter in encoder.parameters():
            parameter.requires_grad_(False)
    else:
        encoder.train()
    return encoder


def save_checkpoint(
    paths: RunPaths,
    temporal_model: nn.Module,
    market_encoder: nn.Module,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    config: ExperimentConfig,
    step: int,
    name: str,
) -> None:
    checkpoint = {
        "step": int(step),
        "temporal_model": unwrap_compiled(temporal_model).state_dict(),
        "market_encoder": market_encoder.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "config": config_to_dict(config),
    }
    path = paths.checkpoints_dir / f"checkpoint_{name}.pt"
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save(checkpoint, tmp)
    tmp.replace(path)
    with paths.checkpoint_manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"step": int(step), "name": name, "path": str(path), "ts": time.time()}) + "\n")


def resolve_encoder_checkpoint(value: str, search_root: Path) -> Path:
    if value and value.lower() not in {"latest", "auto"}:
        return Path(value)
    candidates = sorted(search_root.glob("*/checkpoints/checkpoint_latest.pt"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No checkpoint_latest.pt found under {search_root}")
    return candidates[0]


def parse_int_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in str(raw).split(",") if part.strip())


def parse_tickers(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip().upper() for part in str(raw).split(",") if part.strip())
    return values or ("ALL",)


def horizon_names(horizons: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(f"h{int(value)}" for value in horizons)


def resolve_amp_dtype(value: str) -> torch.dtype | None:
    if value == "off":
        return None
    if value == "fp16":
        return torch.float16
    if value == "bf16":
        return torch.bfloat16
    raise ValueError(value)


def autocast_for(device: torch.device, amp_dtype: torch.dtype | None):
    if amp_dtype is None or device.type != "cuda":
        return nullcontext()
    return torch.amp.autocast("cuda", dtype=amp_dtype)


def unwrap_compiled(model: nn.Module) -> nn.Module:
    return getattr(model, "_orig_mod", model)


def gpu_allocated_gib(device: torch.device) -> float:
    if device.type != "cuda":
        return 0.0
    return float(torch.cuda.memory_allocated(device) / 1024**3)


def gpu_reserved_gib(device: torch.device) -> float:
    if device.type != "cuda":
        return 0.0
    return float(torch.cuda.memory_reserved(device) / 1024**3)


def config_to_dict(config: Any) -> Any:
    if hasattr(config, "__dataclass_fields__"):
        return {key: config_to_dict(getattr(config, key)) for key in config.__dataclass_fields__}
    if isinstance(config, Path):
        return str(config)
    if isinstance(config, tuple):
        return [config_to_dict(value) for value in config]
    return config


if __name__ == "__main__":
    raise SystemExit(main())
