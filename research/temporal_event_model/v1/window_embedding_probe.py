from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_user
from research.mlops.clickhouse_events import PersistentClickHouseBytesClient, encode_unified_event_window
from research.mlops.env import discover_env_files, load_env_files, secret_status
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.paths import RunPaths
from research.mlops.wandb_utils import init_wandb
from research.temporal_event_model.v1.cache_probe import (
    DEFAULT_CACHE_ROOT,
    DEFAULT_ENCODER_CHECKPOINT,
    DEFAULT_ENCODER_VERSION,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_WANDB_PROJECT,
    ProbeConfig,
    autocast_context,
    build_targets_from_bytes,
    format_metric_line,
    log_confusion_tables_to_wandb,
    memory_profile,
    probe_loss_and_metrics,
    resolve_amp_dtype,
    save_checkpoint,
    scalar_metrics_without_confusion,
    to_manifest_config,
)
from research.temporal_event_model.v1.config import DataConfig
from research.temporal_event_model.v1.data import (
    TemporalIndexRow,
    fetch_ticker_time_window,
    load_temporal_index_rows,
    normalized_data_config,
)
from research.temporal_event_model.v1.model import EmbeddingContextFutureLabelPredictor, load_trusted_torch_checkpoint
from research.temporal_event_model.v1.progress import ProbeProgressState, ProbeTrainingReporter


MODEL_FAMILY = "temporal_event_model"
MODEL_VERSION = "v1"
JOB_TYPE = "window_embedding_probe"
MICROSECONDS_PER_DAY = 86_400_000_000
HEADER_BYTES = 14
EVENT_BYTES = 16
EVENTS_PER_CHUNK = 128
DEFAULT_WINDOW_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT.parent / "window_embedding_probe_laptop"
DEFAULT_WINDOW_PROJECT = "June2026-event-encoder-window-probes"


@dataclass(slots=True)
class WindowProbeConfig:
    clickhouse_url: str = ""
    clickhouse_database: str = "market_sip_compact"
    events_table: str = "events"
    train_index_table: str = "train_2019_to_2025"
    validation_index_table: str = "validation_2026"
    tickers: tuple[str, ...] = ("ALL",)
    cache_root: Path = DEFAULT_CACHE_ROOT
    output_root: Path = DEFAULT_WINDOW_OUTPUT_ROOT
    run_name: str = ""
    batch_size: int = 512
    epochs: int = 2
    blocks_per_epoch: int = 24
    validation_blocks: int = 8
    validation_batches_per_block: int = 2
    window_days: int = 15
    block_max_events: int = 250_000
    min_samples_per_block: int = 512
    recent_count: int = 16
    recent_stride: int = 1
    older_count: int = 16
    older_min_lag: int = 32
    older_max_lag: int = 1024
    label_chunks: int = 2
    encoder_version: str = DEFAULT_ENCODER_VERSION
    encoder_checkpoint: Path = DEFAULT_ENCODER_CHECKPOINT
    encoder_d_byte: int = 40
    encoder_d_model: int = 256
    encoder_embedding_dim: int = 32
    encoder_heads: int = 8
    encoder_layers: int = 10
    encoder_decoder_layers: int = 4
    encoder_ffn_mult: int = 4
    encoder_dropout: float = 0.08
    encoder_batch_size: int = 4096
    temporal_d_model: int = 128
    temporal_layers: int = 2
    temporal_heads: int = 4
    temporal_ffn_mult: int = 4
    hidden_dim: int = 128
    dropout: float = 0.10
    flat_threshold_bps: float = 2.0
    strong_threshold_bps: float = 20.0
    learning_rate: float = 8e-4
    weight_decay: float = 1e-4
    grad_clip_norm: float = 1.0
    amp_dtype: str = "bf16"
    seed: int = 17
    logging_steps: int = 10
    validation_frequency_blocks: int = 8
    progress_layout: str = "auto"
    progress_refresh_per_second: float = 1.0
    wandb_project: str = DEFAULT_WINDOW_PROJECT
    wandb_entity: str = "mehdifaraji"
    wandb_mode: str = "auto"
    clickhouse_max_threads: int = 8
    clickhouse_max_memory_usage: str = "120G"

    @property
    def context_chunks(self) -> int:
        return int(self.recent_count) + int(self.older_count)

    @property
    def horizons(self) -> tuple[int, ...]:
        return tuple(EVENTS_PER_CHUNK * (idx + 1) for idx in range(int(self.label_chunks)))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT, args.env_file), verbose=True)
    config = build_config(args)
    run_name = config.run_name or default_run_name(config)
    run_root = Path(config.output_root) / run_name
    device = torch.device("cuda" if torch.cuda.is_available() and args.device != "cpu" else "cpu")
    if device.type == "cuda":
        torch.set_float32_matmul_precision("high")
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    rng = random.Random(config.seed)

    print("=" * 100, flush=True)
    print("Temporal v1 streaming-window embedding probe", flush=True)
    print(f"run_name={run_name}", flush=True)
    print(f"device={device}", flush=True)
    print(f"clickhouse={resolved_clickhouse_url(config)} db={config.clickhouse_database}.{config.events_table}", flush=True)
    print(f"encoder={config.encoder_version} checkpoint={config.encoder_checkpoint}", flush=True)
    print(
        f"context recent={config.recent_count}x stride={config.recent_stride} "
        f"older={config.older_count} lags={config.older_min_lag}..{config.older_max_lag}",
        flush=True,
    )
    print(f"target_chunks={config.horizons} objective=tick_regression_plus_classes", flush=True)
    print(f"secrets={secret_status(('WANDB_API_KEY', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_WORKSTATION_PASSWORD'))}", flush=True)
    print("=" * 100, flush=True)
    if args.print_only:
        print("PRINT ONLY: no training started.", flush=True)
        return 0
    paths = RunPaths.create(run_root)

    text_client = ClickHouseHttpClient(resolved_clickhouse_url(config), default_clickhouse_user(), default_clickhouse_password())
    train_index_rows = load_temporal_index_rows(text_client, data_config(config), split="train")
    validation_blocks = build_validation_windows(text_client, config, seed=config.seed + 777)
    bytes_client = PersistentClickHouseBytesClient(resolved_clickhouse_url(config), default_clickhouse_user(), default_clickhouse_password())

    event_encoder = build_event_encoder(config, device)
    model = EmbeddingContextFutureLabelPredictor(
        embedding_dim=config.encoder_embedding_dim,
        temporal_d_model=config.temporal_d_model,
        temporal_layers=config.temporal_layers,
        temporal_heads=config.temporal_heads,
        temporal_ffn_mult=config.temporal_ffn_mult,
        target_chunks=config.label_chunks,
        hidden_dim=config.hidden_dim,
        dropout=config.dropout,
        max_context_chunks=config.context_chunks,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    # The number of train batches in a random ticker window is not known until
    # after the ClickHouse rows are fetched and converted to rolling chunks. Use
    # a conservative scheduler horizon so a large block does not consume the
    # entire cosine cycle, then update the Rich progress estimate dynamically as
    # real block sizes are observed.
    scheduler_steps_estimate = estimate_scheduler_steps(config)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer,
        T_max=scheduler_steps_estimate,
        eta_min=config.learning_rate * 0.05,
    )
    amp_dtype = resolve_amp_dtype(config.amp_dtype)
    reporter_state = ProbeProgressState(
        run_name=run_name,
        device=str(device),
        data_source=f"{config.clickhouse_database}.{config.events_table}",
        encoder_checkpoint=str(config.encoder_checkpoint),
        output_dir=str(run_root),
        batch_size=config.batch_size,
        epochs=config.epochs,
        total_steps=scheduler_steps_estimate,
        train_shard_count=config.blocks_per_epoch,
        validation_shard_count=len(validation_blocks),
        probe_parameters=sum(p.numel() for p in model.parameters()),
        frozen_encoder_parameters=sum(p.numel() for p in event_encoder.parameters()),
    )
    wandb_run = init_wandb(
        entity=config.wandb_entity,
        project=config.wandb_project,
        run_name=run_name,
        config=manifest_config(config),
        run_dir=paths.wandb_dir,
        mode=config.wandb_mode,
        timeout_seconds=90,
    )
    metric_logger = JsonlMetricLogger(paths.metrics_path, wandb_run)
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config=manifest_config(config),
        data_roots={"clickhouse": f"{config.clickhouse_database}.{config.events_table}"},
        output_root=run_root,
        source_checkpoint=config.encoder_checkpoint,
        wandb_info={"project": config.wandb_project, "run_name": run_name},
    )

    global_step = 0
    best_validation_loss = math.inf
    started = time.perf_counter()
    try:
        with ProbeTrainingReporter(layout=config.progress_layout, state=reporter_state, refresh_per_second=config.progress_refresh_per_second) as reporter:
            reporter.message(f"Training started. Output: {run_root}")
            reporter.message(f"context_lags={context_lags(config).tolist()}")
            for epoch in range(1, config.epochs + 1):
                reporter.message(f"EPOCH START {epoch}/{config.epochs}")
                for block_index in range(1, config.blocks_per_epoch + 1):
                    block = load_random_embedding_block(
                        client=bytes_client,
                        index_rows=train_index_rows,
                        config=config,
                        event_encoder=event_encoder,
                        device=device,
                        amp_dtype=amp_dtype,
                        rng=rng,
                        reporter=reporter,
                    )
                    if block is None:
                        reporter.message("WARN skipped empty/invalid block")
                        continue
                    shard_metrics, global_step = train_embedding_block(
                        model=model,
                        optimizer=optimizer,
                        scheduler=scheduler,
                        block=block,
                        config=config,
                        device=device,
                        amp_dtype=amp_dtype,
                        epoch=epoch,
                        block_index=block_index,
                        global_step=global_step,
                        metric_logger=metric_logger,
                        reporter=reporter,
                        run_start_time=started,
                    )
                    metric_logger.log(
                        scalar_metrics_without_confusion({f"training/block_{key}": value for key, value in shard_metrics.items()}),
                        global_step,
                    )
                    if config.validation_frequency_blocks > 0 and block_index % config.validation_frequency_blocks == 0:
                        validation_metrics = evaluate_window_probe(
                            model=model,
                            event_encoder=event_encoder,
                            client=bytes_client,
                            blocks=validation_blocks,
                            config=config,
                            device=device,
                            amp_dtype=amp_dtype,
                            reporter=reporter,
                        )
                        validation_metrics = {f"validation/{key}": value for key, value in validation_metrics.items()}
                        log_confusion_tables_to_wandb(metric_logger.wandb_run, validation_metrics, global_step, prefix="validation")
                        validation_scalar_metrics = scalar_metrics_without_confusion(validation_metrics)
                        metric_logger.log(validation_scalar_metrics, global_step)
                        reporter.update({}, step=global_step, validation_metrics=validation_scalar_metrics)
                        reporter.message(format_metric_line("VALIDATION", global_step, validation_scalar_metrics))
                        val_loss = validation_metrics.get("validation/loss", math.inf)
                        save_checkpoint(paths.checkpoints_dir / "checkpoint_latest.pt", model, optimizer, scheduler, proxy_probe_config(config), global_step, epoch)
                        if val_loss < best_validation_loss:
                            best_validation_loss = float(val_loss)
                            save_checkpoint(paths.checkpoints_dir / "checkpoint_best_val.pt", model, optimizer, scheduler, proxy_probe_config(config), global_step, epoch)
                            reporter.message(f"Saved checkpoint best_val: {paths.checkpoints_dir / 'checkpoint_best_val.pt'}")
                epoch_checkpoint = paths.checkpoints_dir / f"checkpoint_epoch_{epoch:03d}.pt"
                save_checkpoint(epoch_checkpoint, model, optimizer, scheduler, proxy_probe_config(config), global_step, epoch)
                reporter.message(f"Saved checkpoint epoch {epoch}: {epoch_checkpoint}")
    finally:
        bytes_client.close()
        if wandb_run is not None:
            wandb_run.finish()
    print(f"DONE steps={global_step:,} elapsed_hours={(time.perf_counter() - started) / 3600.0:.2f} output={run_root}", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a production-like streaming embedding temporal probe from ClickHouse windows.")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--clickhouse-database", default="market_sip_compact")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--train-index-table", default="train_2019_to_2025")
    parser.add_argument("--validation-index-table", default="validation_2026")
    parser.add_argument("--tickers", default="ALL")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_WINDOW_OUTPUT_ROOT)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--blocks-per-epoch", type=int, default=24)
    parser.add_argument("--validation-blocks", type=int, default=8)
    parser.add_argument("--validation-batches-per-block", type=int, default=2)
    parser.add_argument("--window-days", type=int, default=15)
    parser.add_argument("--block-max-events", type=int, default=250_000)
    parser.add_argument("--min-samples-per-block", type=int, default=512)
    parser.add_argument("--recent-count", type=int, default=16)
    parser.add_argument("--recent-stride", type=int, default=1)
    parser.add_argument("--older-count", type=int, default=16)
    parser.add_argument("--older-min-lag", type=int, default=32)
    parser.add_argument("--older-max-lag", type=int, default=1024)
    parser.add_argument("--label-chunks", type=int, default=2)
    parser.add_argument("--encoder-version", default=DEFAULT_ENCODER_VERSION)
    parser.add_argument("--encoder-checkpoint", type=Path, default=DEFAULT_ENCODER_CHECKPOINT)
    parser.add_argument("--encoder-d-byte", type=int, default=40)
    parser.add_argument("--encoder-d-model", type=int, default=256)
    parser.add_argument("--encoder-embedding-dim", type=int, default=32)
    parser.add_argument("--encoder-heads", type=int, default=8)
    parser.add_argument("--encoder-layers", type=int, default=10)
    parser.add_argument("--encoder-decoder-layers", type=int, default=4)
    parser.add_argument("--encoder-ffn-mult", type=int, default=4)
    parser.add_argument("--encoder-dropout", type=float, default=0.08)
    parser.add_argument("--encoder-batch-size", type=int, default=4096)
    parser.add_argument("--temporal-d-model", type=int, default=128)
    parser.add_argument("--temporal-layers", type=int, default=2)
    parser.add_argument("--temporal-heads", type=int, default=4)
    parser.add_argument("--temporal-ffn-mult", type=int, default=4)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--flat-threshold-bps", type=float, default=2.0)
    parser.add_argument("--strong-threshold-bps", type=float, default=20.0)
    parser.add_argument("--learning-rate", type=float, default=8e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--grad-clip-norm", type=float, default=1.0)
    parser.add_argument("--amp-dtype", choices=("off", "fp16", "bf16"), default="bf16")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--validation-frequency-blocks", type=int, default=8)
    parser.add_argument("--wandb-project", default=DEFAULT_WINDOW_PROJECT)
    parser.add_argument("--wandb-entity", default="mehdifaraji")
    parser.add_argument("--wandb-mode", default="auto")
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "off"), default="auto")
    parser.add_argument("--progress-refresh-per-second", type=float, default=1.0)
    parser.add_argument("--clickhouse-max-threads", type=int, default=8)
    parser.add_argument("--clickhouse-max-memory-usage", default="120G")
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--env-file", type=Path, default=None)
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> WindowProbeConfig:
    tickers = tuple(part.strip().upper() for part in str(args.tickers).split(",") if part.strip()) or ("ALL",)
    if args.label_chunks < 1:
        raise ValueError("--label-chunks must be positive")
    if args.recent_count < 1 or args.older_count < 0:
        raise ValueError("recent_count must be positive and older_count must be non-negative")
    return WindowProbeConfig(
        clickhouse_url=args.clickhouse_url,
        clickhouse_database=args.clickhouse_database,
        events_table=args.events_table,
        train_index_table=args.train_index_table,
        validation_index_table=args.validation_index_table,
        tickers=tickers,
        output_root=args.output_root,
        run_name=args.run_name,
        batch_size=args.batch_size,
        epochs=args.epochs,
        blocks_per_epoch=args.blocks_per_epoch,
        validation_blocks=args.validation_blocks,
        validation_batches_per_block=args.validation_batches_per_block,
        window_days=args.window_days,
        block_max_events=args.block_max_events,
        min_samples_per_block=args.min_samples_per_block,
        recent_count=args.recent_count,
        recent_stride=args.recent_stride,
        older_count=args.older_count,
        older_min_lag=args.older_min_lag,
        older_max_lag=args.older_max_lag,
        label_chunks=args.label_chunks,
        encoder_version=args.encoder_version,
        encoder_checkpoint=args.encoder_checkpoint,
        encoder_d_byte=args.encoder_d_byte,
        encoder_d_model=args.encoder_d_model,
        encoder_embedding_dim=args.encoder_embedding_dim,
        encoder_heads=args.encoder_heads,
        encoder_layers=args.encoder_layers,
        encoder_decoder_layers=args.encoder_decoder_layers,
        encoder_ffn_mult=args.encoder_ffn_mult,
        encoder_dropout=args.encoder_dropout,
        encoder_batch_size=args.encoder_batch_size,
        temporal_d_model=args.temporal_d_model,
        temporal_layers=args.temporal_layers,
        temporal_heads=args.temporal_heads,
        temporal_ffn_mult=args.temporal_ffn_mult,
        hidden_dim=args.hidden_dim,
        dropout=args.dropout,
        flat_threshold_bps=args.flat_threshold_bps,
        strong_threshold_bps=args.strong_threshold_bps,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        grad_clip_norm=args.grad_clip_norm,
        amp_dtype=args.amp_dtype,
        seed=args.seed,
        logging_steps=args.logging_steps,
        validation_frequency_blocks=args.validation_frequency_blocks,
        progress_layout=args.progress_layout,
        progress_refresh_per_second=args.progress_refresh_per_second,
        wandb_project=args.wandb_project,
        wandb_entity=args.wandb_entity,
        wandb_mode=args.wandb_mode,
        clickhouse_max_threads=args.clickhouse_max_threads,
        clickhouse_max_memory_usage=args.clickhouse_max_memory_usage,
    )


def default_run_name(config: WindowProbeConfig) -> str:
    ckpt_name = Path(config.encoder_checkpoint).parent.parent.name if config.encoder_checkpoint else "random"
    return f"v1-window-embedding-probe-{config.encoder_version}-{ckpt_name}-ctx{config.context_chunks}-bs{config.batch_size}"


def resolved_clickhouse_url(config: WindowProbeConfig) -> str:
    return (
        config.clickhouse_url
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get("CLICKHOUSE_URL")
        or os.environ.get("TD__DATABASE__CLICKHOUSE__ENDPOINT_URL")
        or "http://localhost:18123"
    )


def data_config(config: WindowProbeConfig) -> DataConfig:
    return normalized_data_config(
        DataConfig(
            clickhouse_url=resolved_clickhouse_url(config),
            clickhouse_database=config.clickhouse_database,
            events_table=config.events_table,
            train_index_table=config.train_index_table,
            validation_index_table=config.validation_index_table,
            tickers=config.tickers,
            events_per_chunk=EVENTS_PER_CHUNK,
            context_chunks=config.context_chunks,
            target_chunks=1,
            window_days=config.window_days,
            train_stride_choices=(1,),
            validation_stride_choices=(1,),
            block_max_events=config.block_max_events,
            min_samples_per_block=config.min_samples_per_block,
            validation_blocks=config.validation_blocks,
            validation_batches_per_block=config.validation_batches_per_block,
            clickhouse_max_threads=config.clickhouse_max_threads,
            clickhouse_max_memory_usage=config.clickhouse_max_memory_usage,
        )
    )


def proxy_probe_config(config: WindowProbeConfig) -> ProbeConfig:
    return ProbeConfig(
        cache_root=config.cache_root,
        batch_size=config.batch_size,
        epochs=config.epochs,
        horizons=config.horizons,
        flat_threshold_bps=config.flat_threshold_bps,
        strong_threshold_bps=config.strong_threshold_bps,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
        grad_clip_norm=config.grad_clip_norm,
        hidden_dim=config.hidden_dim,
        dropout=config.dropout,
        seed=config.seed,
        amp_dtype=config.amp_dtype,
        encoder_version=config.encoder_version,
        encoder_checkpoint=config.encoder_checkpoint,
        encoder_d_byte=config.encoder_d_byte,
        encoder_d_model=config.encoder_d_model,
        encoder_embedding_dim=config.encoder_embedding_dim,
        encoder_heads=config.encoder_heads,
        encoder_layers=config.encoder_layers,
        encoder_decoder_layers=config.encoder_decoder_layers,
        encoder_ffn_mult=config.encoder_ffn_mult,
        encoder_dropout=config.encoder_dropout,
        output_root=config.output_root,
        run_name=config.run_name,
        wandb_project=config.wandb_project,
        wandb_entity=config.wandb_entity,
        wandb_mode=config.wandb_mode,
        logging_steps=config.logging_steps,
        validation_frequency_shards=config.validation_frequency_blocks,
        preload_shards_to_ram=False,
        progress_layout=config.progress_layout,
        progress_refresh_per_second=config.progress_refresh_per_second,
    )


def manifest_config(config: WindowProbeConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key, value in list(payload.items()):
        if isinstance(value, Path):
            payload[key] = str(value)
        elif isinstance(value, tuple):
            payload[key] = list(value)
    payload["context_lags"] = context_lags(config).tolist()
    payload["target_design"] = to_manifest_config(proxy_probe_config(config))["target_design"]
    payload["streaming_embedding_design"] = {
        "chunk_events": EVENTS_PER_CHUNK,
        "embedding_stream_stride": 1,
        "selection": "older geometric lags plus dense recent lags from rolling per-ticker embedding stream",
    }
    return payload


def build_event_encoder(config: WindowProbeConfig, device: torch.device) -> torch.nn.Module:
    version = config.encoder_version.lower().strip()
    model_module = importlib.import_module(f"research.masked_event_model.{version}.model")
    config_module = importlib.import_module(f"research.masked_event_model.{version}.config")
    model_config = config_module.ModelConfig(
        d_byte=config.encoder_d_byte,
        d_model=config.encoder_d_model,
        embedding_dim=config.encoder_embedding_dim,
        n_heads=config.encoder_heads,
        encoder_layers=config.encoder_layers,
        decoder_layers=config.encoder_decoder_layers,
        ffn_mult=config.encoder_ffn_mult,
        dropout=config.encoder_dropout,
    )
    autoencoder = model_module.EventTokenMaskedAutoencoder(events_per_chunk=EVENTS_PER_CHUNK, config=model_config)
    payload = load_trusted_torch_checkpoint(config.encoder_checkpoint, map_location="cpu")
    state = payload.get("model_state_dict") or payload.get("model") or payload.get("state_dict") if isinstance(payload, dict) else payload
    if not isinstance(state, dict):
        raise RuntimeError(f"Checkpoint does not contain model weights: {config.encoder_checkpoint}")
    missing, unexpected = autoencoder.load_state_dict(state, strict=False)
    if missing:
        print(f"WARN encoder missing keys={len(missing)}", flush=True)
    if unexpected:
        print(f"WARN encoder unexpected keys={len(unexpected)}", flush=True)
    encoder = autoencoder.build_encoder_model().to(device).eval()
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
    return encoder


def context_lags(config: WindowProbeConfig) -> np.ndarray:
    recent = [idx * max(1, int(config.recent_stride)) for idx in range(int(config.recent_count))]
    older: list[int] = []
    if config.older_count > 0:
        min_lag = max(max(recent) + 1, int(config.older_min_lag))
        max_lag = max(min_lag, int(config.older_max_lag))
        raw = np.geomspace(float(min_lag), float(max_lag), num=max(config.older_count * 4, config.older_count + 1))
        for value in raw:
            lag = int(round(float(value)))
            if lag not in recent and lag not in older:
                older.append(lag)
            if len(older) >= config.older_count:
                break
        lag = min_lag
        while len(older) < config.older_count:
            if lag not in recent and lag not in older:
                older.append(lag)
            lag += 1
    lags = sorted(older + recent, reverse=True)
    expected = config.context_chunks
    if len(lags) != expected:
        raise RuntimeError(f"Built {len(lags)} context lags, expected {expected}: {lags}")
    return np.asarray(lags, dtype=np.int64)


@dataclass(slots=True)
class EmbeddingBlock:
    ticker: str
    start_us: int
    end_us: int
    headers: np.ndarray
    events: np.ndarray
    embeddings: np.ndarray
    valid_chunks: np.ndarray
    candidates: np.ndarray
    profile: dict[str, float]


def build_validation_windows(client: ClickHouseHttpClient, config: WindowProbeConfig, *, seed: int) -> list[tuple[str, int, int]]:
    rows = load_temporal_index_rows(client, data_config(config), split="validation")
    rng = random.Random(seed)
    out: list[tuple[str, int, int]] = []
    window_us = int(config.window_days * MICROSECONDS_PER_DAY)
    attempts = 0
    while len(out) < config.validation_blocks and attempts < config.validation_blocks * 100:
        attempts += 1
        row = rng.choice(rows)
        if row.last_sip_timestamp_us - row.first_sip_timestamp_us <= window_us:
            start_us = row.first_sip_timestamp_us
        else:
            start_us = rng.randint(row.first_sip_timestamp_us, row.last_sip_timestamp_us - window_us)
        out.append((row.ticker, start_us, start_us + window_us))
    if not out:
        raise RuntimeError("No validation windows could be sampled.")
    return out


def load_random_embedding_block(
    *,
    client: PersistentClickHouseBytesClient,
    index_rows: list[TemporalIndexRow],
    config: WindowProbeConfig,
    event_encoder: torch.nn.Module,
    device: torch.device,
    amp_dtype: torch.dtype | None,
    rng: random.Random,
    reporter: ProbeTrainingReporter | None,
) -> EmbeddingBlock | None:
    for _attempt in range(40):
        row = rng.choice(index_rows)
        block = load_embedding_block_for_window(
            client=client,
            ticker=row.ticker,
            first_us=row.first_sip_timestamp_us,
            last_us=row.last_sip_timestamp_us,
            config=config,
            event_encoder=event_encoder,
            device=device,
            amp_dtype=amp_dtype,
            rng=rng,
            reporter=reporter,
        )
        if block is not None and block.candidates.size >= minimum_train_samples_per_block(config):
            return block
    return None


def load_embedding_block_for_window(
    *,
    client: PersistentClickHouseBytesClient,
    ticker: str,
    first_us: int,
    last_us: int,
    config: WindowProbeConfig,
    event_encoder: torch.nn.Module,
    device: torch.device,
    amp_dtype: torch.dtype | None,
    rng: random.Random,
    reporter: ProbeTrainingReporter | None,
) -> EmbeddingBlock | None:
    started = time.perf_counter()
    window_us = int(config.window_days * MICROSECONDS_PER_DAY)
    if last_us - first_us <= window_us:
        start_us = first_us
    else:
        start_us = rng.randint(first_us, last_us - window_us)
    end_us = start_us + window_us
    dc = data_config(config)
    query_started = time.perf_counter()
    rows = fetch_ticker_time_window(client, dc, ticker, start_us, end_us)
    query_seconds = time.perf_counter() - query_started
    if rows.shape[0] > config.block_max_events:
        rows = crop_random_embedding_subrange(rows, config, rng)
    if rows.shape[0] < minimum_rows_required(config):
        return None
    encode_started = time.perf_counter()
    headers, events, valid = build_rolling_chunks(rows)
    compact_seconds = time.perf_counter() - encode_started
    candidates = valid_context_origins(valid, config)
    if candidates.size < minimum_train_samples_per_block(config):
        return None
    embed_started = time.perf_counter()
    embeddings = encode_valid_chunks(event_encoder, headers, events, valid, config, device, amp_dtype)
    embed_seconds = time.perf_counter() - embed_started
    profile = {
        "data/block_load_seconds": time.perf_counter() - started,
        "data/block_query_seconds": query_seconds,
        "data/block_compact_seconds": compact_seconds,
        "data/block_embed_seconds": embed_seconds,
        "data/block_rows": float(rows.shape[0]),
        "data/block_chunks": float(headers.shape[0]),
        "data/block_valid_chunks": float(np.count_nonzero(valid)),
        "data/block_valid_origins": float(candidates.size),
    }
    if reporter is not None:
        reporter.message(
            f"BLOCK ticker={ticker} rows={rows.shape[0]:,} chunks={headers.shape[0]:,} "
            f"origins={candidates.size:,} query={query_seconds:.2f}s compact={compact_seconds:.2f}s embed={embed_seconds:.2f}s"
        )
    return EmbeddingBlock(
        ticker=ticker,
        start_us=start_us,
        end_us=end_us,
        headers=headers,
        events=events,
        embeddings=embeddings,
        valid_chunks=valid,
        candidates=candidates,
        profile=profile,
    )


def minimum_rows_required(config: WindowProbeConfig) -> int:
    return EVENTS_PER_CHUNK + int(np.max(context_lags(config))) + config.label_chunks * EVENTS_PER_CHUNK


def minimum_train_samples_per_block(config: WindowProbeConfig) -> int:
    return max(int(config.min_samples_per_block), int(config.batch_size))


def max_valid_origins_per_block(config: WindowProbeConfig) -> int:
    max_lag = int(np.max(context_lags(config)))
    max_future_offset = int(config.label_chunks) * EVENTS_PER_CHUNK
    max_chunks = max(0, int(config.block_max_events) - EVENTS_PER_CHUNK + 1)
    return max(1, max_chunks - max_lag - max_future_offset)


def estimate_scheduler_steps(config: WindowProbeConfig) -> int:
    max_steps_per_block = max(1, max_valid_origins_per_block(config) // max(1, int(config.batch_size)))
    return max(1, int(config.epochs) * int(config.blocks_per_epoch) * max_steps_per_block)


def update_dynamic_progress_total(
    reporter: ProbeTrainingReporter,
    *,
    config: WindowProbeConfig,
    epoch: int,
    block_index: int,
    global_step: int,
    current_block_steps: int,
) -> None:
    blocks_done_before = max(0, (int(epoch) - 1) * int(config.blocks_per_epoch) + (int(block_index) - 1))
    total_blocks = max(1, int(config.epochs) * int(config.blocks_per_epoch))
    if blocks_done_before > 0:
        observed_steps_per_block = max(1.0, float(global_step) / float(blocks_done_before))
    else:
        observed_steps_per_block = max(1.0, float(current_block_steps))
    remaining_blocks_after_current = max(0, total_blocks - blocks_done_before - 1)
    estimated_total = int(
        round(float(global_step) + float(current_block_steps) + remaining_blocks_after_current * observed_steps_per_block)
    )
    reporter.state.total_steps = max(int(global_step) + int(current_block_steps), estimated_total)


def crop_random_embedding_subrange(rows: np.ndarray, config: WindowProbeConfig, rng: random.Random) -> np.ndarray:
    required = minimum_rows_required(config)
    keep = max(required + minimum_train_samples_per_block(config), min(config.block_max_events, int(rows.shape[0])))
    if keep >= rows.shape[0]:
        return rows
    start = rng.randint(0, int(rows.shape[0]) - keep)
    return rows[start : start + keep].copy()


def build_rolling_chunks(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    chunk_count = max(0, int(rows.shape[0]) - EVENTS_PER_CHUNK + 1)
    headers = np.zeros((chunk_count, HEADER_BYTES), dtype=np.uint8)
    events = np.zeros((chunk_count, EVENTS_PER_CHUNK, EVENT_BYTES), dtype=np.uint8)
    valid = np.zeros((chunk_count,), dtype=bool)
    for chunk_index in range(chunk_count):
        start = chunk_index
        end = start + EVENTS_PER_CHUNK
        previous_sip_us = int(rows["sip_timestamp_us"][start - 1]) if start > 0 else None
        encoded = encode_unified_event_window(rows[start:end], previous_sip_us=previous_sip_us)
        if isinstance(encoded, str):
            continue
        header, event_bytes = encoded
        headers[chunk_index] = header
        events[chunk_index] = event_bytes
        valid[chunk_index] = True
    return headers, events, valid


@torch.no_grad()
def encode_valid_chunks(
    encoder: torch.nn.Module,
    headers: np.ndarray,
    events: np.ndarray,
    valid: np.ndarray,
    config: WindowProbeConfig,
    device: torch.device,
    amp_dtype: torch.dtype | None,
) -> np.ndarray:
    embeddings = np.zeros((headers.shape[0], config.encoder_embedding_dim), dtype=np.float32)
    valid_indices = np.flatnonzero(valid)
    for start in range(0, valid_indices.size, config.encoder_batch_size):
        idx = valid_indices[start : start + config.encoder_batch_size]
        header_tensor = torch.from_numpy(headers[idx].copy()).to(device=device, dtype=torch.uint8, non_blocking=True)
        event_tensor = torch.from_numpy(events[idx].copy()).to(device=device, dtype=torch.uint8, non_blocking=True)
        with autocast_context(device, amp_dtype):
            out = encoder(header_tensor, event_tensor)
        embeddings[idx] = out.detach().float().cpu().numpy()
    return embeddings


def valid_context_origins(valid: np.ndarray, config: WindowProbeConfig) -> np.ndarray:
    lags = context_lags(config)
    max_lag = int(np.max(lags))
    max_future_offset = config.label_chunks * EVENTS_PER_CHUNK
    low = max_lag
    high_exclusive = int(valid.shape[0]) - max_future_offset
    if high_exclusive <= low:
        return np.empty((0,), dtype=np.int64)
    candidates = np.arange(low, high_exclusive, dtype=np.int64)
    context_ok = valid[candidates[:, None] - lags[None, :]].all(axis=1)
    target_offsets = candidates[:, None] + (np.arange(1, config.label_chunks + 1, dtype=np.int64)[None, :] * EVENTS_PER_CHUNK)
    target_ok = valid[target_offsets].all(axis=1)
    return candidates[context_ok & target_ok]


def batch_from_embedding_block(block: EmbeddingBlock, candidate_indices: np.ndarray, config: WindowProbeConfig, device: torch.device) -> dict[str, torch.Tensor]:
    lags = context_lags(config)
    context_embeddings = block.embeddings[candidate_indices[:, None] - lags[None, :]]
    label_offsets = candidate_indices[:, None] + (np.arange(1, config.label_chunks + 1, dtype=np.int64)[None, :] * EVENTS_PER_CHUNK)
    target_values, target_metrics, valid = build_targets_from_bytes(
        block.headers[candidate_indices],
        block.events[candidate_indices],
        block.headers[label_offsets],
        block.events[label_offsets],
        proxy_probe_config(config),
    )
    return {
        "context_embeddings": torch.from_numpy(context_embeddings.copy()).to(device=device, dtype=torch.float32, non_blocking=True),
        "target_low_high_ticks_norm": torch.from_numpy(target_values["low_high_ticks_norm"]).to(device=device, dtype=torch.float32, non_blocking=True),
        "target_up_class": torch.from_numpy(target_values["up_class"]).to(device=device, dtype=torch.long, non_blocking=True),
        "target_down_class": torch.from_numpy(target_values["down_class"]).to(device=device, dtype=torch.long, non_blocking=True),
        "target_path_class": torch.from_numpy(target_values["path_class"]).to(device=device, dtype=torch.long, non_blocking=True),
        "target_metrics": {key: torch.from_numpy(value).to(device=device, non_blocking=True) for key, value in target_metrics.items()},
        "valid_mask": torch.from_numpy(valid).to(device=device, dtype=torch.bool, non_blocking=True),
    }


def train_embedding_block(
    *,
    model: EmbeddingContextFutureLabelPredictor,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    block: EmbeddingBlock,
    config: WindowProbeConfig,
    device: torch.device,
    amp_dtype: torch.dtype | None,
    epoch: int,
    block_index: int,
    global_step: int,
    metric_logger: JsonlMetricLogger,
    reporter: ProbeTrainingReporter,
    run_start_time: float,
) -> tuple[dict[str, float], int]:
    model.train()
    rng = np.random.default_rng(config.seed + epoch * 100_000 + block_index)
    order = block.candidates.copy()
    rng.shuffle(order)
    steps = int(order.size) // int(config.batch_size)
    update_dynamic_progress_total(
        reporter,
        config=config,
        epoch=epoch,
        block_index=block_index,
        global_step=global_step,
        current_block_steps=steps,
    )
    running: dict[str, float] = {}
    started = time.perf_counter()
    for step in range(steps):
        step_started = time.perf_counter()
        batch_idx = order[step * config.batch_size : (step + 1) * config.batch_size]
        data_started = time.perf_counter()
        batch = batch_from_embedding_block(block, batch_idx, config, device)
        data_seconds = time.perf_counter() - data_started
        optimizer.zero_grad(set_to_none=True)
        forward_started = time.perf_counter()
        with autocast_context(device, amp_dtype):
            output = model(batch["context_embeddings"])
            loss, metrics = probe_loss_and_metrics(
                low_high_tick_pred=output.low_high_tick_pred,
                up_class_logits=output.up_class_logits,
                down_class_logits=output.down_class_logits,
                path_class_logits=output.path_class_logits,
                target_low_high_ticks_norm=batch["target_low_high_ticks_norm"],
                target_up_class=batch["target_up_class"],
                target_down_class=batch["target_down_class"],
                target_path_class=batch["target_path_class"],
                target_metrics=batch["target_metrics"],
                valid_mask=batch["valid_mask"],
                config=proxy_probe_config(config),
            )
        forward_seconds = time.perf_counter() - forward_started
        backward_started = time.perf_counter()
        loss.backward()
        if config.grad_clip_norm > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip_norm)
        backward_seconds = time.perf_counter() - backward_started
        optimizer_started = time.perf_counter()
        optimizer.step()
        scheduler.step()
        optimizer_seconds = time.perf_counter() - optimizer_started
        step_seconds = time.perf_counter() - step_started
        global_step += 1
        scalar_metrics = scalar_metrics_without_confusion(metrics)
        for key, value in scalar_metrics.items():
            running[key] = running.get(key, 0.0) + float(value)
        if global_step % max(1, config.logging_steps) == 0 or step == 0:
            elapsed = time.perf_counter() - run_start_time
            samples_seen = global_step * config.batch_size
            row = {
                **{f"training/{key}": value for key, value in scalar_metrics.items()},
                "training/lr": float(optimizer.param_groups[0]["lr"]),
                "training/epoch": float(epoch),
                "training/block": float(block_index),
                "training/shard_position": float(block_index),
                "training/shard": float(block_index),
                "training/shard_step": float(step + 1),
                "training/shard_steps": float(steps),
                "training/samples_seen_total": float(samples_seen),
                "training/samples_per_sec": float(samples_seen / max(elapsed, 1e-6)),
                "profile/step_seconds": float(step_seconds),
                "profile/data_seconds": float(data_seconds),
                "profile/forward_seconds": float(forward_seconds),
                "profile/backward_seconds": float(backward_seconds),
                "profile/optimizer_seconds": float(optimizer_seconds),
                **memory_profile(device),
                **block.profile,
            }
            metric_logger.log(row, global_step)
            reporter.update(row, step=global_step)
            reporter.message(format_metric_line("TRAIN", global_step, row))
    elapsed = time.perf_counter() - started
    out = {key: value / max(1, steps) for key, value in running.items()}
    out["seconds"] = elapsed
    out["samples_per_sec"] = (steps * config.batch_size) / max(elapsed, 1e-6)
    reporter.message(format_metric_line("BLOCK DONE", global_step, {f"shard/{key}": value for key, value in out.items()}))
    return out, global_step


@torch.no_grad()
def evaluate_window_probe(
    *,
    model: EmbeddingContextFutureLabelPredictor,
    event_encoder: torch.nn.Module,
    client: PersistentClickHouseBytesClient,
    blocks: list[tuple[str, int, int]],
    config: WindowProbeConfig,
    device: torch.device,
    amp_dtype: torch.dtype | None,
    reporter: ProbeTrainingReporter | None,
) -> dict[str, float]:
    started = time.perf_counter()
    model.eval()
    sums: dict[str, float] = {}
    batches = 0
    rng = random.Random(config.seed + 99_999)
    for ticker, start_us, end_us in blocks:
        block = load_embedding_block_for_window(
            client=client,
            ticker=ticker,
            first_us=start_us,
            last_us=end_us,
            config=config,
            event_encoder=event_encoder,
            device=device,
            amp_dtype=amp_dtype,
            rng=rng,
            reporter=None,
        )
        if block is None:
            continue
        order = block.candidates.copy()
        np.random.default_rng(config.seed + int(start_us % 1_000_000)).shuffle(order)
        max_batches = min(config.validation_batches_per_block, int(order.size) // int(config.batch_size))
        for step in range(max_batches):
            idx = order[step * config.batch_size : (step + 1) * config.batch_size]
            batch = batch_from_embedding_block(block, idx, config, device)
            with autocast_context(device, amp_dtype):
                output = model(batch["context_embeddings"])
                _, metrics = probe_loss_and_metrics(
                    low_high_tick_pred=output.low_high_tick_pred,
                    up_class_logits=output.up_class_logits,
                    down_class_logits=output.down_class_logits,
                    path_class_logits=output.path_class_logits,
                    target_low_high_ticks_norm=batch["target_low_high_ticks_norm"],
                    target_up_class=batch["target_up_class"],
                    target_down_class=batch["target_down_class"],
                    target_path_class=batch["target_path_class"],
                    target_metrics=batch["target_metrics"],
                    valid_mask=batch["valid_mask"],
                    config=proxy_probe_config(config),
                )
            for key, value in metrics.items():
                sums[key] = sums.get(key, 0.0) + float(value)
            batches += 1
    out = {key: (float(value) if "_confusion/" in key else float(value) / max(1, batches)) for key, value in sums.items()}
    out["seconds"] = time.perf_counter() - started
    if reporter is not None:
        reporter.message(f"VALIDATION batches={batches} seconds={out['seconds']:.2f}")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
