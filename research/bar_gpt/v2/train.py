from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np
import torch
from torch.utils.data import DataLoader, IterableDataset

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.bar_gpt.v2 import (
    LEARNING_CONTRACT,
    MODEL_FAMILY,
    MODEL_VERSION,
    assert_checkpoint_version,
)
from research.bar_gpt.v2.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig, to_dict
from research.bar_gpt.v2.data import AUTOREGRESSIVE_VIEW_NAMES, PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, BarGPTBatch, BarGPTExample, BarView, collate_examples
from research.bar_gpt.v2.loader import (
    ArrowStreamClient,
    BarGPTIterableDataset,
    BarGPTSequentialDataset,
    ClickHouseBarStreamConfig,
    SequentialBlockPlan,
    SequentialSessionPlan,
    build_session_examples,
    make_dataloader,
    make_sequential_dataloader,
    month_units,
    validation_block_plan,
)
from research.bar_gpt.v2.offline_shards import (
    OfflineBlockRef,
    OfflineShardDataset,
    OfflineShardUnit,
    discover_offline_units,
    hydrate_offline_runtime_config,
    make_offline_dataloader,
    verify_shard_catalog_lock,
)
from research.bar_gpt.v2.full_chunk_training import (
    EpochChunkPlan,
    build_epoch_chunk_plan,
    load_epoch_chunk_plan,
    load_full_chunk_manifest,
    load_full_training_refs,
    write_epoch_chunk_plan,
)
from research.bar_gpt.v2.model_discovery import load_discovery_manifest, panel_refs
from research.bar_gpt.v2.prefetch import DeviceBatchPrefetcher
from research.bar_gpt.v2.sampling import CoverageCursor, coverage_plan_summary
from research.bar_gpt.v2.model import BarGPTV2, build_model_mermaid
from research.bar_gpt.v2.metrics import ValidationAccumulator
from research.bar_gpt.v2.objectives import BarGPTLoss, compute_loss
from research.bar_gpt.v2.progress import TrainingProgressState, TrainingReporter
from research.bar_gpt.v2.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v2.targets import (
    AVAILABILITY_TARGET_COUNT,
    CONTINUOUS_TARGET_COUNT,
    RETURN_CLASS_COUNT,
    RETURN_TARGET_COUNT,
    TARGET_NAMES,
)
from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.metrics import AsyncJsonlMetricLogger
from research.mlops.model_artifacts import parameter_summary, write_model_artifacts, write_model_card
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.schedulers import (
    EpochChunkCosineScheduler,
    SampleCosineRestartScheduler,
    SampleWarmupCosineScheduler,
)
from research.mlops.seeds import set_seed
from research.mlops.wandb_utils import init_wandb


JOB_TYPE = "train"
DISCOVERY_VALIDATION_WORKERS = 8
_INTERRUPTED = False
TrainingScheduler = (
    SampleCosineRestartScheduler
    | SampleWarmupCosineScheduler
    | EpochChunkCosineScheduler
)
_RESUME_RUNTIME_DATA_FIELDS = frozenset(
    {
        "ready_queue_blocks",
        "worker_prefetch_batches",
        "clickhouse_max_threads_per_worker",
        "clickhouse_max_block_size",
        "clickhouse_max_memory_usage",
        "clickhouse_query_days",
        "clickhouse_max_bytes_before_external_sort",
        "clickhouse_retry_attempts",
        "clickhouse_retry_initial_seconds",
        "clickhouse_retry_max_seconds",
        "pin_memory",
        "persistent_workers",
    }
)


def _handle_interrupt(_signum: int, _frame: Any) -> None:
    global _INTERRUPTED
    _INTERRUPTED = True


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip().upper() for item in value.split(",") if item.strip())


def _int_csv(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def _named_int_csv(value: str) -> tuple[tuple[str, int], ...]:
    result: list[tuple[str, int]] = []
    for item in value.split(","):
        name, raw = item.split("=", 1)
        result.append((name.strip(), int(raw.strip())))
    return tuple(result)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    data, model, train = DataConfig(), BarGPTConfig(), TrainConfig()
    parser = argparse.ArgumentParser(description="Pretrain BarGPT v2 from the certified v12 offline shards.")
    parser.add_argument("--database", default=data.database)
    parser.add_argument("--one-second-table", default=data.one_second_table)
    parser.add_argument("--manifest-table", default=data.manifest_table)
    parser.add_argument("--alias-manifest-table", default=data.alias_manifest_table)
    parser.add_argument("--daily-table", default=data.daily_table)
    parser.add_argument("--daily-manifest-table", default=data.daily_manifest_table)
    parser.add_argument("--condition-table", default=data.condition_table)
    parser.add_argument("--condition-status-table", default=data.condition_status_table)
    parser.add_argument("--identity-database", default=data.identity_database)
    parser.add_argument("--identity-interval-table", default=data.identity_interval_table)
    parser.add_argument("--identity-entity-table", default=data.identity_entity_table)
    parser.add_argument("--identity-event-table", default=data.identity_event_table)
    parser.add_argument("--split-database", default=data.split_database)
    parser.add_argument("--split-table", default=data.split_table)
    parser.add_argument("--tickers", default=",".join(data.tickers))
    parser.add_argument("--start-date", default=data.start_date)
    parser.add_argument("--end-date", default=data.end_date)
    parser.add_argument("--validation-start-date", default=data.validation_start_date)
    parser.add_argument("--daily-history-start-date", default=data.daily_history_start_date)
    parser.add_argument("--horizons-us", default=",".join(str(value) for value in data.horizons_us))
    parser.add_argument("--context-bars-1s", type=int, default=data.context_bars_1s)
    parser.add_argument("--origin-bars-1s", type=int, default=data.origin_bars_1s)
    parser.add_argument("--min-origins-per-block", type=int, default=data.min_origins_per_block)
    parser.add_argument("--coverage-mode", choices=("sequential", "stratified"), default=data.coverage_mode)
    parser.add_argument("--coverage-blocks-per-unit", type=int, default=data.coverage_blocks_per_unit)
    parser.add_argument("--origin-fetch-candidate-blocks", type=int, default=data.origin_fetch_candidate_blocks)
    parser.add_argument("--origin-emit-blocks-per-chunk", type=int, default=data.origin_emit_blocks_per_chunk)
    parser.add_argument("--validation-blocks-per-slice", type=int, default=data.validation_blocks_per_slice)
    parser.add_argument("--daily-context-bars", type=int, default=data.daily_context_bars)
    parser.add_argument("--intraday-context-bars", default=','.join(f"{name}={value}" for name, value in data.intraday_context_bars))
    parser.add_argument("--calendar-context-bars", default=','.join(f"{name}={value}" for name, value in data.calendar_context_bars))
    parser.add_argument("--calendar-warmup-daily-bars", type=int, default=data.calendar_warmup_daily_bars)
    parser.add_argument("--batch-size", type=int, default=data.batch_size)
    parser.add_argument("--loader-workers", type=int, default=data.loader_workers)
    parser.add_argument("--ready-queue-blocks", type=int, default=data.ready_queue_blocks)
    parser.add_argument("--worker-prefetch-batches", type=int, default=data.worker_prefetch_batches)
    parser.add_argument(
        "--offline-length-bucket-batches",
        type=int,
        default=data.offline_length_bucket_batches,
    )
    parser.add_argument("--clickhouse-max-threads-per-worker", type=int, default=data.clickhouse_max_threads_per_worker)
    parser.add_argument("--clickhouse-max-memory-usage", type=int, default=data.clickhouse_max_memory_usage)
    parser.add_argument("--clickhouse-query-days", type=int, default=data.clickhouse_query_days)
    parser.add_argument("--clickhouse-prefetch-pages", type=int, default=data.clickhouse_prefetch_pages)
    parser.add_argument("--clickhouse-retry-attempts", type=int, default=data.clickhouse_retry_attempts)
    parser.add_argument("--clickhouse-retry-initial-seconds", type=float, default=data.clickhouse_retry_initial_seconds)
    parser.add_argument("--clickhouse-retry-max-seconds", type=float, default=data.clickhouse_retry_max_seconds)
    parser.add_argument(
        "--clickhouse-max-bytes-before-external-sort",
        type=int,
        default=data.clickhouse_max_bytes_before_external_sort,
    )
    parser.add_argument("--balance-activity-regimes", action=argparse.BooleanOptionalAction, default=data.balance_activity_regimes)
    parser.add_argument("--d-model", type=int, default=model.d_model)
    parser.add_argument("--n-layers", type=int, default=model.n_layers)
    parser.add_argument("--n-heads", type=int, default=model.n_heads)
    parser.add_argument("--n-kv-heads", type=int, default=model.n_kv_heads)
    parser.add_argument("--dropout", type=float, default=model.dropout)
    parser.add_argument("--output-root", default=str(train.output_root))
    parser.add_argument("--run-name", default="")
    parser.add_argument("--epochs", type=int, default=train.epochs)
    parser.add_argument("--max-samples", type=int, default=train.max_samples)
    parser.add_argument("--learning-rate", type=float, default=train.learning_rate)
    parser.add_argument("--weight-decay", type=float, default=train.weight_decay)
    parser.add_argument("--grad-clip-norm", type=float, default=train.grad_clip_norm)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=train.amp)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "float32"), default=train.amp_dtype)
    parser.add_argument("--compile-model", action=argparse.BooleanOptionalAction, default=train.compile_model)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=train.gradient_accumulation_steps)
    parser.add_argument("--cuda-prefetch", action=argparse.BooleanOptionalAction, default=train.cuda_prefetch)
    parser.add_argument("--logging-samples", type=int, default=train.logging_samples)
    parser.add_argument(
        "--training-metrics-interval-samples",
        type=int,
        default=train.training_metrics_interval_samples,
    )
    parser.add_argument(
        "--epoch-train-evaluation-origins",
        type=int,
        default=train.epoch_train_evaluation_origins,
    )
    parser.add_argument(
        "--full-validation-final-epoch-only",
        action=argparse.BooleanOptionalAction,
        default=train.full_validation_final_epoch_only,
        help=(
            "use the bounded monitor at non-final epoch boundaries and reserve "
            "paired training/full-validation evaluation for the final epoch"
        ),
    )
    parser.add_argument(
        "--full-chunk-training",
        action=argparse.BooleanOptionalAction,
        default=train.full_chunk_training,
        help=(
            "train exact offline chunks with adaptive replay, fixed per-chunk "
            "validation, and outer-epoch early stopping"
        ),
    )
    parser.add_argument(
        "--chunk-target-origins",
        type=int,
        default=train.chunk_target_origins,
    )
    parser.add_argument(
        "--chunk-validation-origins",
        type=int,
        default=train.chunk_validation_origins,
    )
    parser.add_argument("--max-chunk-epochs", type=int, default=train.max_chunk_epochs)
    parser.add_argument(
        "--chunk-early-stopping-patience",
        type=int,
        default=train.chunk_early_stopping_patience,
    )
    parser.add_argument(
        "--chunk-early-stopping-min-relative-delta",
        type=float,
        default=train.chunk_early_stopping_min_relative_delta,
    )
    parser.add_argument(
        "--outer-early-stopping-patience",
        type=int,
        default=train.outer_early_stopping_patience,
    )
    parser.add_argument(
        "--outer-early-stopping-min-relative-delta",
        type=float,
        default=train.outer_early_stopping_min_relative_delta,
    )
    parser.add_argument("--validation-interval-samples", type=int, default=train.validation_interval_samples)
    parser.add_argument("--validation-initial-samples", type=int, default=train.validation_initial_samples)
    parser.add_argument(
        "--monitor-evaluation-origins",
        type=int,
        default=train.monitor_evaluation_origins,
        help="maximum origins consumed by each periodic F2 monitor evaluation",
    )
    parser.add_argument("--validation-batches", type=int, default=train.validation_batches)
    parser.add_argument("--validation-runs-per-epoch", type=int, default=train.validation_runs_per_epoch)
    parser.add_argument("--warmup-samples", type=int, default=train.warmup_samples)
    parser.add_argument("--warmup-fraction", type=float, default=train.warmup_fraction)
    parser.add_argument("--minimum-learning-rate", type=float, default=train.minimum_learning_rate)
    parser.add_argument("--cosine-cycle-samples", type=int, default=train.cosine_cycle_samples)
    parser.add_argument("--cosine-restart-decay", type=float, default=train.cosine_restart_decay)
    parser.add_argument(
        "--checkpoint-validation-evaluations",
        type=int,
        default=train.checkpoint_validation_evaluations,
        help="stage one resumable checkpoint after this many validation evaluations",
    )
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=train.progress_layout)
    parser.add_argument("--wandb-project", default=train.wandb_project)
    parser.add_argument("--wandb-entity", default=train.wandb_entity)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=train.wandb_mode)
    parser.add_argument("--wandb-init-timeout", type=int, default=train.wandb_init_timeout)
    parser.add_argument("--resume-checkpoint", default="")
    parser.add_argument("--seed", type=int, default=train.seed)
    parser.add_argument("--data-source", choices=("offline", "clickhouse"), default="clickhouse")
    parser.add_argument("--offline-shard-root", default=r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
    parser.add_argument("--offline-train-start-date", default="2019-01-01")
    parser.add_argument("--offline-train-end-date", default="2026-01-01")
    parser.add_argument("--offline-validation-start-date", default="2026-01-01")
    parser.add_argument("--offline-validation-end-date", default="2026-08-01")
    parser.add_argument(
        "--experiment-manifest",
        default="",
        help="fixed block manifest for architecture/quality discovery",
    )
    parser.add_argument(
        "--scheduler-mode",
        choices=("cosine-restarts", "single-cosine", "epoch-chunk-cosine"),
        default="cosine-restarts",
    )
    parser.add_argument("--dummy-data", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def build_config(args: argparse.Namespace) -> ExperimentConfig:
    horizons = _int_csv(args.horizons_us)
    data = DataConfig(
        database=str(args.database),
        one_second_table=str(args.one_second_table),
        manifest_table=str(args.manifest_table),
        alias_manifest_table=str(args.alias_manifest_table),
        daily_table=str(args.daily_table),
        daily_manifest_table=str(args.daily_manifest_table),
        condition_table=str(args.condition_table),
        condition_status_table=str(args.condition_status_table),
        identity_database=str(args.identity_database),
        identity_interval_table=str(args.identity_interval_table),
        identity_entity_table=str(args.identity_entity_table),
        identity_event_table=str(args.identity_event_table),
        split_database=str(args.split_database),
        split_table=str(args.split_table),
        tickers=_csv(args.tickers),
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        validation_start_date=str(args.validation_start_date),
        daily_history_start_date=str(args.daily_history_start_date),
        horizons_us=horizons,
        context_bars_1s=int(args.context_bars_1s),
        origin_bars_1s=int(args.origin_bars_1s),
        min_origins_per_block=int(args.min_origins_per_block),
        coverage_mode=str(args.coverage_mode),
        coverage_blocks_per_unit=int(args.coverage_blocks_per_unit),
        origin_fetch_candidate_blocks=int(args.origin_fetch_candidate_blocks),
        origin_emit_blocks_per_chunk=int(args.origin_emit_blocks_per_chunk),
        validation_blocks_per_slice=int(args.validation_blocks_per_slice),
        daily_context_bars=int(args.daily_context_bars),
        intraday_context_bars=_named_int_csv(str(args.intraday_context_bars)),
        calendar_context_bars=_named_int_csv(str(args.calendar_context_bars)),
        calendar_warmup_daily_bars=int(args.calendar_warmup_daily_bars),
        batch_size=int(args.batch_size),
        maximum_target_horizon_us=max(horizons),
        loader_workers=int(args.loader_workers),
        ready_queue_blocks=int(args.ready_queue_blocks),
        worker_prefetch_batches=int(args.worker_prefetch_batches),
        offline_length_bucket_batches=int(args.offline_length_bucket_batches),
        clickhouse_max_threads_per_worker=int(args.clickhouse_max_threads_per_worker),
        clickhouse_max_memory_usage=int(args.clickhouse_max_memory_usage),
        clickhouse_query_days=int(args.clickhouse_query_days),
        clickhouse_prefetch_pages=int(args.clickhouse_prefetch_pages),
        clickhouse_max_bytes_before_external_sort=int(args.clickhouse_max_bytes_before_external_sort),
        clickhouse_retry_attempts=int(args.clickhouse_retry_attempts),
        clickhouse_retry_initial_seconds=float(args.clickhouse_retry_initial_seconds),
        clickhouse_retry_max_seconds=float(args.clickhouse_retry_max_seconds),
        balance_activity_regimes=bool(args.balance_activity_regimes),
    )
    if str(args.data_source) == "offline":
        data = hydrate_offline_runtime_config(Path(args.offline_shard_root), data)
    if len(data.tickers) < 2:
        raise ValueError("training requires at least two tickers; single-ticker configurations are supported only for data and shard builds")
    data.validate()
    model = BarGPTConfig(
        d_model=int(args.d_model),
        n_layers=int(args.n_layers),
        n_heads=int(args.n_heads),
        n_kv_heads=int(args.n_kv_heads),
        dropout=float(args.dropout),
    )
    model.validate()
    train = TrainConfig(
        output_root=Path(args.output_root),
        run_name=str(args.run_name),
        epochs=int(args.epochs),
        max_samples=int(args.max_samples),
        learning_rate=float(args.learning_rate),
        weight_decay=float(args.weight_decay),
        grad_clip_norm=float(args.grad_clip_norm),
        amp=bool(args.amp),
        amp_dtype=str(args.amp_dtype),
        compile_model=bool(args.compile_model),
        gradient_accumulation_steps=int(args.gradient_accumulation_steps),
        cuda_prefetch=bool(args.cuda_prefetch),
        seed=int(args.seed),
        wandb_project=str(args.wandb_project),
        wandb_entity=str(args.wandb_entity),
        wandb_mode=str(args.wandb_mode),
        wandb_init_timeout=int(args.wandb_init_timeout),
        logging_samples=int(args.logging_samples),
        training_metrics_interval_samples=int(args.training_metrics_interval_samples),
        epoch_train_evaluation_origins=int(args.epoch_train_evaluation_origins),
        full_validation_final_epoch_only=bool(args.full_validation_final_epoch_only),
        full_chunk_training=bool(args.full_chunk_training),
        chunk_target_origins=int(args.chunk_target_origins),
        chunk_validation_origins=int(args.chunk_validation_origins),
        max_chunk_epochs=int(args.max_chunk_epochs),
        chunk_early_stopping_patience=int(args.chunk_early_stopping_patience),
        chunk_early_stopping_min_relative_delta=float(
            args.chunk_early_stopping_min_relative_delta
        ),
        outer_early_stopping_patience=int(args.outer_early_stopping_patience),
        outer_early_stopping_min_relative_delta=float(
            args.outer_early_stopping_min_relative_delta
        ),
        validation_interval_samples=int(args.validation_interval_samples),
        validation_initial_samples=int(args.validation_initial_samples),
        monitor_evaluation_origins=int(args.monitor_evaluation_origins),
        validation_batches=int(args.validation_batches),
        validation_runs_per_epoch=int(args.validation_runs_per_epoch),
        warmup_samples=int(args.warmup_samples),
        warmup_fraction=float(args.warmup_fraction),
        minimum_learning_rate=float(args.minimum_learning_rate),
        cosine_cycle_samples=int(args.cosine_cycle_samples),
        cosine_restart_decay=float(args.cosine_restart_decay),
        scheduler_mode=str(args.scheduler_mode),
        checkpoint_validation_evaluations=int(args.checkpoint_validation_evaluations),
        progress_layout=str(args.progress_layout),
    )
    train.validate()
    return ExperimentConfig(model=model, data=data, train=train)


def preflight(client: ClickHouseHttpClient, config: DataConfig) -> dict[str, str]:
    tables = {row.split("\t")[0] for row in client.query_tsv(
        f"SELECT name FROM system.tables WHERE database = {sql_string(config.database)} FORMAT TSVRaw"
    ).splitlines() if row}
    required = {
        config.one_second_table,
        config.manifest_table,
        config.daily_table,
        config.daily_manifest_table,
    }
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"missing required ClickHouse tables in {config.database}: {missing}")
    identity_tables = {row.split("\t")[0] for row in client.query_tsv(
        f"SELECT name FROM system.tables WHERE database = {sql_string(config.identity_database)} FORMAT TSVRaw"
    ).splitlines() if row}
    identity_required = {
        config.identity_interval_table,
        config.identity_entity_table,
        config.identity_event_table,
    }
    if config.split_database == config.identity_database:
        identity_required.add(config.split_table)
    identity_missing = sorted(identity_required - identity_tables)
    if identity_missing:
        raise RuntimeError(f"missing required identity tables in {config.identity_database}: {identity_missing}")
    if config.split_database != config.identity_database:
        split_tables = {row.split("\t")[0] for row in client.query_tsv(
            f"SELECT name FROM system.tables WHERE database = {sql_string(config.split_database)} FORMAT TSVRaw"
        ).splitlines() if row}
        if config.split_table not in split_tables:
            raise RuntimeError(f"missing split authority {config.split_database}.{config.split_table}")
    def certified_raw_ranges(manifest_table: str) -> tuple[str, list[tuple[str, str]]]:
        manifest_columns = {row.split("\t")[0] for row in client.query_tsv(
            f"SELECT name FROM system.columns WHERE database={sql_string(config.database)} "
            f"AND table={sql_string(manifest_table)} FORMAT TSVRaw"
        ).splitlines() if row}
        if "local_date" not in manifest_columns:
            raise RuntimeError(f"{config.database}.{manifest_table} is not a raw one-second manifest")
        sql = f"""
SELECT message
FROM {quote_ident(config.database)}.{quote_ident(manifest_table)} FINAL
WHERE artifact_name = {sql_string(config.one_second_table)} AND status = 'certified_range'
ORDER BY local_date, unit_id
FORMAT TSVRaw
"""
        intervals: list[tuple[str, str]] = []
        for line in client.query_tsv(sql).splitlines():
            match = re.search(r"certified (?:empty )?range \[(\d{4}-\d{2}-\d{2}),(\d{4}-\d{2}-\d{2})\)", line)
            if match:
                intervals.append((match.group(1), match.group(2)))
        if not intervals:
            raise RuntimeError(f"{config.database}.{manifest_table} has no certified raw training range")
        cursor = config.start_date
        for start, end in sorted(intervals):
            if end <= cursor or start > cursor:
                continue
            cursor = max(cursor, end)
            if cursor >= config.end_date:
                break
        if cursor < config.end_date:
            raise RuntimeError(
                f"requested training range [{config.start_date},{config.end_date}) is not continuously certified "
                f"by {manifest_table}; certified coverage reaches only {cursor}"
            )
        return cursor, intervals

    cursor, intervals = certified_raw_ranges(config.manifest_table)
    alias_cursor = "identity_interval_audited"
    alias_intervals: list[tuple[str, str]] = []
    if config.alias_manifest_table in tables:
        alias_sql = f"""
SELECT message
FROM {quote_ident(config.database)}.{quote_ident(config.alias_manifest_table)} FINAL
WHERE artifact_name={sql_string(config.one_second_table)} AND status='certified_range'
FORMAT TSVRaw
"""
        for line in client.query_tsv(alias_sql).splitlines():
            match = re.search(r"certified (?:empty )?range \[(\d{4}-\d{2}-\d{2}),(\d{4}-\d{2}-\d{2})\)", line)
            if match:
                alias_intervals.append((match.group(1), match.group(2)))
    alias_mapping_sql = f"""
SELECT upper(e.current_ticker), upper(i.ticker_normalized), toString(i.valid_from_date),
       toString(ifNull(i.valid_to_date_exclusive,toDate('9999-12-31')))
FROM (SELECT provider_entity_key,current_ticker,is_deleted FROM {quote_ident(config.identity_database)}.{quote_ident(config.identity_entity_table)} FINAL) AS e
INNER JOIN (SELECT provider_entity_key,ticker_normalized,valid_from_date,valid_to_date_exclusive,is_deleted,mapping_status
            FROM {quote_ident(config.identity_database)}.{quote_ident(config.identity_interval_table)} FINAL) AS i USING provider_entity_key
WHERE e.is_deleted=0 AND i.is_deleted=0 AND i.mapping_status='mapped'
  AND upper(e.current_ticker) IN ({', '.join(sql_string(ticker) for ticker in config.tickers)})
  AND upper(e.current_ticker) != upper(i.ticker_normalized)
FORMAT TSVRaw
"""
    alias_checks = 0
    for line in client.query_tsv(alias_mapping_sql).splitlines():
        canonical, source, valid_from, valid_to = line.split("\t")[:4]
        left, right = max(config.start_date, valid_from), min(config.end_date, valid_to)
        if left >= right:
            continue
        missing_sql = f"""
SELECT count()
FROM
(
    SELECT DISTINCT session_date
    FROM {quote_ident(config.database)}.{quote_ident(config.daily_table)} FINAL
    WHERE source_ticker={sql_string(source)} AND session_kind='regular'
      AND session_date>=toDate({sql_string(left)}) AND session_date<toDate({sql_string(right)})
) AS d
LEFT ANTI JOIN
(
    SELECT DISTINCT local_date
    FROM {quote_ident(config.database)}.{quote_ident(config.one_second_table)}
    WHERE ticker={sql_string(source)}
      AND local_date>=toDate({sql_string(left)}) AND local_date<toDate({sql_string(right)})
) AS b ON d.session_date=b.local_date
FORMAT TSVRaw
"""
        missing_days = int(client.query_tsv(missing_sql).strip() or "0")
        if missing_days:
            raise RuntimeError(
                f"raw identity alias {source}->{canonical} is missing one-second coverage for "
                f"{missing_days} daily sessions in [{left},{right})"
            )
        alias_checks += 1
    daily_sql = f"""
SELECT chunk_start, chunk_end
FROM {quote_ident(config.database)}.{quote_ident(config.daily_manifest_table)} FINAL
WHERE artifact_name = {sql_string(config.daily_table)} AND status = 'complete'
ORDER BY chunk_start, chunk_end
FORMAT TSVRaw
"""
    daily_cursor = config.daily_history_start_date
    daily_ranges = 0
    for line in client.query_tsv(daily_sql).splitlines():
        values = line.split("\t")
        if len(values) != 2:
            continue
        start, end = values
        if end <= daily_cursor or start > daily_cursor:
            continue
        daily_cursor = max(daily_cursor, end)
        daily_ranges += 1
        if daily_cursor >= config.end_date:
            break
    if daily_cursor < config.end_date:
        raise RuntimeError(
            f"requested daily-session range [{config.daily_history_start_date},{config.end_date}) is not continuously certified; "
            f"certified coverage reaches only {daily_cursor}"
        )
    condition_positive_sql = f"""
SELECT count(),
       countIf(condition_halt_pause_count>0),
       countIf(condition_resume_count>0),
       countIf(condition_news_risk_count>0),
       countIf(condition_luld_limit_state_count>0)
FROM {quote_ident(config.database)}.{quote_ident(config.one_second_table)}
WHERE ticker IN ({', '.join(sql_string(ticker) for ticker in config.tickers)})
  AND local_date>=toDate({sql_string(config.start_date)})
  AND local_date<toDate({sql_string(config.validation_start_date)})
FORMAT TSVRaw
"""
    positive_values = [int(value) for value in client.query_tsv(condition_positive_sql).strip().split("\t") if value]
    if len(positive_values) != 5:
        raise RuntimeError("embedded one-second condition audit returned an invalid schema")
    return {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "learning_contract": LEARNING_CONTRACT,
        "certified_start": config.start_date,
        "certified_end": cursor,
        "certified_ranges": str(len(intervals)),
        "alias_certified_end": alias_cursor,
        "alias_certified_ranges": str(len(alias_intervals)),
        "alias_identity_ranges_checked": str(alias_checks),
        "daily_certified_end": daily_cursor,
        "daily_certified_ranges": str(daily_ranges),
        "condition_authority": "embedded_1s",
        "condition_positive_rows": str(positive_values[0]),
        "condition_halt_rows": str(positive_values[1]),
        "condition_resume_rows": str(positive_values[2]),
        "condition_news_rows": str(positive_values[3]),
        "condition_luld_rows": str(positive_values[4]),
    }


def _condition_certification_coverage(
    status_tsv: str,
    *,
    condition_table: str,
    expected_days: int,
) -> tuple[set[str], int]:
    prefix = condition_table + ":tickers="
    covered: set[str] = set()
    complete_artifacts = 0
    for line in status_tsv.splitlines():
        values = line.split("\t")
        if len(values) < 3 or not values[0].startswith(prefix):
            continue
        row_count, completed_count = int(values[1]), int(values[2])
        if row_count != expected_days or completed_count != expected_days:
            continue
        tickers = {ticker.strip().upper() for ticker in values[0][len(prefix):].split(",") if ticker.strip()}
        if not tickers:
            continue
        covered.update(tickers)
        complete_artifacts += 1
    return covered, complete_artifacts


def _stream_config(data: DataConfig) -> ClickHouseBarStreamConfig:
    return ClickHouseBarStreamConfig(
        url=default_clickhouse_url(),
        user=default_clickhouse_user(),
        password=default_clickhouse_password(),
        database=data.database,
        table=data.one_second_table,
        max_threads=data.clickhouse_max_threads_per_worker,
        max_block_size=data.clickhouse_max_block_size,
        max_memory_usage=data.clickhouse_max_memory_usage,
        query_days=data.clickhouse_query_days,
        max_bytes_before_external_sort=data.clickhouse_max_bytes_before_external_sort,
        retry_attempts=data.clickhouse_retry_attempts,
        retry_initial_seconds=data.clickhouse_retry_initial_seconds,
        retry_max_seconds=data.clickhouse_retry_max_seconds,
    )


def _resolved_warmup_samples(config: TrainConfig, schedule_samples: int) -> int:
    if schedule_samples < 2:
        raise ValueError("schedule_samples must be at least two")
    requested = (
        int(config.warmup_samples)
        if config.warmup_samples > 0
        else int(round(schedule_samples * config.warmup_fraction))
    )
    return min(requested, schedule_samples - 1)


def _reached_diagnostic_limit(
    *, training_limit: int, planned_samples: int, samples_seen: int
) -> bool:
    """Return true only for an explicit cap shorter than the natural plan."""
    return training_limit < planned_samples and samples_seen >= training_limit


def _chunk_training_refs(
    plan: EpochChunkPlan,
    catalog: Sequence[OfflineBlockRef],
    chunk_index: int,
) -> tuple[OfflineBlockRef, ...]:
    if not 0 <= chunk_index < plan.chunk_count:
        raise IndexError("chunk index is outside the epoch plan")
    try:
        return tuple(
            catalog[index]
            for index in plan.chunks[chunk_index].training_ref_indices
        )
    except IndexError as exc:
        raise RuntimeError("epoch chunk plan references outside the training catalog") from exc


def _chunk_repetition_complete(
    *, blocks_seen: int, repetition_start_blocks: int, target_blocks: int
) -> bool:
    if target_blocks <= 0:
        raise ValueError("chunk repetition target_blocks must be positive")
    return blocks_seen - repetition_start_blocks >= target_blocks


def _outer_early_stopping_update(
    *,
    observed_loss: float,
    best_loss: float,
    reference_loss: float,
    epochs_without_improvement: int,
    patience: int,
    minimum_relative_delta: float,
) -> tuple[float, float, int, bool]:
    if not math.isfinite(observed_loss):
        raise FloatingPointError("outer-epoch validation loss is non-finite")
    required = reference_loss * (1.0 - minimum_relative_delta)
    improved = not math.isfinite(reference_loss) or observed_loss < required
    best_loss = min(best_loss, observed_loss)
    if improved:
        reference_loss = observed_loss
        epochs_without_improvement = 0
    else:
        epochs_without_improvement += 1
    stopped = patience > 0 and epochs_without_improvement >= patience
    return best_loss, reference_loss, epochs_without_improvement, stopped


def _validation_milestones(
    *,
    epoch_origins: int,
    runs_per_epoch: int,
    explicit_interval: int,
    initial_samples: int,
) -> tuple[int, ...]:
    """Return local origin milestones, including the epoch-end evaluation.

    A positive explicit interval is the authoritative F2 sample clock. The
    epoch boundary is always included separately for the paired epoch audit.
    """
    if epoch_origins <= 0 or runs_per_epoch <= 0:
        raise ValueError("validation schedule requires positive epoch and run counts")
    if explicit_interval > 0:
        offsets = [
            value
            for value in range(explicit_interval, epoch_origins, explicit_interval)
            if epoch_origins - value >= explicit_interval
        ]
    elif runs_per_epoch == 1:
        offsets = []
    else:
        offsets = [min(initial_samples, epoch_origins)]
        offsets.extend(round(epoch_origins * index / (runs_per_epoch - 1)) for index in range(1, runs_per_epoch))
    offsets.append(epoch_origins)
    return tuple(sorted({min(epoch_origins, max(1, int(value))) for value in offsets}))


def sequential_coverage_counts(
    client: ClickHouseHttpClient,
    config: DataConfig,
    *,
    seed: int = 17,
    tickers: Sequence[str] | None = None,
) -> tuple[int, int, int, dict[str, tuple[int, int]], SequentialBlockPlan]:
    """Return exact epoch totals and ticker-month block plans in dataset order."""
    selected_tickers = tuple(ticker.upper() for ticker in (tickers or config.training_tickers))
    if not selected_tickers:
        raise RuntimeError("sequential coverage requires at least one selected ticker")
    if len(set(selected_tickers)) != len(selected_tickers):
        raise ValueError("sequential coverage tickers must be unique")
    stream = ArrowStreamClient(_stream_config(config))
    lookback_start = (dt.date.fromisoformat(config.start_date) - dt.timedelta(days=14)).isoformat()
    intervals = stream.read_identity_intervals(
        selected_tickers,
        identity_database=config.identity_database,
        interval_table=config.identity_interval_table,
        entity_table=config.identity_entity_table,
        event_table=config.identity_event_table,
        coverage_start=lookback_start,
    )
    subqueries: list[str] = []
    for ticker in selected_tickers:
        predicates = []
        for interval in intervals[ticker]:
            left = max(lookback_start, interval.valid_from)
            right = min(config.validation_start_date, interval.valid_to_exclusive)
            if left < right:
                predicates.append(
                    f"(ticker={sql_string(interval.source_ticker)} "
                    f"AND local_date>=toDate({sql_string(left)}) "
                    f"AND local_date<toDate({sql_string(right)}))"
                )
        if predicates:
            subqueries.append(
                f"SELECT {sql_string(ticker)} AS canonical_ticker, local_date "
                f"FROM {quote_ident(config.database)}.{quote_ident(config.one_second_table)} "
                f"PREWHERE {' OR '.join(predicates)} GROUP BY local_date"
            )
    if not subqueries:
        raise RuntimeError("no point-in-time one-second ranges cover the sequential training population")
    rows = client.query_tsv(
        "SELECT canonical_ticker, toString(local_date) FROM (\n"
        + "\nUNION ALL\n".join(subqueries)
        + "\n) ORDER BY canonical_ticker, local_date FORMAT TSVRaw"
    )
    dates_by_ticker: dict[str, list[dt.date]] = {ticker: [] for ticker in selected_tickers}
    for line in rows.splitlines():
        values = line.split("\t")
        if len(values) == 2:
            dates_by_ticker[values[0]].append(dt.date.fromisoformat(values[1]))

    session_seconds = 16 * 3_600
    sessions = blocks = origins = 0
    unit_plans: dict[str, tuple[int, int]] = {}
    session_plans: list[SequentialSessionPlan] = []
    session_block_starts: list[int] = []
    unit_global_starts: list[int] = []
    unit_block_counts: list[int] = []
    units = month_units(
        config.start_date,
        config.validation_start_date,
        selected_tickers,
        seed=seed,
    )
    for unit_index, unit in enumerate(units):
        left = dt.date.fromisoformat(unit.start_date)
        right = dt.date.fromisoformat(unit.end_date)
        fetch_start = left - dt.timedelta(days=14)
        dates = dates_by_ticker[unit.ticker]
        previous_dates = [day for day in dates if fetch_start <= day < left]
        previous_date = max(previous_dates).isoformat() if previous_dates else None
        unit_sessions = unit_blocks = unit_origins = 0
        unit_global_starts.append(blocks)
        for day in dates:
            if day < left or day >= right:
                continue
            first_origin = 0 if previous_date is not None else int(config.intraday_warmup_bars_1s)
            eligible = max(0, session_seconds - first_origin)
            full, remainder = divmod(eligible, int(config.origin_bars_1s))
            session_blocks = full
            session_origins = full * int(config.origin_bars_1s)
            if remainder >= int(config.min_origins_per_block):
                session_blocks += 1
                session_origins += remainder
            if session_blocks:
                session_block_starts.append(blocks + unit_blocks)
                session_plans.append(
                    SequentialSessionPlan(
                        unit_index=unit_index,
                        ticker=unit.ticker,
                        unit_start_date=unit.start_date,
                        unit_end_date=unit.end_date,
                        local_date=day.isoformat(),
                        prior_date=previous_date,
                        first_origin=first_origin,
                        block_count=session_blocks,
                        unit_block_start=unit_blocks,
                        global_block_start=blocks + unit_blocks,
                    )
                )
            unit_blocks += session_blocks
            unit_origins += session_origins
            unit_sessions += 1
            previous_date = day.isoformat()
        sessions += unit_sessions
        blocks += unit_blocks
        origins += unit_origins
        unit_block_counts.append(unit_blocks)
        unit_plans[f"{unit.ticker}:{left:%Y-%m}"] = (unit_blocks, unit_origins)
    if not sessions or not blocks or not origins:
        raise RuntimeError("sequential coverage query found no trainable one-second sessions")
    block_plan = SequentialBlockPlan(
        sessions=tuple(session_plans),
        session_block_starts=tuple(session_block_starts),
        unit_global_starts=tuple(unit_global_starts),
        unit_block_counts=tuple(unit_block_counts),
        total_blocks=blocks,
        total_origins=origins,
    )
    return sessions, blocks, origins, unit_plans, block_plan


class _DummyDataset(IterableDataset[BarGPTExample]):
    def __init__(self, example: BarGPTExample) -> None:
        self.example = example

    def __iter__(self) -> Iterator[BarGPTExample]:
        while True:
            yield self.example


def _dummy_example(data: DataConfig) -> BarGPTExample:
    length = data.context_bars_1s + data.origin_bars_1s + data.right_support_bars_1s + 10
    raw = torch.zeros((length, len(FEATURE_NAMES)), dtype=torch.float32)
    time_axis = torch.arange(length, dtype=torch.float32)
    for prefix, offset in (("trade", 0.00), ("bid", -0.01), ("ask", 0.01)):
        raw[:, FEATURE_INDEX[f"{prefix}_present"]] = 1
        price = 100.0 + offset + time_axis * 0.0001
        for field in ("open", "high", "low", "close"):
            raw[:, FEATURE_INDEX[f"{prefix}_{field}"]] = price
        raw[:, FEATURE_INDEX[f"{prefix}_high"]] += 0.005
        raw[:, FEATURE_INDEX[f"{prefix}_low"]] -= 0.005
        raw[:, FEATURE_INDEX[f"{prefix}_size_sum"]] = 100
        raw[:, FEATURE_INDEX[f"{prefix}_size_squared_sum"]] = 10_000
        raw[:, FEATURE_INDEX[f"{prefix}_price_size_sum"]] = price * 100
        raw[:, FEATURE_INDEX[f"{prefix}_event_count"]] = 1
    raw[:, FEATURE_INDEX["quote_pair_present"]] = 1
    raw[:, FEATURE_INDEX["quote_pair_count"]] = 1
    raw[:, FEATURE_INDEX["spread_close"]] = 0.02
    raw[:, FEATURE_INDEX["spread_sum"]] = 0.02
    raw[:, FEATURE_INDEX["midpoint_close"]] = 100 + time_axis * 0.0001
    raw[:, FEATURE_INDEX["midpoint_sum"]] = raw[:, FEATURE_INDEX["midpoint_close"]]
    raw[:, FEATURE_INDEX["microprice_close"]] = raw[:, FEATURE_INDEX["midpoint_close"]]
    raw[:, FEATURE_INDEX["microprice_sum"]] = raw[:, FEATURE_INDEX["midpoint_close"]]
    raw[:, FEATURE_INDEX["source_event_count"]] = 3
    starts = 1_700_000_000_000_000 + torch.arange(length, dtype=torch.long) * 1_000_000
    session = BarView(raw, starts, starts + 1_000_000, starts + 1_000_000)
    return next(build_session_examples(
        ticker="DUMMY",
        local_date="2026-01-02",
        session=session,
        daily=None,
        split_actions=(),
        config=data,
    ))


def _loaders(
    config: ExperimentConfig,
    args: argparse.Namespace,
    *,
    resume_cursors: dict[int, CoverageCursor] | None = None,
    sequential_plan: SequentialBlockPlan | None = None,
    validation_plan: SequentialBlockPlan | None = None,
    offline_train_units: Sequence[OfflineShardUnit] = (),
    offline_validation_units: Sequence[OfflineShardUnit] = (),
    offline_manifest: dict[str, Any] | None = None,
    offline_train_refs: Sequence[OfflineBlockRef] = (),
) -> tuple[DataLoader[Any], DataLoader[Any]]:
    if args.dummy_data:
        example = _dummy_example(config.data)
        train_dataset = _DummyDataset(example)
        validation_dataset = _DummyDataset(example)
        common = dict(batch_size=config.data.batch_size, num_workers=0, collate_fn=collate_examples)
        return DataLoader(train_dataset, **common), DataLoader(validation_dataset, **common)
    if args.data_source == "offline":
        if not offline_train_units or not offline_validation_units:
            raise ValueError("offline training requires certified train and validation shard units")
        experiment_manifest = str(getattr(args, "experiment_manifest", ""))
        manifest = offline_manifest or (
            load_discovery_manifest(
                Path(experiment_manifest),
                shard_root=Path(args.offline_shard_root),
                config=config.data,
            )
            if experiment_manifest
            else None
        )
        train_dataset = OfflineShardDataset(
            offline_train_units,
            seed=config.train.seed,
            shuffle_units=True,
            resume_cursors=resume_cursors,
            # Full-catalog mode passes only the active replayable chunk's
            # exact refs. The million-ref catalog remains parent-owned.
            block_refs=(
                tuple(offline_train_refs)
                if config.train.full_chunk_training
                else panel_refs(manifest, "train") if manifest is not None else ()
            ),
            batch_size=config.data.batch_size,
            length_bucket_batches=config.data.offline_length_bucket_batches,
        )
        validation_workers = min(
            DISCOVERY_VALIDATION_WORKERS,
            int(config.data.loader_workers),
            len(config.data.validation_slices),
        ) if manifest is not None else min(
            int(config.data.loader_workers),
            len(config.data.validation_slices),
        )
        validation_data = replace(
            config.data,
            # A model-independent evaluation batch shape makes the bounded
            # monitor prefix identical across model-size comparisons.
            batch_size=8,
            loader_workers=validation_workers,
            worker_prefetch_batches=(1 if manifest is not None else config.data.worker_prefetch_batches),
            offline_length_bucket_batches=1,
            persistent_workers=manifest is not None,
            balance_activity_regimes=False,
        )
        validation_dataset = OfflineShardDataset(
            offline_validation_units,
            seed=config.train.seed,
            shuffle_units=False,
            validation_slices=() if manifest is not None else config.data.validation_slices,
            blocks_per_validation_slice=0 if manifest is not None else config.data.validation_blocks_per_slice,
            block_refs=panel_refs(manifest, "monitor") if manifest is not None else (),
        )
        return (
            make_offline_dataloader(train_dataset, config.data, drop_last=False),
            make_offline_dataloader(
                validation_dataset,
                validation_data,
                drop_last=False,
                persistent_workers=manifest is not None,
            ),
        )
    stream = _stream_config(config.data)
    if validation_plan is None:
        raise ValueError("real training requires the bounded validation block plan")
    # The fixed held-out panel can be prepared once in parallel with training.
    # Use the training batch shape but no more workers than held-out tickers;
    # extra workers would only duplicate causal warmups for this finite panel.
    validation_workers = min(
        int(config.data.loader_workers),
        len({ticker for ticker, _start, _end in config.data.validation_slices}),
    )
    validation_data = replace(
        config.data,
        batch_size=8,
        loader_workers=validation_workers,
        offline_length_bucket_batches=1,
        persistent_workers=False,
        balance_activity_regimes=False,
    )
    validation_dataset = BarGPTSequentialDataset(
        data_config=validation_data,
        stream_config=stream,
        plan=validation_plan,
    )
    validation_loader = make_sequential_dataloader(validation_dataset, validation_data)
    if config.data.coverage_mode == "sequential" and sequential_plan is None:
        raise ValueError("sequential training requires the certified global block plan")
    # Training must use the worker-sharded iterable stream, including the
    # sequential coverage mode.  A map-style DataLoader round-robins blocks
    # across workers and therefore makes several workers warm the same
    # ticker-month.  Here a ticker (and every one of its months) has one
    # deterministic owner, which preserves its rolling 1s cache.
    train_dataset = BarGPTIterableDataset(
        data_config=config.data,
        stream_config=stream,
        split="train",
        seed=config.train.seed,
        resume_cursors=resume_cursors,
    )
    return make_dataloader(train_dataset, config.data, drop_last=True), validation_loader


def _offline_chunk_training_loader(
    config: ExperimentConfig,
    *,
    units: Sequence[OfflineShardUnit],
    refs: Sequence[OfflineBlockRef],
    resume_cursors: dict[int, CoverageCursor] | None,
    stream_epoch: int,
) -> DataLoader[Any]:
    """Create one exact replayable chunk stream without rebuilding validation."""
    if not units or not refs:
        raise ValueError("full chunk training requires units and exact block references")
    dataset = OfflineShardDataset(
        units,
        seed=config.train.seed,
        shuffle_units=True,
        resume_cursors=resume_cursors,
        block_refs=tuple(refs),
        batch_size=config.data.batch_size,
        length_bucket_batches=config.data.offline_length_bucket_batches,
    )
    dataset.epoch = int(stream_epoch)
    return make_offline_dataloader(dataset, config.data, drop_last=False)


def _training_prefetcher(
    loader: DataLoader[Any],
    config: ExperimentConfig,
    device: torch.device,
) -> DeviceBatchPrefetcher:
    host_cache_batches = max(1, math.ceil(config.data.ready_queue_blocks / config.data.batch_size))
    return DeviceBatchPrefetcher(
        loader,
        device,
        enabled=config.train.cuda_prefetch,
        host_cache_batches=host_cache_batches,
    )


def _preserve_training_prefetch_during_validation(loader: DataLoader[Any]) -> bool:
    """Keep immutable offline-shard workers warm across fixed-panel validation."""
    return isinstance(loader.dataset, OfflineShardDataset)


def _amp_dtype(name: str) -> torch.dtype:
    return {"bf16": torch.bfloat16, "fp16": torch.float16, "float32": torch.float32}[name]


def _unwrap(model: torch.nn.Module) -> torch.nn.Module:
    return getattr(model, "_orig_mod", model)


def _mask_inactive_condition_targets(
    batch: BarGPTBatch,
    active_channels: tuple[bool, bool, bool, bool],
) -> None:
    if batch.horizon_mask is None:
        return
    active = torch.as_tensor(
        active_channels,
        dtype=torch.bool,
        device=batch.horizon_mask.device,
    )
    batch.horizon_mask[..., -4:] &= active


def _forward(
    model: torch.nn.Module,
    batch: BarGPTBatch,
    config: ExperimentConfig,
    *,
    collect_target_stats: bool = True,
    horizon_ids: torch.Tensor | None = None,
) -> tuple[Any, BarGPTLoss]:
    if horizon_ids is None:
        horizon_ids = torch.arange(
            len(config.data.horizons_us), device=batch.origin_indices.device
        )
    output = model(
        batch.views,
        timeframe_us=TIMEFRAME_US_BY_NAME,
        pathway_ids=PATHWAY_ID_BY_NAME,
        base_view="1s",
        origin_indices=batch.origin_indices,
        asof_indices=batch.asof_indices,
        view_masks={name: batch.view_mask[name] for name in batch.masked_context_views},
        attention_windows=config.data.attention_window_by_name,
        horizon_ids=horizon_ids,
    )
    _mask_inactive_condition_targets(batch, config.data.condition_target_active)
    return output, compute_loss(
        output,
        batch,
        config.train,
        config.model.quantiles,
        collect_target_stats=collect_target_stats,
    )


def _nonfinite_loss_diagnostic(result: BarGPTLoss, batch: BarGPTBatch) -> str:
    bad_metrics = sorted(
        key for key, value in result.metrics.items() if not bool(torch.isfinite(value).all())
    )
    bad_views = sorted(
        name for name, value in batch.views.items() if not bool(torch.isfinite(value).all())
    )
    target_details: list[str] = []
    if batch.horizon_targets is not None and batch.horizon_mask is not None:
        invalid = torch.nonzero(
            (~torch.isfinite(batch.horizon_targets)) & batch.horizon_mask,
            as_tuple=False,
        )[:4].detach().cpu().tolist()
        for batch_index, origin_index, horizon_index, target_index in invalid:
            target_details.append(
                f"{batch.tickers[batch_index]}:{batch.local_dates[batch_index]} "
                f"origin={origin_index} horizon={int(batch.horizons_us[horizon_index]) // 1_000_000}s "
                f"target={TARGET_NAMES[target_index]}"
            )
    parts = [f"nonfinite_metrics={bad_metrics or ['unknown']}"]
    if bad_views:
        parts.append(f"nonfinite_views={bad_views}")
    if target_details:
        parts.append("nonfinite_valid_targets=" + "; ".join(target_details))
    parts.append(f"batch={list(zip(batch.tickers, batch.local_dates, strict=True))}")
    return " | ".join(parts)


def _finite_check_vector(result: BarGPTLoss, batch: BarGPTBatch) -> tuple[torch.Tensor, tuple[str, ...]]:
    """Check the optimized scalar without rescanning every certified target.

    Every objective replaces masked targets before arithmetic and the total
    loss includes every active target. A non-finite contributing target must
    therefore make this scalar non-finite. Scanning the full horizon tensor a
    second time was redundant memory-bandwidth work on every microbatch.
    """
    del batch
    return torch.isfinite(result.loss.detach()).reshape(1), ("loss",)


def _assert_finite_before_step(
    checks: list[torch.Tensor],
    names: tuple[str, ...],
    batches: list[tuple[tuple[str, ...], tuple[str, ...]]],
    *,
    device: torch.device,
) -> None:
    """Fail before an optimizer update without synchronizing normal CUDA work.

    ``torch._assert_async`` is ordered before the optimizer kernels on the
    default CUDA stream.  A non-finite loss therefore aborts that stream before
    parameters can be updated, while a healthy run avoids a host round trip on
    every update.  CPU retains the detailed synchronous diagnostic.
    """
    matrix = torch.stack(checks).detach()
    if device.type == "cuda" and hasattr(torch, "_assert_async"):
        torch._assert_async(matrix.all(), "BarGPT encountered a non-finite training value before optimizer step")
        return
    finite_matrix = matrix.cpu()
    if bool(finite_matrix.all()):
        return
    bad_rows = torch.nonzero(~finite_matrix, as_tuple=False).tolist()
    details = "; ".join(
        f"micro={row + 1} field={names[column]} "
        f"batch={tuple(zip(*batches[row], strict=True))!r}"
        for row, column in bad_rows[:8]
    )
    raise FloatingPointError(f"non-finite training values before optimizer update: {details}")


def _batch_eligibility_metrics(batch: BarGPTBatch) -> dict[str, torch.Tensor]:
    """Expose context availability separately from event-timed AR supervision."""
    result: dict[str, torch.Tensor] = {}
    if (
        batch.horizon_targets is not None
        and batch.horizon_mask is not None
        and any(batch.condition_blocks)
    ):
        condition_target = batch.horizon_targets[..., -4:]
        condition_mask = batch.horizon_mask[..., -4:]
        valid = condition_mask.sum().clamp_min(1)
        result["train/condition_positive_rate"] = (
            ((condition_target > 0) & condition_mask).sum().float() / valid
        )
    return result


def _wandb_metric_key(key: str) -> str:
    """Put the semantic metric category at W&B's first grouping level."""
    if key == "train/loss":
        return "train_loss/total"
    if key.startswith("train/loss_"):
        leaf = key.removeprefix("train/loss_")
        return (
            "train_loss_ar_views/" + leaf
            if leaf.removeprefix("ar_") in AUTOREGRESSIVE_VIEW_NAMES
            else "train_loss/" + leaf
        )
    if key == "val/loss":
        return "validation_loss/total"
    if key.startswith("val/loss_"):
        return "validation_loss/" + key.removeprefix("val/loss_")
    if key.startswith("train/loader_stage_"):
        return "train_runtime_loader/" + key.removeprefix("train/loader_stage_")
    train_groups = {
        "samples_seen": "train_progress/origins_seen",
        "batches_seen": "train_progress/microbatches_seen",
        "optimizer_steps": "train_progress/optimizer_steps",
        "blocks_seen": "train_progress/blocks_seen",
        "units_seen": "train_progress/units_seen",
        "condition_blocks_seen": "train_progress/condition_blocks_seen",
        "accumulation_microbatches": "train_optimization/accumulation_microbatches",
        "learning_rate": "train_optimization/learning_rate",
        "epoch_peak_learning_rate": "train_optimization/epoch_peak_learning_rate",
        "chunk_cosine_progress": "train_optimization/chunk_cosine_progress",
        "gradient_norm": "train_optimization/gradient_norm",
        "amp_scale": "train_optimization/amp_scale",
        "loader_wait_seconds": "train_runtime/loader_wait_seconds",
        "gpu_seconds": "train_runtime/gpu_seconds",
        "gpu_duty_cycle": "train_runtime/gpu_duty_cycle",
        "host_cache_batches": "train_runtime/host_cache_batches",
        "host_cache_capacity": "train_runtime/host_cache_capacity",
        "origins_per_second": "train_runtime/origins_per_second",
        "update_wall_seconds": "train_runtime/update_wall_seconds",
        "host_cache_empty_reads": "train_runtime_loader/host_cache_empty_reads",
        "device_stage_empty_waits": "train_runtime_loader/device_stage_empty_waits",
        "device_staged_batches": "train_runtime_loader/device_staged_batches",
        "h2d_completed_batches": "train_runtime_loader/h2d_completed_batches",
        "h2d_seconds": "train_runtime_loader/h2d_seconds",
        "condition_positive_rate": "train_data/condition_positive_rate",
        "outer_epoch": "train_progress/outer_epoch",
        "chunk_index": "train_progress/chunk_index",
        "chunks_completed": "train_progress/chunks_completed",
        "chunk_origins_seen": "train_progress/chunk_origins_seen",
        "chunk_blocks_seen": "train_progress/chunk_blocks_seen",
        "chunk_epoch": "train_progress/chunk_epoch",
        "chunk_epoch_origins_seen": "train_progress/chunk_epoch_origins_seen",
        "chunk_epoch_blocks_seen": "train_progress/chunk_epoch_blocks_seen",
    }
    if key.startswith("train/"):
        leaf = key.removeprefix("train/")
        return train_groups.get(leaf, f"train_misc/{leaf}")
    return key


@dataclass(slots=True)
class _DeferredUpdateLossBuffer:
    names: tuple[str, ...] = ()
    loss_sums: torch.Tensor | None = None
    origins: int = 0
    gradient_norm: torch.Tensor | None = None
    step: int = 0
    metadata: dict[str, float] = field(default_factory=dict)

    def append(
        self,
        metrics: dict[str, torch.Tensor],
        *,
        origins: int,
        step: int,
        metadata: dict[str, float],
    ) -> None:
        names = tuple(
            key
            for key in metrics
            if key == "train/loss" or key.startswith("train/loss_") or key == "train/gradient_norm"
        )
        if self.names and names != self.names:
            raise RuntimeError(f"training loss schema changed: expected={self.names}, actual={names}")
        self.names = names
        loss_names = tuple(name for name in names if name != "train/gradient_norm")
        values = torch.stack([metrics[name].detach() for name in loss_names])
        self.loss_sums = values if self.loss_sums is None else self.loss_sums + values
        self.origins += int(origins)
        self.gradient_norm = metrics["train/gradient_norm"].detach()
        self.step = int(step)
        self.metadata = dict(metadata)

    def flush(self, logger: AsyncJsonlMetricLogger) -> None:
        if self.loss_sums is None:
            return
        loss_names = tuple(name for name in self.names if name != "train/gradient_norm")
        assert self.gradient_norm is not None
        row = torch.cat(
            (
                self.loss_sums / max(1, self.origins),
                self.gradient_norm.reshape(1),
            )
        ).cpu().tolist()
        logger.log(
            {
                **{
                    name: float(value)
                    for name, value in zip(
                        (*loss_names, "train/gradient_norm"), row, strict=True
                    )
                },
                **self.metadata,
            },
            self.step,
        )
        self.loss_sums = None
        self.origins = 0
        self.gradient_norm = None
        self.step = 0
        self.metadata.clear()

    def merge_last(self, metrics: dict[str, float]) -> bool:
        if self.loss_sums is None:
            return False
        self.metadata.update(metrics)
        return True


class PreparedValidationBatches:
    """Materialize the fixed held-out panel once while training begins."""

    def __init__(self, loader: DataLoader[Any]) -> None:
        # Construct the DataLoader iterator on the caller thread so worker
        # process creation is safe on Windows; consume it in a background
        # thread while training workers fetch their own ticker shards.
        self._iterator = iter(loader)
        self._ready = threading.Event()
        self._batches: tuple[BarGPTBatch, ...] = ()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(
            target=self._materialize,
            name="bar-gpt-validation-cache",
            daemon=True,
        )
        self._thread.start()

    def _materialize(self) -> None:
        try:
            self._batches = tuple(self._iterator)
            if not self._batches:
                raise RuntimeError("fixed validation panel produced no batches")
        except BaseException as exc:
            self._failure = exc
        finally:
            shutdown = getattr(self._iterator, "_shutdown_workers", None)
            if callable(shutdown):
                shutdown()
            self._ready.set()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def batch_count(self) -> int:
        return len(self._batches) if self._ready.is_set() and self._failure is None else 0

    def __iter__(self) -> Iterator[BarGPTBatch]:
        self._ready.wait()
        if self._failure is not None:
            raise RuntimeError("fixed validation panel preparation failed") from self._failure
        return iter(self._batches)

    def close(self) -> None:
        if not self._ready.is_set():
            shutdown = getattr(self._iterator, "_shutdown_workers", None)
            if callable(shutdown):
                shutdown()
        self._thread.join(timeout=30.0)


class ReusableValidationBatches:
    """Reiterate a fixed panel without retaining its collated batches in RAM."""

    def __init__(self, loader: DataLoader[Any]) -> None:
        self._loader = loader

    @property
    def ready(self) -> bool:
        # Discovery validation is intentionally lazy: worker creation and the
        # bounded prefetch queue begin only when an evaluation is due.
        return True

    @property
    def batch_count(self) -> int:
        return 0

    def __iter__(self) -> Iterator[BarGPTBatch]:
        return iter(self._loader)

    def close(self) -> None:
        iterator = getattr(self._loader, "_iterator", None)
        shutdown = getattr(iterator, "_shutdown_workers", None)
        if callable(shutdown):
            shutdown()
        if hasattr(self._loader, "_iterator"):
            self._loader._iterator = None


def _offline_reference_evaluation_cache(
    config: ExperimentConfig,
    *,
    units: Sequence[OfflineShardUnit],
    refs: Sequence[OfflineBlockRef],
    seed: int,
) -> ReusableValidationBatches:
    """Build one bounded evaluation stream from already-planned block refs."""
    if not refs:
        raise ValueError("offline reference evaluation requires at least one block")
    evaluation_data = replace(
        config.data,
        batch_size=8,
        loader_workers=min(4, int(config.data.loader_workers)),
        worker_prefetch_batches=1,
        persistent_workers=False,
        balance_activity_regimes=False,
        offline_length_bucket_batches=1,
    )
    dataset = OfflineShardDataset(
        units,
        seed=int(seed),
        shuffle_units=False,
        block_refs=tuple(refs),
        batch_size=evaluation_data.batch_size,
        length_bucket_batches=1,
    )
    return ReusableValidationBatches(
        make_offline_dataloader(
            dataset,
            evaluation_data,
            drop_last=False,
            persistent_workers=False,
        )
    )


def _epoch_training_evaluation_loader(
    config: ExperimentConfig,
    args: argparse.Namespace,
    *,
    offline_train_units: Sequence[OfflineShardUnit],
    manifest: dict[str, Any] | None,
) -> ReusableValidationBatches | None:
    """Build a deterministic, fixed-shape training-population evaluator.

    Production BarGPT training is offline. A constant evaluation batch shape
    makes the selected prefix independent of each model's optimized training
    microbatch. Direct ClickHouse training remains available, but cannot claim
    the fixed offline epoch-training panel and therefore returns ``None``.
    """
    if args.dummy_data:
        example = _dummy_example(config.data)
        loader = DataLoader(
            _DummyDataset(example),
            batch_size=1,
            num_workers=0,
            collate_fn=collate_examples,
        )
        return ReusableValidationBatches(loader)
    if args.data_source != "offline":
        return None
    evaluation_data = replace(
        config.data,
        batch_size=8,
        loader_workers=min(4, int(config.data.loader_workers)),
        worker_prefetch_batches=1,
        persistent_workers=True,
        balance_activity_regimes=False,
        offline_length_bucket_batches=1,
    )
    dataset = OfflineShardDataset(
        offline_train_units,
        seed=config.train.seed,
        shuffle_units=False,
        block_refs=(
            panel_refs(
                manifest,
                "epoch_train" if config.train.full_chunk_training else "train",
            )
            if manifest is not None
            else ()
        ),
        batch_size=evaluation_data.batch_size,
        length_bucket_batches=1,
    )
    return ReusableValidationBatches(
        make_offline_dataloader(
            dataset,
            evaluation_data,
            drop_last=False,
            persistent_workers=True,
        )
    )


@torch.no_grad()
def validate(
    model: torch.nn.Module,
    loader: Iterable[BarGPTBatch],
    config: ExperimentConfig,
    device: torch.device,
    *,
    namespace: str = "validation",
    max_batches: int | None = None,
    max_origins: int | None = None,
) -> dict[str, float]:
    model.eval()
    accumulator = ValidationAccumulator(
        config.data.horizons_us,
        config.model.quantiles,
        namespace=namespace,
    )
    iterator = DeviceBatchPrefetcher(
        loader,
        device,
        enabled=config.train.cuda_prefetch,
        close_iterator=not isinstance(loader, ReusableValidationBatches),
    )
    horizon_ids = torch.arange(len(config.data.horizons_us), device=device)
    try:
        completed = 0
        while (max_batches is None or completed < max_batches) and (
            max_origins is None or accumulator.origins < max_origins
        ):
            try:
                batch = next(iterator)
            except StopIteration:
                break
            with torch.autocast(device_type=device.type, dtype=_amp_dtype(config.train.amp_dtype), enabled=config.train.amp and device.type == "cuda"):
                output, result = _forward(
                    model,
                    batch,
                    config,
                    horizon_ids=horizon_ids,
                )
            accumulator.update(output, batch, result)
            completed += 1
    finally:
        iterator.close()
    model.train()
    return accumulator.finalize()


def _generalization_gap_metrics(
    train_metrics: dict[str, float], validation_metrics: dict[str, float]
) -> dict[str, float]:
    """Return positive degradation gaps for the epoch scorecard."""
    pairs = {
        "loss_total": ("loss/total", False),
        "trade_mae_bps": ("trade_summary/mae_bps_macro", False),
        "close_balanced_accuracy": (
            "close_return_class_summary/balanced_accuracy_macro",
            True,
        ),
        "close_mcc": ("close_return_class_summary/mcc_macro", True),
        "ar_close_balanced_accuracy": (
            "ar_close_return_class_summary/balanced_accuracy_macro",
            True,
        ),
        "ar_close_mcc": ("ar_close_return_class_summary/mcc_macro", True),
    }
    result: dict[str, float] = {}
    for name, (suffix, higher_is_better) in pairs.items():
        train_value = train_metrics.get(f"epoch_train_{suffix}")
        validation_value = validation_metrics.get(f"validation_{suffix}")
        if train_value is None or validation_value is None:
            continue
        result[f"epoch_generalization_gap/{name}"] = (
            float(train_value) - float(validation_value)
            if higher_is_better
            else float(validation_value) - float(train_value)
        )
    return result


def _epoch_boundary_evaluation_namespace(
    *, epoch: int, epochs: int, full_validation_final_epoch_only: bool
) -> str:
    """Select the bounded or complete evaluation contract at an epoch boundary."""
    if epoch <= 0 or epochs <= 0 or epoch > epochs:
        raise ValueError("epoch boundary requires 1 <= epoch <= epochs")
    if full_validation_final_epoch_only and epoch < epochs:
        return "monitor"
    return "validation"


def checkpoint_payload(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    scheduler: TrainingScheduler,
    checkpointer: AsyncCheckpointManager,
    config: ExperimentConfig,
    *,
    samples_seen: int,
    batches_seen: int,
    optimizer_steps: int,
    blocks_seen: int,
    units_seen: set[str],
    condition_blocks_seen: int,
    epoch: int,
    epoch_start_samples: int,
    data_cursors: dict[int, CoverageCursor],
    plan_hash: str,
    last_checkpoint_samples: int,
    validation_evaluations_completed: int,
    wandb_run_id: str | None,
    validation_runs_in_epoch: int = 0,
    last_validation_samples: int = -1,
    last_full_validation_samples: int = -1,
    epoch_train_metrics: dict[str, float] | None = None,
    epoch_validation_metrics: dict[str, float] | None = None,
    generalization_gap_metrics: dict[str, float] | None = None,
    full_chunk_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "learning_contract": LEARNING_CONTRACT,
        "model": _unwrap(model).state_dict(),
        "optimizer": optimizer.state_dict(),
        "scaler": scaler.state_dict(),
        "scheduler": scheduler.state_dict(),
        "checkpointer": checkpointer.state_dict(),
        "config": to_dict(config),
        "samples_seen": samples_seen,
        "batches_seen": batches_seen,
        "optimizer_steps": optimizer_steps,
        "blocks_seen": blocks_seen,
        "units_seen": sorted(units_seen),
        "condition_blocks_seen": condition_blocks_seen,
        "epoch": epoch,
        "epoch_start_samples": epoch_start_samples,
        "data_cursors": {str(worker): asdict(cursor) for worker, cursor in data_cursors.items()},
        "plan_hash": plan_hash,
        "last_checkpoint_samples": last_checkpoint_samples,
        "validation_evaluations_completed": validation_evaluations_completed,
        "validation_runs_in_epoch": validation_runs_in_epoch,
        "last_validation_samples": last_validation_samples,
        "last_full_validation_samples": last_full_validation_samples,
        "epoch_train_metrics": dict(epoch_train_metrics or {}),
        "epoch_validation_metrics": dict(epoch_validation_metrics or {}),
        "generalization_gap_metrics": dict(generalization_gap_metrics or {}),
        "full_chunk_state": dict(full_chunk_state or {}),
        "wandb_run_id": wandb_run_id,
        # Raw cache tensors are intentionally not checkpointed: they can be
        # gigabytes per worker and are deterministically rebuilt from these
        # committed cursors plus the causal warmup contract on resume.
        "data_stream_state": {
            "loader_stream_contract_version": config.data.loader_stream_contract_version,
            "cache_restore": "rehydrate_from_committed_worker_cursors",
            "intraday_warmup_bars_1s": config.data.intraday_warmup_bars_1s,
            "calendar_warmup_daily_bars": config.data.calendar_warmup_daily_bars,
        },
        "rng": {
            "python": random.getstate(),
            "numpy": np.random.get_state(),
            "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        },
    }


def restore_checkpoint(
    path: str,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    scheduler: TrainingScheduler,
    device: torch.device,
    config: ExperimentConfig,
    plan_hash: str,
) -> dict[str, Any]:
    if not path:
        return {
            "samples_seen": 0, "batches_seen": 0, "optimizer_steps": 0,
            "blocks_seen": 0, "units_seen": [], "condition_blocks_seen": 0,
            "epoch": 0, "data_cursors": {}, "checkpointer": {}, "wandb_run_id": None,
        }
    payload = torch.load(path, map_location=device, weights_only=False)
    assert_checkpoint_version(payload)
    saved_config = payload.get("config", {})
    current = to_dict(config)
    saved_data = _resume_data_contract(saved_config.get("data", {}))
    current_data = _resume_data_contract(current.get("data", {}))
    if saved_config.get("model") != current.get("model") or saved_data != current_data:
        raise RuntimeError("resume checkpoint model/data contract does not match the requested run")
    if payload.get("plan_hash") != plan_hash:
        raise RuntimeError("resume checkpoint coverage plan does not match the requested run")
    stream_state = payload.get("data_stream_state", {})
    if stream_state.get("cache_restore") != "rehydrate_from_committed_worker_cursors":
        raise RuntimeError("resume checkpoint does not contain the worker-owned data-stream state")
    _unwrap(model).load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    scaler.load_state_dict(payload.get("scaler", {}))
    scheduler.load_state_dict(payload.get("scheduler"))
    rng = payload.get("rng", {})
    if rng:
        random.setstate(rng["python"])
        np.random.set_state(rng["numpy"])
        cpu_rng = rng["torch"].detach().to(device="cpu", dtype=torch.uint8).contiguous()
        torch.set_rng_state(cpu_rng)
        if torch.cuda.is_available() and rng.get("cuda"):
            torch.cuda.set_rng_state_all(
                [value.detach().to(device="cpu", dtype=torch.uint8).contiguous() for value in rng["cuda"]]
            )
    return payload


def _resume_data_contract(data: dict[str, Any]) -> dict[str, Any]:
    contract = {key: value for key, value in data.items() if key not in _RESUME_RUNTIME_DATA_FIELDS}
    # Checkpoints written before the worker-owned iterable stream had no
    # version field and carried one global map-loader cursor.  Interpret those
    # explicitly as v1 so later stream contracts fail closed rather than
    # replaying/mixing worker shards under incompatible cursor semantics.
    contract.setdefault("loader_stream_contract_version", 1)
    return contract


def _cursor_map(values: dict[str, Any] | None) -> dict[int, CoverageCursor]:
    return {
        int(worker): CoverageCursor(unit_index=int(cursor["unit_index"]), block_offset=int(cursor["block_offset"]))
        for worker, cursor in (values or {}).items()
    }


def _advance_cursors(
    cursors: dict[int, CoverageCursor],
    batch: BarGPTBatch,
    sequential_plan: SequentialBlockPlan | None = None,
    *,
    latest_per_worker: bool = False,
) -> dict[int, CoverageCursor]:
    if sequential_plan is not None:
        current = cursors.get(0)
        expected = 0 if current is None else sequential_plan.cursor_global_index(current) + 1
        for worker, unit, block in zip(
            batch.worker_ids, batch.unit_indices, batch.block_offsets, strict=True
        ):
            if int(worker) != 0:
                raise RuntimeError("ordered sequential training requires one logical global cursor")
            candidate = CoverageCursor(int(unit), int(block))
            actual = sequential_plan.cursor_global_index(candidate)
            if actual != expected:
                raise RuntimeError(
                    f"sequential block order violation: expected global block {expected:,}, got {actual:,} "
                    f"(unit={candidate.unit_index}, block={candidate.block_offset})"
                )
            current = candidate
            expected += 1
        return {0: current} if current is not None else dict(cursors)
    updated = dict(cursors)
    for worker, unit, block in zip(batch.worker_ids, batch.unit_indices, batch.block_offsets, strict=True):
        candidate = CoverageCursor(int(unit), int(block))
        current = updated.get(int(worker))
        if latest_per_worker or current is None or (candidate.unit_index, candidate.block_offset) > (current.unit_index, current.block_offset):
            updated[int(worker)] = candidate
    return updated


def _checkpoint_policy(config: TrainConfig) -> CheckpointPolicy:
    return CheckpointPolicy(
        # The trainer owns validation cadence. Every call represents one
        # already-selected evaluation boundary.
        latest_steps=1,
        archive_steps=0,
        # Training loss is non-stationary across ticker and session regimes and
        # is not a valid selection criterion. Saving every new minimum also
        # synchronously clones the full state out of the training thread.
        save_best_train=False,
        save_best_val=True,
        monitor_train_key="train/loss",
        monitor_val_key="validation_loss/total",
        threshold_intervals=True,
        # The final forced save must queue behind an in-flight best/latest save;
        # skipping it would lose the newest durable data cursor on shutdown.
        skip_latest_if_busy=False,
        clock_name="validation_evaluation",
        archive_prefix="checkpoint_validation",
        archive_on_force=False,
    )


def _validation_checkpoint_due(completed: int, frequency: int) -> bool:
    if completed < 0 or frequency <= 0:
        raise ValueError("validation checkpoint clock requires non-negative progress and positive frequency")
    return completed > 0 and completed % frequency == 0


def main(argv: Iterable[str] | None = None) -> int:
    global _INTERRUPTED
    _INTERRUPTED = False
    signal.signal(signal.SIGINT, _handle_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_interrupt)
    args = parse_args(argv)
    # Offline-shard training does not need a ClickHouse client, but it still
    # needs runtime credentials such as WANDB_API_KEY from the workstation
    # secrets authority. Discovery prints paths only; secret values are never
    # logged or copied into manifests.
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    config = build_config(args)
    if config.train.full_chunk_training and (
        args.data_source != "offline" or not str(args.experiment_manifest)
    ):
        raise ValueError(
            "full chunk training requires offline shards and a full-catalog experiment manifest"
        )
    set_seed(config.train.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        torch.cuda.manual_seed_all(config.train.seed)
        torch.set_float32_matmul_precision("high")
    clickhouse_client: ClickHouseHttpClient | None = None
    offline_train_units: tuple[OfflineShardUnit, ...] = ()
    offline_validation_units: tuple[OfflineShardUnit, ...] = ()
    discovery_manifest: dict[str, Any] | None = None
    if args.dummy_data:
        evidence = {"mode": "dummy"}
    elif args.data_source == "offline":
        root = Path(args.offline_shard_root)
        verify_shard_catalog_lock(root)
        if args.experiment_manifest:
            discovery_manifest = (
                load_full_chunk_manifest(
                    Path(args.experiment_manifest),
                    shard_root=root,
                    config=config.data,
                )
                if config.train.full_chunk_training
                else load_discovery_manifest(
                    Path(args.experiment_manifest),
                    shard_root=root,
                    config=config.data,
                )
            )
            if config.train.full_chunk_training:
                training_tickers = tuple(
                    str(value)
                    for value in discovery_manifest["cohorts"]["training_tickers"]
                )
                validation_tickers = tuple(
                    str(value)
                    for value in discovery_manifest["cohorts"]["evaluation_tickers"]
                )
            else:
                manifest_train_refs = panel_refs(discovery_manifest, "train")
                manifest_evaluation_refs = (
                    *panel_refs(discovery_manifest, "monitor"),
                    *panel_refs(discovery_manifest, "validation"),
                )
                training_tickers = tuple(sorted({ref.ticker for ref in manifest_train_refs}))
                validation_tickers = tuple(sorted({ref.ticker for ref in manifest_evaluation_refs}))
        else:
            training_tickers = tuple(config.data.training_tickers)
            validation_tickers = tuple(
                sorted({ticker for ticker, _start, _end in config.data.validation_slices})
            )
        offline_train_units = discover_offline_units(
            root,
            config.data,
            tickers=training_tickers,
            start_date=str(args.offline_train_start_date),
            end_date=str(args.offline_train_end_date),
        )
        offline_validation_units = discover_offline_units(
            root,
            config.data,
            tickers=validation_tickers,
            start_date=str(args.offline_validation_start_date),
            end_date=str(args.offline_validation_end_date),
        )
        condition_counts = tuple(
            sum(unit.condition_positive_counts[index] for unit in offline_train_units)
            for index in range(4)
        )
        evidence = {
            "mode": "offline_shards_v12",
            "offline_shard_root": str(root),
            "offline_training_units": str(len(offline_train_units)),
            "offline_training_blocks": str(sum(unit.blocks for unit in offline_train_units)),
            "offline_training_origins": str(sum(unit.origins for unit in offline_train_units)),
            "offline_validation_units": str(len(offline_validation_units)),
            "offline_validation_available_blocks": str(sum(unit.blocks for unit in offline_validation_units)),
            "condition_halt_rows": str(condition_counts[0]),
            "condition_resume_rows": str(condition_counts[1]),
            "condition_news_rows": str(condition_counts[2]),
            "condition_luld_rows": str(condition_counts[3]),
        }
    else:
        clickhouse_client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
        )
        evidence = preflight(clickhouse_client, config.data)
    condition_evidence = (
        ("halt_pause", "condition_halt_rows"),
        ("resume", "condition_resume_rows"),
        ("news_risk", "condition_news_rows"),
        ("luld_limit_state", "condition_luld_rows"),
    )
    if not args.dummy_data:
        config.data.condition_target_active = tuple(
            int(evidence.get(evidence_key, "0")) > 0
            for _name, evidence_key in condition_evidence
        )
    evidence["condition_active_targets"] = ",".join(
        name
        for (name, _evidence_key), active in zip(
            condition_evidence, config.data.condition_target_active, strict=True
        )
        if active
    ) or "none"
    evidence["condition_inactive_targets"] = ",".join(
        name
        for (name, _evidence_key), active in zip(
            condition_evidence, config.data.condition_target_active, strict=True
        )
        if not active
    ) or "none"
    sequential_sessions = sequential_blocks = sequential_origins = 0
    sequential_unit_plans: dict[str, tuple[int, int]] = {}
    sequential_block_plan: SequentialBlockPlan | None = None
    bounded_validation_plan: SequentialBlockPlan | None = None
    if not args.dummy_data and args.data_source == "offline":
        if discovery_manifest is not None:
            if config.train.full_chunk_training:
                summary = discovery_manifest["summaries"]["train"]
                sequential_sessions = int(summary["ticker_dates"])
                sequential_blocks = int(summary["blocks"])
                sequential_origins = int(summary["origins"])
            else:
                train_refs = panel_refs(discovery_manifest, "train")
                sequential_sessions = len({(ref.ticker, ref.local_date) for ref in train_refs})
                sequential_blocks = len(train_refs)
                sequential_origins = sum(ref.origins for ref in train_refs)
        else:
            sequential_sessions = sum(unit.sessions for unit in offline_train_units)
            sequential_blocks = sum(unit.blocks for unit in offline_train_units)
            sequential_origins = sum(unit.origins for unit in offline_train_units)
        sequential_unit_plans = {
            unit.unit_key: (unit.blocks, unit.origins)
            for unit in offline_train_units
        }
        evidence.update({
            "training_sessions_per_epoch": str(sequential_sessions),
            "training_blocks_per_epoch": str(sequential_blocks),
            "training_origins_per_epoch": str(sequential_origins),
        })
    elif not args.dummy_data and config.data.coverage_mode == "sequential":
        assert clickhouse_client is not None
        (
            sequential_sessions,
            sequential_blocks,
            sequential_origins,
            sequential_unit_plans,
            sequential_block_plan,
        ) = sequential_coverage_counts(clickhouse_client, config.data, seed=config.train.seed)
        evidence.update(
            {
                "training_sessions_per_epoch": str(sequential_sessions),
                "training_blocks_per_epoch": str(sequential_blocks),
                "training_origins_per_epoch": str(sequential_origins),
            }
        )
    if not args.dummy_data and args.data_source == "clickhouse":
        bounded_validation_plan = validation_block_plan(
            data_config=config.data,
            stream_config=_stream_config(config.data),
        )
        evidence["validation_blocks"] = str(bounded_validation_plan.total_blocks)
        evidence["validation_origins"] = str(bounded_validation_plan.total_origins)
    elif not args.dummy_data:
        evidence["validation_blocks"] = str(
            len(config.data.validation_slices) * config.data.validation_blocks_per_slice
        )
        evidence["validation_origins"] = "stored_in_offline_blocks"
    run_name = args.run_name or f"bar-gpt-v2-{time.strftime('%Y%m%d-%H%M%S')}"
    config.train.run_name = run_name
    run_root = Path(config.train.output_root) / run_name if args.output_root else default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, run_name)
    paths = RunPaths.create(run_root)
    if discovery_manifest is not None:
        if config.train.full_chunk_training:
            plan_training_tickers = tuple(
                str(value)
                for value in discovery_manifest["cohorts"]["training_tickers"]
            )
            validation_tickers = tuple(
                str(value)
                for value in discovery_manifest["cohorts"]["evaluation_tickers"]
            )
        else:
            plan_training_tickers = tuple(
                sorted({ref.ticker for ref in panel_refs(discovery_manifest, "train")})
            )
            validation_tickers = tuple(sorted({
                ref.ticker
                for panel_name in ("monitor", "validation")
                for ref in panel_refs(discovery_manifest, panel_name)
            }))
    else:
        plan_training_tickers = tuple(config.data.training_tickers)
        validation_tickers = tuple(
            sorted({ticker for ticker, _start, _end in config.data.validation_slices})
        )
    identity_holdouts = tuple(
        ticker for ticker in config.data.tickers
        if ticker not in set(plan_training_tickers)
    )
    plan = coverage_plan_summary(
        start_date=(str(args.offline_train_start_date) if args.data_source == "offline" else config.data.start_date),
        end_date=(str(args.offline_train_end_date) if args.data_source == "offline" else config.data.validation_start_date),
        training_tickers=plan_training_tickers,
        blocks_per_unit=config.data.coverage_blocks_per_unit,
        origin_bars=config.data.origin_bars_1s,
        epochs=config.train.epochs,
        seed=config.train.seed,
        fetch_candidate_blocks=config.data.origin_fetch_candidate_blocks,
        emit_blocks_per_chunk=config.data.origin_emit_blocks_per_chunk,
        coverage_mode="stratified" if args.dummy_data else config.data.coverage_mode,
        sessions_per_epoch=sequential_sessions,
        sequential_blocks_per_epoch=sequential_blocks,
        sequential_origins_per_epoch=sequential_origins,
    )
    plan_hash = plan.plan_hash
    if discovery_manifest is not None:
        plan_hash = hashlib.sha256(
            f"{plan.plan_hash}|{discovery_manifest['manifest_hash']}".encode("utf-8")
        ).hexdigest()
    planned_samples = plan.expected_origins
    if config.train.full_chunk_training:
        planned_samples *= config.train.max_chunk_epochs
    if args.dummy_data and config.train.max_samples == 0:
        planned_samples = config.data.batch_size * config.data.origin_bars_1s * config.train.gradient_accumulation_steps
    training_limit = config.train.max_samples if config.train.max_samples > 0 else planned_samples
    # A diagnostic/safety cap must not shorten the epoch learning-rate curve.
    schedule_samples = max(2, training_limit if args.dummy_data else planned_samples)
    epoch_plan_origins = max(
        1,
        math.ceil(plan.expected_origins / config.train.epochs)
        * (config.train.max_chunk_epochs if config.train.full_chunk_training else 1),
    )
    validation_milestones = (
        (epoch_plan_origins,)
        if config.train.full_chunk_training
        else _validation_milestones(
            epoch_origins=epoch_plan_origins,
            runs_per_epoch=config.train.validation_runs_per_epoch,
            explicit_interval=config.train.validation_interval_samples,
            initial_samples=config.train.validation_initial_samples,
        )
    )
    validation_interval = validation_milestones[0]
    if config.train.scheduler_mode == "epoch-chunk-cosine" and not config.train.full_chunk_training:
        raise ValueError("epoch-chunk-cosine requires --full-chunk-training")
    (paths.run_root / "config.json").write_text(json.dumps(to_dict(config), indent=2, default=str), encoding="utf-8")
    (paths.run_root / "coverage_plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    model: torch.nn.Module = BarGPTV2(config.model).to(device)
    view_names = tuple(name for name in TIMEFRAME_US_BY_NAME if name not in config.data.calendar_timeframes)
    all_view_names = (*view_names, *config.data.calendar_timeframes)
    def model_artifact_dummy() -> tuple[tuple[Any, ...], dict[str, Any]]:
        batch, length = 1, 8
        views = {name: torch.zeros(batch, length, config.model.feature_dim, device=device) for name in all_view_names}
        asof = {name: torch.full((batch, 1), length - 1, dtype=torch.long, device=device) for name in all_view_names}
        return (), {
            "views": views,
            "timeframe_us": {name: TIMEFRAME_US_BY_NAME[name] for name in all_view_names},
            "pathway_ids": {name: PATHWAY_ID_BY_NAME[name] for name in all_view_names},
            "base_view": "1s",
            "origin_indices": torch.zeros(batch, 1, dtype=torch.long, device=device),
            "asof_indices": asof,
            "attention_windows": config.data.attention_window_by_name,
            "horizon_ids": torch.arange(len(config.data.horizons_us), dtype=torch.long, device=device),
        }
    # Durable architecture evidence is created at run start, before the first
    # optimizer update, so a stopped run remains reviewable.
    write_model_artifacts(
        model=model,
        artifact_dir=paths.artifacts_dir,
        model_config=config.model,
        input_contract={
            "views": {name: ["B", "T_view", len(FEATURE_NAMES)] for name in all_view_names},
            "asof_indices": ["B", "N_views"],
            "origin_indices": ["B", "N_origins"],
            "time_semantics": "explicit bar_start_us, bar_end_us, available_at_us, and session/calendar masks",
        },
        output_contract={
            "embedding": ["B", "N_origins", config.model.d_model],
            "autoregressive": {
                name: ["B", "T_view-1", config.model.autoregressive_target_dim]
                for name in AUTOREGRESSIVE_VIEW_NAMES
            },
            "autoregressive_return_class_logits": {
                name: ["B", "T_view-1", RETURN_TARGET_COUNT, RETURN_CLASS_COUNT]
                for name in AUTOREGRESSIVE_VIEW_NAMES
            },
            "physical_horizon_quantiles": ["B", "N_origins", len(config.data.horizons_us), CONTINUOUS_TARGET_COUNT, len(config.model.quantiles)],
            "physical_horizon_availability_logits": ["B", "N_origins", len(config.data.horizons_us), AVAILABILITY_TARGET_COUNT],
            "physical_horizon_return_class_logits": ["B", "N_origins", len(config.data.horizons_us), RETURN_TARGET_COUNT, RETURN_CLASS_COUNT],
        },
        architecture_mermaid=build_model_mermaid(),
        summary_notes=(
            "Causal decoder-only multiscale bar model. Every origin uses the fixed "
            "multiscale input; physical horizon targets are built from future support "
            "with causal masks. Calendar views are context-only and have no autoregressive heads."
        ),
        dummy_input_factory=model_artifact_dummy,
    )
    if config.train.compile_model and hasattr(torch, "compile"):
        model = torch.compile(model, dynamic=True)
    training_horizon_ids = torch.arange(
        len(config.data.horizons_us), device=device
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay, foreach=device.type == "cuda")
    resolved_warmup_samples = _resolved_warmup_samples(config.train, schedule_samples)
    if config.train.scheduler_mode == "single-cosine":
        scheduler = SampleWarmupCosineScheduler(
            optimizer,
            warmup_samples=resolved_warmup_samples,
            total_samples=schedule_samples,
            minimum_lr=config.train.minimum_learning_rate,
        )
    elif config.train.scheduler_mode == "epoch-chunk-cosine":
        scheduler = EpochChunkCosineScheduler(
            optimizer,
            warmup_samples=resolved_warmup_samples,
            minimum_lr=config.train.minimum_learning_rate,
            epoch_decay=config.train.cosine_restart_decay,
        )
    else:
        scheduler = SampleCosineRestartScheduler(
            optimizer,
            warmup_samples=resolved_warmup_samples,
            cycle_samples=config.train.cosine_cycle_samples,
            minimum_lr=config.train.minimum_learning_rate,
            restart_decay=config.train.cosine_restart_decay,
        )
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and config.train.amp_dtype == "fp16" and device.type == "cuda")
    restored = restore_checkpoint(args.resume_checkpoint, model, optimizer, scaler, scheduler, device, config, plan_hash)
    chunk_planner: ThreadPoolExecutor | None = None
    next_epoch_plan_future: Future[tuple[EpochChunkPlan, float]] | None = None
    current_epoch_chunk_plan: EpochChunkPlan | None = None
    chunk_plan_seconds = 0.0
    full_training_refs: tuple[OfflineBlockRef, ...] = ()
    if config.train.full_chunk_training:
        assert discovery_manifest is not None
        full_training_refs = load_full_training_refs(
            manifest_path=Path(args.experiment_manifest),
            units=offline_train_units,
            manifest=discovery_manifest,
            ticker_order=config.data.tickers,
        )
        monitor_pool_refs = panel_refs(discovery_manifest, "monitor_pool")
        chunk_plan_root = paths.run_root / "chunk_plans"

        def prepare_chunk_plan(epoch: int) -> tuple[EpochChunkPlan, float]:
            started = time.perf_counter()
            output = chunk_plan_root / f"epoch_{epoch + 1:04d}.json"
            if output.is_file():
                prepared = load_epoch_chunk_plan(output)
            else:
                prepared = build_epoch_chunk_plan(
                    epoch=epoch,
                    seed=config.train.seed,
                    training_blocks=sequential_blocks,
                    training_origins=sequential_origins,
                    training_refs=full_training_refs,
                    target_chunk_origins=config.train.chunk_target_origins,
                    validation_origins=config.train.chunk_validation_origins,
                    monitor_pool=monitor_pool_refs,
                )
                write_epoch_chunk_plan(prepared, output)
            if (
                prepared.epoch != epoch
                or prepared.training_blocks != sequential_blocks
                or prepared.training_origins != sequential_origins
                or prepared.target_chunk_origins != config.train.chunk_target_origins
                or prepared.target_validation_origins
                != config.train.chunk_validation_origins
            ):
                raise RuntimeError(
                    f"full-training epoch plan {output} does not match the active coverage contract"
                )
            planned_indices = [
                index
                for chunk in prepared.chunks
                for index in chunk.training_ref_indices
            ]
            if (
                len(planned_indices) != len(full_training_refs)
                or len(set(planned_indices)) != len(full_training_refs)
                or min(planned_indices, default=-1) != 0
                or max(planned_indices, default=-1) != len(full_training_refs) - 1
            ):
                raise RuntimeError(
                    f"full-training epoch plan {output} does not partition the catalog"
                )
            return prepared, time.perf_counter() - started

        current_epoch_chunk_plan, chunk_plan_seconds = prepare_chunk_plan(
            int(restored.get("epoch", 0))
        )
        chunk_planner = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="bar-gpt-next-epoch-plan",
        )
    resume_cursors = _cursor_map(restored.get("data_cursors"))
    train_loader, validation_loader = _loaders(
        config,
        args,
        resume_cursors=resume_cursors,
        sequential_plan=sequential_block_plan,
        validation_plan=bounded_validation_plan,
        offline_train_units=offline_train_units,
        offline_validation_units=offline_validation_units,
        offline_manifest=discovery_manifest,
        offline_train_refs=(
            _chunk_training_refs(
                current_epoch_chunk_plan,
                full_training_refs,
                int((restored.get("full_chunk_state") or {}).get("chunk_index", 0)),
            )
            if current_epoch_chunk_plan is not None
            else ()
        ),
    )
    validation_cache = (
        ReusableValidationBatches(validation_loader)
        if discovery_manifest is not None
        else PreparedValidationBatches(validation_loader)
    )
    full_validation_cache = validation_cache
    if discovery_manifest is not None:
        validation_workers = min(
            DISCOVERY_VALIDATION_WORKERS,
            int(config.data.loader_workers),
            len(config.data.validation_slices),
        )
        validation_data = replace(
            config.data,
            batch_size=8,
            loader_workers=validation_workers,
            worker_prefetch_batches=1,
            offline_length_bucket_batches=1,
            persistent_workers=True,
            balance_activity_regimes=False,
        )
        full_validation_dataset = OfflineShardDataset(
            offline_validation_units,
            seed=config.train.seed,
            shuffle_units=False,
            block_refs=panel_refs(discovery_manifest, "validation"),
        )
        full_validation_cache = ReusableValidationBatches(
            make_offline_dataloader(
                full_validation_dataset,
                validation_data,
                drop_last=False,
                persistent_workers=True,
            )
        )
    epoch_training_cache = _epoch_training_evaluation_loader(
        config,
        args,
        offline_train_units=offline_train_units,
        manifest=discovery_manifest,
    )
    if epoch_training_cache is None:
        raise RuntimeError(
            "paired epoch training evaluation requires the offline or dummy-data path"
        )
    resumed_wandb_id = restored.get("wandb_run_id")
    if args.resume_checkpoint and config.train.wandb_mode != "disabled" and not resumed_wandb_id:
        raise RuntimeError(
            "resume checkpoint has no W&B run id; use --wandb-mode disabled only for an explicit logging reset"
        )
    wandb_run = init_wandb(
        entity=config.train.wandb_entity,
        project=config.train.wandb_project,
        run_name=run_name,
        config=to_dict(config),
        run_dir=paths.wandb_dir,
        mode=config.train.wandb_mode,
        timeout_seconds=config.train.wandb_init_timeout,
        run_id=str(resumed_wandb_id) if resumed_wandb_id else None,
        # Structured metrics and artifacts remain enabled. Only W&B's stdout/
        # stderr interception is disabled because the Rich dashboard redraws
        # continuously and otherwise produces unbounded output.log growth.
        capture_console=False,
    )
    wandb_run_id = str(getattr(wandb_run, "id", "")) or None
    metrics_logger = AsyncJsonlMetricLogger(
        paths.metrics_path,
        wandb_run,
        wandb_key_mapper=_wandb_metric_key,
    )
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config={
            **to_dict(config),
            "learning_contract": LEARNING_CONTRACT,
            "data_evidence": evidence,
            "validation_tickers": validation_tickers,
            "identity_holdout_tickers": identity_holdouts,
            "coverage_plan": plan.to_dict(),
            "resolved_training_limit": training_limit,
            "resolved_validation_interval": validation_interval,
            "resolved_validation_milestones": validation_milestones,
            "resolved_warmup_samples": resolved_warmup_samples,
        },
        data_roots=(
            {
                "offline_shards": str(Path(args.offline_shard_root)),
                "training_range": f"[{args.offline_train_start_date},{args.offline_train_end_date})",
                "validation_range": f"[{args.offline_validation_start_date},{args.offline_validation_end_date})",
            }
            if args.data_source == "offline"
            else {
                "clickhouse": default_clickhouse_url(),
                "database": config.data.database,
                "one_second_table": config.data.one_second_table,
                "one_second_manifest_table": config.data.manifest_table,
                "daily_table": config.data.daily_table,
                "daily_manifest_table": config.data.daily_manifest_table,
                "identity_database": config.data.identity_database,
                "identity_interval_table": config.data.identity_interval_table,
            }
        ),
        output_root=paths.run_root,
        source_checkpoint=Path(args.resume_checkpoint) if args.resume_checkpoint else None,
        wandb_info={
            "project": config.train.wandb_project,
            "entity": config.train.wandb_entity,
            "run_name": run_name,
            "run_id": wandb_run_id,
        },
    )
    checkpointer = AsyncCheckpointManager(
        paths.checkpoints_dir,
        paths.checkpoint_manifest_path,
        _checkpoint_policy(config.train),
    )
    checkpointer.load_state_dict(restored.get("checkpointer"))
    samples_seen = int(restored.get("samples_seen", 0))
    batches_seen = int(restored.get("batches_seen", 0))
    optimizer_steps = int(restored.get("optimizer_steps", 0))
    blocks_seen = int(restored.get("blocks_seen", 0))
    units_seen = set(str(value) for value in restored.get("units_seen", []))
    condition_blocks_seen = int(restored.get("condition_blocks_seen", 0))
    resume_epoch = int(restored.get("epoch", 0))
    restored_chunk_state = dict(restored.get("full_chunk_state") or {})
    epoch_start_blocks = int(
        restored_chunk_state.get("epoch_start_blocks", resume_epoch * sequential_blocks)
    )
    chunk_index = int(restored_chunk_state.get("chunk_index", 0))
    chunk_start_samples = int(
        restored_chunk_state.get("chunk_start_samples", restored.get("epoch_start_samples", 0))
    )
    chunk_start_blocks = int(
        restored_chunk_state.get("chunk_start_blocks", epoch_start_blocks)
    )
    chunk_epoch = int(restored_chunk_state.get("chunk_epoch", 0))
    chunk_epoch_start_samples = int(
        restored_chunk_state.get("chunk_epoch_start_samples", chunk_start_samples)
    )
    chunk_epoch_start_blocks = int(
        restored_chunk_state.get("chunk_epoch_start_blocks", chunk_start_blocks)
    )
    chunk_best_validation_loss = float(
        restored_chunk_state.get("chunk_best_validation_loss", math.inf)
    )
    chunk_validation_reference_loss = float(
        restored_chunk_state.get("chunk_validation_reference_loss", math.inf)
    )
    chunk_epochs_without_improvement = int(
        restored_chunk_state.get("chunk_epochs_without_improvement", 0)
    )
    chunk_ready_to_advance = bool(
        restored_chunk_state.get("chunk_ready_to_advance", False)
    )
    chunk_epoch_validated = bool(
        restored_chunk_state.get("chunk_epoch_validated", False)
    )
    chunks_completed = int(restored_chunk_state.get("chunks_completed", 0))
    best_outer_validation_loss = float(
        restored_chunk_state.get("best_outer_validation_loss", math.inf)
    )
    early_stopping_reference_loss = float(
        restored_chunk_state.get("early_stopping_reference_loss", math.inf)
    )
    outer_epochs_without_improvement = int(
        restored_chunk_state.get("outer_epochs_without_improvement", 0)
    )
    early_stopped = bool(restored_chunk_state.get("early_stopped", False))
    last_checkpoint_samples = int(
        restored.get("last_checkpoint_samples", restored.get("last_latest_samples", 0))
    )
    durable_cursors = dict(resume_cursors)
    epoch_start_samples = int(restored.get("epoch_start_samples", resume_epoch * epoch_plan_origins))
    state = TrainingProgressState(
        run_name=run_name,
        device=str(device),
        precision=config.train.amp_dtype if config.train.amp else "float32",
        output_dir=str(paths.run_root),
        model_parameters=int(parameter_summary(_unwrap(model))["total_parameters"]),
        max_samples=training_limit,
        epochs_total=max(1, config.train.epochs),
        epoch_index=min(max(1, resume_epoch + 1), max(1, config.train.epochs)),
        epoch_start_origins=epoch_start_samples,
        epoch_origin_budget=epoch_plan_origins,
        epoch_origins_seen=max(0, samples_seen - epoch_start_samples),
        samples_seen=samples_seen,
        batches_seen=batches_seen,
        optimizer_steps=optimizer_steps,
        blocks_seen=blocks_seen,
        units_seen=len(units_seen),
        condition_blocks_seen=condition_blocks_seen,
        planned_units=plan.units * config.train.epochs,
        planned_blocks=(
            plan.expected_blocks * config.train.max_chunk_epochs
            if config.train.full_chunk_training
            else plan.expected_blocks
        ),
        gradient_accumulation_steps=config.train.gradient_accumulation_steps,
        cuda_prefetch=config.train.cuda_prefetch and device.type == "cuda",
        origin_bars=config.data.origin_bars_1s,
        warmup_samples=resolved_warmup_samples,
        schedule_samples=schedule_samples,
        scheduler_mode=config.train.scheduler_mode,
        epoch_lr_decay=config.train.cosine_restart_decay,
        epoch_peak_lr=config.train.learning_rate,
        unit_plans=sequential_unit_plans,
        full_chunk_training=config.train.full_chunk_training,
        chunk_index=chunk_index + 1,
        chunk_count=(current_epoch_chunk_plan.chunk_count if current_epoch_chunk_plan else 0),
        chunk_start_origins=chunk_start_samples,
        chunk_origin_budget=(
            current_epoch_chunk_plan.chunks[chunk_index].approximate_target_origins
            * config.train.max_chunk_epochs
            if current_epoch_chunk_plan and chunk_index < current_epoch_chunk_plan.chunk_count
            else 0
        ),
        chunk_start_blocks=chunk_start_blocks,
        chunk_block_budget=(
            current_epoch_chunk_plan.chunks[chunk_index].target_blocks
            * config.train.max_chunk_epochs
            if current_epoch_chunk_plan and chunk_index < current_epoch_chunk_plan.chunk_count
            else 0
        ),
        chunk_planner_seconds=chunk_plan_seconds,
        next_epoch_plan_ready=False,
        chunk_epoch_index=chunk_epoch + 1,
        chunk_epochs_total=config.train.max_chunk_epochs,
        chunk_epoch_start_origins=chunk_epoch_start_samples,
        chunk_epoch_start_blocks=chunk_epoch_start_blocks,
        chunk_best_validation_loss=(
            chunk_best_validation_loss
            if math.isfinite(chunk_best_validation_loss)
            else None
        ),
        chunk_epochs_without_improvement=chunk_epochs_without_improvement,
    )
    reporter = TrainingReporter(state, layout=config.train.progress_layout)
    log_interval = max(1, config.train.logging_samples)
    next_log = ((samples_seen // log_interval) + 1) * log_interval
    metric_interval = max(1, config.train.training_metrics_interval_samples)
    next_training_metrics = ((samples_seen // metric_interval) + 1) * metric_interval
    deferred_losses = _DeferredUpdateLossBuffer()
    restored_validation_runs = restored.get("validation_runs_in_epoch")
    validation_state_missing = restored_validation_runs is None
    validation_runs_in_epoch = int(restored_validation_runs or 0)
    evaluations_per_full_epoch = (
        current_epoch_chunk_plan.chunk_count * config.train.max_chunk_epochs + 1
        if current_epoch_chunk_plan is not None
        else 1
    )
    validation_evaluations_completed = int(
        restored.get(
            "validation_evaluations_completed",
            resume_epoch
            * (
                evaluations_per_full_epoch
                if config.train.full_chunk_training
                else len(validation_milestones)
            )
            + validation_runs_in_epoch,
        )
    )
    epoch_validation_milestones = validation_milestones
    pending_resume_validation = bool(args.resume_checkpoint) and validation_state_missing
    next_validation = (
        samples_seen
        if pending_resume_validation
        else epoch_start_samples + epoch_validation_milestones[min(validation_runs_in_epoch, len(epoch_validation_milestones) - 1)]
    )
    last_validation_samples = int(restored.get("last_validation_samples", -1))
    last_full_validation_samples = int(restored.get("last_full_validation_samples", -1))
    if not pending_resume_validation:
        validation_runs_in_epoch = min(
            validation_runs_in_epoch,
            (
                current_epoch_chunk_plan.chunk_count * config.train.max_chunk_epochs
                if config.train.full_chunk_training
                and current_epoch_chunk_plan is not None
                else max(0, len(epoch_validation_milestones) - 1)
            ),
        )
    state.validation_runs_completed = validation_evaluations_completed
    state.validation_runs_total = (
        max(1, config.train.epochs)
        * (
            (current_epoch_chunk_plan.chunk_count * config.train.max_chunk_epochs + 1)
            if current_epoch_chunk_plan
            else 1
        )
        if config.train.full_chunk_training
        else max(1, config.train.epochs) * len(epoch_validation_milestones)
    )
    state.next_validation_origins = next_validation
    last_metrics: dict[str, float] = {"train/loss": math.inf}
    last_val: dict[str, float] = {}
    last_epoch_train: dict[str, float] = {}
    last_epoch_validation: dict[str, float] = {}
    last_generalization_gap: dict[str, float] = {}
    current_epoch = resume_epoch
    completed_normally = False
    active_iterator: DeviceBatchPrefetcher | None = None

    def full_chunk_checkpoint_state() -> dict[str, Any]:
        if not config.train.full_chunk_training:
            return {}
        return {
            "epoch_start_blocks": int(epoch_start_blocks),
            "chunk_index": int(chunk_index),
            "chunk_start_samples": int(chunk_start_samples),
            "chunk_start_blocks": int(chunk_start_blocks),
            "chunk_epoch": int(chunk_epoch),
            "chunk_epoch_start_samples": int(chunk_epoch_start_samples),
            "chunk_epoch_start_blocks": int(chunk_epoch_start_blocks),
            "chunk_best_validation_loss": float(chunk_best_validation_loss),
            "chunk_validation_reference_loss": float(
                chunk_validation_reference_loss
            ),
            "chunk_epochs_without_improvement": int(
                chunk_epochs_without_improvement
            ),
            "chunk_ready_to_advance": bool(chunk_ready_to_advance),
            "chunk_epoch_validated": bool(chunk_epoch_validated),
            "chunks_completed": int(chunks_completed),
            "epoch_plan_hash": (
                current_epoch_chunk_plan.plan_hash
                if current_epoch_chunk_plan is not None
                and current_epoch_chunk_plan.epoch == current_epoch
                else ""
            ),
            "best_outer_validation_loss": float(best_outer_validation_loss),
            "early_stopping_reference_loss": float(
                early_stopping_reference_loss
            ),
            "outer_epochs_without_improvement": int(outer_epochs_without_improvement),
            "early_stopped": bool(early_stopped),
        }

    def schedule_checkpoint(
        *, force: bool = False, epoch_checkpoint: int | None = None
    ) -> None:
        """Stage one consistent snapshot; the checkpoint worker owns disk I/O."""
        nonlocal last_checkpoint_samples
        snapshot_cursors = dict(durable_cursors)
        staging_started = time.perf_counter()
        queued = checkpointer.maybe_save(
            step=validation_evaluations_completed,
            payload_factory=lambda cursors=snapshot_cursors: checkpoint_payload(
                model, optimizer, scaler, scheduler, checkpointer, config,
                samples_seen=samples_seen, batches_seen=batches_seen,
                optimizer_steps=optimizer_steps, epoch=current_epoch,
                epoch_start_samples=epoch_start_samples,
                blocks_seen=blocks_seen, units_seen=units_seen,
                condition_blocks_seen=condition_blocks_seen,
                data_cursors=cursors, plan_hash=plan_hash,
                last_checkpoint_samples=samples_seen,
                validation_evaluations_completed=validation_evaluations_completed,
                wandb_run_id=wandb_run_id,
                validation_runs_in_epoch=validation_runs_in_epoch,
                last_validation_samples=last_validation_samples,
                last_full_validation_samples=last_full_validation_samples,
                epoch_train_metrics=last_epoch_train,
                epoch_validation_metrics=last_epoch_validation,
                generalization_gap_metrics=last_generalization_gap,
                full_chunk_state=full_chunk_checkpoint_state(),
            ),
            train_metrics=last_metrics,
            val_metrics=last_val,
            force=force,
            named_destinations=(
                ((f"checkpoint_epoch_{epoch_checkpoint:04d}.pt", "epoch_end"),)
                if epoch_checkpoint is not None
                else ()
            ),
        )
        staging_seconds = time.perf_counter() - staging_started
        reporter.state.checkpoint_stage_seconds = staging_seconds
        if queued:
            reporter.message(
                f"Checkpoint snapshot staged in {staging_seconds:.3f}s; background disk write queued"
            )
            last_checkpoint_samples = samples_seen
        else:
            reporter.message(f"Checkpoint request was not queued after {staging_seconds:.3f}s")

    def checkpoint_after_validation() -> None:
        if _validation_checkpoint_due(
            validation_evaluations_completed,
            config.train.checkpoint_validation_evaluations,
        ):
            reporter.phase("checkpointing")
            reporter.message(
                f"Staging checkpoint after validation {validation_evaluations_completed:,}"
            )
            # Force means the bounded writer queues this exact snapshot even
            # if the prior latest-file replacement is still finishing.
            schedule_checkpoint(force=True)
        reporter.phase("running")

    def run_chunk_validation(active_chunk: Any) -> dict[str, float]:
        nonlocal last_val, last_validation_samples, validation_runs_in_epoch
        nonlocal validation_evaluations_completed
        reporter.message(
            f"Evaluating chunk {chunk_index + 1}/{current_epoch_chunk_plan.chunk_count if current_epoch_chunk_plan else 0} "
            f"on fixed chunk validation {active_chunk.validation_hash[:12]}"
        )
        reporter.phase("validating_chunk")
        monitor_cache = _offline_reference_evaluation_cache(
            config,
            units=offline_validation_units,
            refs=active_chunk.validation_refs,
            seed=(
                current_epoch_chunk_plan.shuffle_seed + chunk_index
                if current_epoch_chunk_plan is not None
                else config.train.seed
            ),
        )
        try:
            chunk_metrics = validate(
                model,
                monitor_cache,
                config,
                device,
                namespace="chunk_validation",
                max_batches=None,
                max_origins=None,
            )
        finally:
            monitor_cache.close()
        chunk_metrics.update(
            {
                "chunk/outer_epoch": float(current_epoch + 1),
                "chunk/index": float(chunk_index + 1),
                "chunk/repetition": float(chunk_epoch + 1),
                "chunk/count": float(
                    current_epoch_chunk_plan.chunk_count
                    if current_epoch_chunk_plan is not None
                    else 0
                ),
                "chunk/train_origins": float(samples_seen - chunk_start_samples),
                "chunk/train_blocks": float(blocks_seen - chunk_start_blocks),
                "chunk/repetition_origins": float(
                    samples_seen - chunk_epoch_start_samples
                ),
                "chunk/repetition_blocks": float(
                    blocks_seen - chunk_epoch_start_blocks
                ),
                "chunk/validation_origins": float(active_chunk.validation_origins),
            }
        )
        last_val = chunk_metrics
        if not deferred_losses.merge_last(chunk_metrics):
            metrics_logger.log(chunk_metrics, samples_seen)
        deferred_losses.flush(metrics_logger)
        reporter.chunk_validation(chunk_metrics)
        last_validation_samples = samples_seen
        validation_runs_in_epoch += 1
        validation_evaluations_completed += 1
        return chunk_metrics

    def evaluate_completed_chunk_epoch(active_chunk: Any) -> bool:
        """Evaluate one completed replay and decide whether its chunk stops."""
        nonlocal chunk_best_validation_loss, chunk_validation_reference_loss
        nonlocal chunk_epochs_without_improvement, chunk_ready_to_advance
        nonlocal chunk_epoch_validated
        nonlocal chunks_completed
        chunk_metrics = run_chunk_validation(active_chunk)
        observed_loss = float(chunk_metrics["chunk_validation_loss/total"])
        (
            chunk_best_validation_loss,
            chunk_validation_reference_loss,
            chunk_epochs_without_improvement,
            patience_exhausted,
        ) = _outer_early_stopping_update(
            observed_loss=observed_loss,
            best_loss=chunk_best_validation_loss,
            reference_loss=chunk_validation_reference_loss,
            epochs_without_improvement=chunk_epochs_without_improvement,
            patience=config.train.chunk_early_stopping_patience,
            minimum_relative_delta=(
                config.train.chunk_early_stopping_min_relative_delta
            ),
        )
        completed_repetitions = chunk_epoch + 1
        reached_maximum = completed_repetitions >= config.train.max_chunk_epochs
        chunk_ready_to_advance = bool(patience_exhausted or reached_maximum)
        chunk_epoch_validated = True
        if chunk_ready_to_advance:
            chunks_completed += 1
            state.chunks_completed = chunks_completed
        control_metrics = {
            "chunk_control/best_validation_loss": float(
                chunk_best_validation_loss
            ),
            "chunk_control/repetitions_completed": float(completed_repetitions),
            "chunk_control/epochs_without_improvement": float(
                chunk_epochs_without_improvement
            ),
            "chunk_control/early_stopped": float(patience_exhausted),
            "chunk_control/reached_maximum": float(reached_maximum),
        }
        metrics_logger.log(control_metrics, samples_seen)
        reporter.chunk_epoch(
            index=completed_repetitions,
            total=config.train.max_chunk_epochs,
            start_origins=chunk_epoch_start_samples,
            start_blocks=chunk_epoch_start_blocks,
            best_validation_loss=chunk_best_validation_loss,
            epochs_without_improvement=chunk_epochs_without_improvement,
        )
        reporter.message(
            (
                f"Chunk {chunk_index + 1} stops after {completed_repetitions} repetitions"
                if chunk_ready_to_advance
                else f"Chunk {chunk_index + 1} continues to repetition {completed_repetitions + 1}"
            )
        )
        return chunk_ready_to_advance

    try:
        with reporter:
            checkpointer.set_message_callback(reporter.message)
            reporter.message(
                f"Certified source; plan={plan_hash[:12]} units={plan.units:,} "
                f"blocks={plan.expected_blocks:,}; validation tickers={len(validation_tickers)}; "
                f"identity holdouts={len(identity_holdouts)}"
            )
            reporter.message(
                "Condition targets: active=" + evidence["condition_active_targets"]
                + "; loss-ineligible=" + evidence["condition_inactive_targets"]
            )
            reporter.message(
                "Validation panels use bounded reusable streams"
                if discovery_manifest is not None
                else "Preparing fixed validation panel in parallel with training"
            )
            for epoch in range(resume_epoch, max(1, config.train.epochs)):
                current_epoch = epoch
                if config.train.full_chunk_training:
                    if epoch != resume_epoch:
                        if next_epoch_plan_future is None:
                            raise RuntimeError("next full-training epoch plan was not scheduled")
                        current_epoch_chunk_plan, chunk_plan_seconds = (
                            next_epoch_plan_future.result()
                        )
                        next_epoch_plan_future = None
                        epoch_start_blocks = blocks_seen
                        chunk_index = 0
                        chunk_start_samples = samples_seen
                        chunk_start_blocks = blocks_seen
                        chunk_epoch = 0
                        chunk_epoch_start_samples = samples_seen
                        chunk_epoch_start_blocks = blocks_seen
                        chunk_best_validation_loss = math.inf
                        chunk_validation_reference_loss = math.inf
                        chunk_epochs_without_improvement = 0
                        chunk_ready_to_advance = False
                        chunk_epoch_validated = False
                    if current_epoch_chunk_plan is None:
                        raise RuntimeError("full-training epoch has no chunk plan")
                    restored_plan_hash = str(
                        restored_chunk_state.get("epoch_plan_hash", "")
                    )
                    if (
                        epoch == resume_epoch
                        and restored_plan_hash
                        and restored_plan_hash != current_epoch_chunk_plan.plan_hash
                    ):
                        raise RuntimeError(
                            "resume checkpoint full-training epoch plan does not match "
                            "the durable plan"
                        )
                    state.chunk_planner_seconds = chunk_plan_seconds
                    state.next_epoch_plan_ready = False
                    if (
                        chunk_planner is not None
                        and epoch + 1 < max(1, config.train.epochs)
                    ):
                        next_epoch_plan_future = chunk_planner.submit(
                            prepare_chunk_plan, epoch + 1
                        )
                if epoch != resume_epoch:
                    if config.train.full_chunk_training:
                        assert current_epoch_chunk_plan is not None
                        train_loader = _offline_chunk_training_loader(
                            config,
                            units=offline_train_units,
                            refs=_chunk_training_refs(
                                current_epoch_chunk_plan,
                                full_training_refs,
                                chunk_index,
                            ),
                            resume_cursors={},
                            stream_epoch=(
                                epoch
                                * current_epoch_chunk_plan.chunk_count
                                * config.train.max_chunk_epochs
                            ),
                        )
                    else:
                        train_loader, _unused_validation = _loaders(
                            config,
                            args,
                            resume_cursors={},
                            sequential_plan=sequential_block_plan,
                            validation_plan=bounded_validation_plan,
                            offline_train_units=offline_train_units,
                            offline_validation_units=offline_validation_units,
                            offline_manifest=discovery_manifest,
                        )
                    durable_cursors = {}
                    validation_runs_in_epoch = 0
                    epoch_start_samples = samples_seen
                epoch_validation_milestones = validation_milestones
                if epoch != resume_epoch:
                    next_validation = epoch_start_samples + epoch_validation_milestones[0]
                reporter.schedule_validation(next_validation)
                reporter.epoch(epoch + 1, epoch_start_samples)
                if current_epoch_chunk_plan is not None:
                    if not 0 <= chunk_index < current_epoch_chunk_plan.chunk_count:
                        raise RuntimeError(
                            f"resume chunk index {chunk_index} is outside epoch plan"
                        )
                    active_chunk = current_epoch_chunk_plan.chunks[chunk_index]
                    pending_repetition = _chunk_repetition_complete(
                        blocks_seen=blocks_seen,
                        repetition_start_blocks=chunk_epoch_start_blocks,
                        target_blocks=active_chunk.target_blocks,
                    )
                    if pending_repetition and not chunk_epoch_validated:
                        reporter.message(
                            f"Retrying pending validation for chunk {chunk_index + 1} "
                            f"repetition {chunk_epoch + 1}"
                        )
                        evaluate_completed_chunk_epoch(active_chunk)
                    if pending_repetition and chunk_ready_to_advance and (
                        chunk_index + 1 < current_epoch_chunk_plan.chunk_count
                    ):
                        chunk_index += 1
                        chunk_start_samples = samples_seen
                        chunk_start_blocks = blocks_seen
                        chunk_epoch = 0
                        chunk_epoch_start_samples = samples_seen
                        chunk_epoch_start_blocks = blocks_seen
                        chunk_best_validation_loss = math.inf
                        chunk_validation_reference_loss = math.inf
                        chunk_epochs_without_improvement = 0
                        chunk_ready_to_advance = False
                        chunk_epoch_validated = False
                        durable_cursors = {}
                        active_chunk = current_epoch_chunk_plan.chunks[chunk_index]
                        train_loader = _offline_chunk_training_loader(
                            config,
                            units=offline_train_units,
                            refs=_chunk_training_refs(
                                current_epoch_chunk_plan,
                                full_training_refs,
                                chunk_index,
                            ),
                            resume_cursors={},
                            stream_epoch=(
                                epoch
                                * current_epoch_chunk_plan.chunk_count
                                * config.train.max_chunk_epochs
                                + chunk_index * config.train.max_chunk_epochs
                            ),
                        )
                        checkpoint_after_validation()
                    elif pending_repetition and not chunk_ready_to_advance:
                        chunk_epoch += 1
                        chunk_epoch_validated = False
                        chunk_epoch_start_samples = samples_seen
                        chunk_epoch_start_blocks = blocks_seen
                        durable_cursors = {}
                        train_loader = _offline_chunk_training_loader(
                            config,
                            units=offline_train_units,
                            refs=_chunk_training_refs(
                                current_epoch_chunk_plan,
                                full_training_refs,
                                chunk_index,
                            ),
                            resume_cursors={},
                            stream_epoch=(
                                epoch
                                * current_epoch_chunk_plan.chunk_count
                                * config.train.max_chunk_epochs
                                + chunk_index * config.train.max_chunk_epochs
                                + chunk_epoch
                            ),
                        )
                        checkpoint_after_validation()
                    reporter.chunk(
                        index=chunk_index + 1,
                        count=current_epoch_chunk_plan.chunk_count,
                        start_origins=chunk_start_samples,
                        origin_budget=(
                            active_chunk.approximate_target_origins
                            * config.train.max_chunk_epochs
                        ),
                        start_blocks=chunk_start_blocks,
                        block_budget=(
                            active_chunk.target_blocks
                            * config.train.max_chunk_epochs
                        ),
                        monitor_hash=active_chunk.validation_hash,
                    )
                    if isinstance(scheduler, EpochChunkCosineScheduler):
                        scheduler.start_chunk(
                            epoch=epoch,
                            start_samples=chunk_start_samples,
                            chunk_samples=(
                                active_chunk.approximate_target_origins
                                * config.train.max_chunk_epochs
                            ),
                            samples_seen=samples_seen,
                        )
                if isinstance(train_loader.dataset, (BarGPTIterableDataset, OfflineShardDataset)):
                    train_loader.dataset.epoch = (
                        epoch
                        * (
                            current_epoch_chunk_plan.chunk_count
                            if current_epoch_chunk_plan is not None
                            else 1
                        )
                        * config.train.max_chunk_epochs
                        + chunk_index * config.train.max_chunk_epochs
                        + chunk_epoch
                        if config.train.full_chunk_training
                        else epoch
                    )
                if current_epoch_chunk_plan is not None:
                    reporter.chunk_epoch(
                        index=chunk_epoch + 1,
                        total=config.train.max_chunk_epochs,
                        start_origins=chunk_epoch_start_samples,
                        start_blocks=chunk_epoch_start_blocks,
                        best_validation_loss=(
                            chunk_best_validation_loss
                            if math.isfinite(chunk_best_validation_loss)
                            else None
                        ),
                        epochs_without_improvement=chunk_epochs_without_improvement,
                    )
                if durable_cursors:
                    reporter.message(
                        "Resuming directly from "
                        + ", ".join(
                            f"worker {worker}: unit {cursor.unit_index:,}/block {cursor.block_offset}"
                            for worker, cursor in sorted(durable_cursors.items())
                        )
                    )
                iterator = _training_prefetcher(train_loader, config, device)
                active_iterator = iterator
                optimizer.zero_grad(set_to_none=True)
                accumulation_count = 0
                accumulated_origins = 0
                accumulated_loader_wait = 0.0
                accumulated_gpu_seconds = 0.0
                accumulated_loader_stages: dict[str, float] = {}
                gpu_event_pairs: list[tuple[torch.cuda.Event, torch.cuda.Event]] = []
                accumulated_metrics: dict[str, torch.Tensor] = {}
                finite_checks: list[torch.Tensor] = []
                finite_check_names: tuple[str, ...] = ()
                finite_check_batches: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
                accumulated_blocks = 0
                accumulated_units: set[str] = set()
                accumulated_condition_blocks = 0
                update_wall_started = time.perf_counter()
                pending_cursors = dict(durable_cursors)
                exhausted = False
                periodic_training_metrics: ValidationAccumulator | None = None
                while True:
                    if training_limit > 0 and samples_seen >= training_limit:
                        break
                    try:
                        batch, loader_wait = iterator.next()
                    except StopIteration:
                        exhausted = True
                        batch = None
                        loader_wait = 0.0
                    if batch is not None:
                        if accumulation_count == 0:
                            nominal_update_origins = (
                                config.data.batch_size
                                * config.data.origin_bars_1s
                                * config.train.gradient_accumulation_steps
                            )
                            periodic_training_metrics = (
                                ValidationAccumulator(
                                    config.data.horizons_us,
                                    config.model.quantiles,
                                    namespace="train",
                                    include_loss_metrics=False,
                                    include_confidence_metrics=False,
                                )
                                if samples_seen + nominal_update_origins >= next_training_metrics
                                else None
                            )
                        gpu_started = time.perf_counter()
                        gpu_start_event = gpu_end_event = None
                        if device.type == "cuda":
                            gpu_start_event = torch.cuda.Event(enable_timing=True)
                            gpu_end_event = torch.cuda.Event(enable_timing=True)
                            gpu_start_event.record()
                        with torch.autocast(device_type=device.type, dtype=_amp_dtype(config.train.amp_dtype), enabled=config.train.amp and device.type == "cuda"):
                            output, result = _forward(
                                model,
                                batch,
                                config,
                                collect_target_stats=False,
                                horizon_ids=training_horizon_ids,
                            )
                            scaled_loss = result.loss / config.train.gradient_accumulation_steps
                        if device.type != "cuda" and not torch.isfinite(result.loss):
                            detail = _nonfinite_loss_diagnostic(result, batch)
                            raise FloatingPointError(
                                f"non-finite training loss at microbatch {batches_seen + accumulation_count + 1}: {detail}"
                            )
                        check, check_names = _finite_check_vector(result, batch)
                        if finite_check_names and check_names != finite_check_names:
                            raise RuntimeError(
                                "training metric schema changed inside one optimizer update: "
                                f"expected={finite_check_names}, actual={check_names}, "
                                f"batch={list(zip(batch.tickers, batch.local_dates, strict=True))}"
                            )
                        finite_checks.append(check)
                        finite_check_names = check_names
                        # Retain compact immutable context and format it only
                        # on the exceptional non-finite path.
                        finite_check_batches.append((batch.tickers, batch.local_dates))
                        if scaler.is_enabled():
                            scaler.scale(scaled_loss).backward()
                        else:
                            scaled_loss.backward()
                        if periodic_training_metrics is not None:
                            # Reuse this update's existing predictions. This
                            # adds bounded reductions only at the configured
                            # sample-clock interval and never another forward.
                            periodic_training_metrics.update(output, batch, result)
                        if gpu_end_event is not None and gpu_start_event is not None:
                            gpu_end_event.record()
                            gpu_event_pairs.append((gpu_start_event, gpu_end_event))
                            micro_gpu_seconds = 0.0
                        else:
                            micro_gpu_seconds = time.perf_counter() - gpu_started
                        origins = batch.origin_count
                        accumulation_count += 1
                        accumulated_origins += origins
                        accumulated_loader_wait += loader_wait
                        for name, seconds in batch.loader_stage_seconds.items():
                            accumulated_loader_stages[name] = accumulated_loader_stages.get(name, 0.0) + float(seconds)
                        accumulated_gpu_seconds += micro_gpu_seconds
                        accumulated_blocks += len(batch.tickers)
                        accumulated_units.update(
                            f"{epoch}:{worker}:{unit}"
                            for worker, unit in zip(batch.worker_ids, batch.unit_indices, strict=True)
                        )
                        accumulated_condition_blocks += sum(batch.condition_blocks)
                        pending_cursors = _advance_cursors(
                            pending_cursors,
                            batch,
                            sequential_plan=(
                                sequential_block_plan
                                if isinstance(train_loader.dataset, BarGPTSequentialDataset)
                                else None
                            ),
                            latest_per_worker=isinstance(train_loader.dataset, OfflineShardDataset),
                        )
                        batch_metrics = {**result.metrics, **_batch_eligibility_metrics(batch)}
                        for key, value in batch_metrics.items():
                            contribution = value.detach().float() * origins
                            accumulated_metrics[key] = accumulated_metrics.get(key, torch.zeros_like(contribution)) + contribution

                    full_update = accumulation_count == config.train.gradient_accumulation_steps
                    partial_final_update = exhausted and accumulation_count > 0 and not _INTERRUPTED
                    if full_update or partial_final_update:
                        _assert_finite_before_step(
                            finite_checks,
                            finite_check_names,
                            finite_check_batches,
                            device=device,
                        )
                        step_started = time.perf_counter()
                        step_start_event = step_end_event = None
                        if device.type == "cuda":
                            step_start_event = torch.cuda.Event(enable_timing=True)
                            step_end_event = torch.cuda.Event(enable_timing=True)
                            step_start_event.record()
                        if scaler.is_enabled():
                            previous_scale = scaler.get_scale()
                            scaler.unscale_(optimizer)
                        if partial_final_update:
                            correction = config.train.gradient_accumulation_steps / accumulation_count
                            for parameter in model.parameters():
                                if parameter.grad is not None:
                                    parameter.grad.mul_(correction)
                        gradient_norm = torch.nn.utils.clip_grad_norm_(
                            model.parameters(), config.train.grad_clip_norm
                        )
                        if scaler.is_enabled():
                            scaler.step(optimizer)
                            scaler.update()
                            if scaler.get_scale() < previous_scale:
                                raise FloatingPointError("FP16 optimizer step overflowed; durable cursor was not advanced")
                        else:
                            optimizer.step()
                        optimizer.zero_grad(set_to_none=True)
                        if step_end_event is not None and step_start_event is not None:
                            step_end_event.record()
                            gpu_event_pairs.append((step_start_event, step_end_event))
                        else:
                            accumulated_gpu_seconds += time.perf_counter() - step_started
                        samples_seen += accumulated_origins
                        batches_seen += accumulation_count
                        optimizer_steps += 1
                        blocks_seen += accumulated_blocks
                        units_seen.update(accumulated_units)
                        condition_blocks_seen += accumulated_condition_blocks
                        durable_cursors = dict(pending_cursors)
                        if isinstance(scheduler, EpochChunkCosineScheduler):
                            scheduler.step(
                                samples_seen=samples_seen,
                            )
                        else:
                            scheduler.step(samples_seen)
                        telemetry_due = samples_seen >= next_log or optimizer_steps == 1
                        if telemetry_due and device.type == "cuda":
                            # Deliberately synchronize only at the bounded
                            # telemetry cadence, never once per optimizer
                            # update.  The events still report exact GPU time.
                            for _start, end in gpu_event_pairs:
                                end.synchronize()
                            accumulated_gpu_seconds += sum(
                                start.elapsed_time(end) / 1_000.0
                                for start, end in gpu_event_pairs
                            )
                        if telemetry_due:
                            update_wall_seconds = time.perf_counter() - update_wall_started
                            prefetch_telemetry = iterator.telemetry()
                            metric_names = tuple(accumulated_metrics)
                            # Gradient norm is already computed for clipping. Include it
                            # in the existing batched device-to-host telemetry transfer,
                            # avoiding a separate GPU synchronization for the terminal.
                            metric_values = torch.stack(
                                [accumulated_metrics[key] for key in metric_names]
                                + [gradient_norm.detach()]
                            ).cpu().tolist()
                            metrics = {
                                key: float(value) / max(1, accumulated_origins)
                                for key, value in zip(metric_names, metric_values[:-1], strict=True)
                            }
                            metrics["train/gradient_norm"] = float(metric_values[-1])
                            metrics.update(
                                {
                                    "train/samples_seen": float(samples_seen),
                                    "train/batches_seen": float(batches_seen),
                                    "train/optimizer_steps": float(optimizer_steps),
                                    "train/blocks_seen": float(blocks_seen),
                                    "train/units_seen": float(len(units_seen)),
                                    "train/condition_blocks_seen": float(condition_blocks_seen),
                                    "train/accumulation_microbatches": float(accumulation_count),
                                    "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                                    "train/loader_wait_seconds": accumulated_loader_wait,
                                    "train/gpu_seconds": accumulated_gpu_seconds,
                                    "train/gpu_duty_cycle": accumulated_gpu_seconds
                                    / max(update_wall_seconds, 1e-9),
                                    "train/host_cache_batches": float(iterator.cache_fill),
                                    "train/host_cache_capacity": float(iterator.cache_capacity),
                                    "train/origins_per_second": accumulated_origins / max(update_wall_seconds, 1e-9),
                                    "train/update_wall_seconds": update_wall_seconds,
                                }
                            )
                            metrics.update({f"train/{key}": value for key, value in prefetch_telemetry.items()})
                            metrics.update({
                                f"train/loader_stage_{name}": seconds
                                for name, seconds in sorted(accumulated_loader_stages.items())
                            })
                            last_metrics = metrics
                        else:
                            # Keep the terminal's coverage state current while
                            # avoiding a CUDA scalar transfer for this update.
                            metrics = dict(last_metrics)
                            metrics.update(
                                {
                                    "train/samples_seen": float(samples_seen),
                                    "train/batches_seen": float(batches_seen),
                                    "train/optimizer_steps": float(optimizer_steps),
                                    "train/blocks_seen": float(blocks_seen),
                                    "train/units_seen": float(len(units_seen)),
                                    "train/condition_blocks_seen": float(condition_blocks_seen),
                                    "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                                    "train/host_cache_batches": float(iterator.cache_fill),
                                    "train/host_cache_capacity": float(iterator.cache_capacity),
                                }
                            )
                        update_metadata = {
                            "train/samples_seen": float(samples_seen),
                            "train/batches_seen": float(batches_seen),
                            "train/optimizer_steps": float(optimizer_steps),
                            "train/blocks_seen": float(blocks_seen),
                            "train/units_seen": float(len(units_seen)),
                            "train/condition_blocks_seen": float(condition_blocks_seen),
                            "train/accumulation_microbatches": float(accumulation_count),
                            "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                            "train/amp_scale": float(scaler.get_scale()) if scaler.is_enabled() else 1.0,
                        }
                        if config.train.full_chunk_training:
                            update_metadata.update(
                                {
                                    "train/outer_epoch": float(current_epoch + 1),
                                    "train/chunk_index": float(chunk_index + 1),
                                    "train/chunk_epoch": float(chunk_epoch + 1),
                                    "train/chunks_completed": float(chunks_completed),
                                    "train/chunk_origins_seen": float(
                                        samples_seen - chunk_start_samples
                                    ),
                                    "train/chunk_blocks_seen": float(
                                        blocks_seen - chunk_start_blocks
                                    ),
                                    "train/chunk_epoch_origins_seen": float(
                                        samples_seen - chunk_epoch_start_samples
                                    ),
                                    "train/chunk_epoch_blocks_seen": float(
                                        blocks_seen - chunk_epoch_start_blocks
                                    ),
                                }
                            )
                        if isinstance(scheduler, EpochChunkCosineScheduler):
                            update_metadata.update(
                                {
                                    "train/epoch_peak_learning_rate": float(
                                        max(
                                            config.train.minimum_learning_rate,
                                            config.train.learning_rate
                                            * config.train.cosine_restart_decay**current_epoch,
                                        )
                                    ),
                                    "train/chunk_cosine_progress": float(
                                        scheduler.chunk_progress
                                    ),
                                }
                            )
                        if telemetry_due:
                            update_metadata.update(
                                {
                                    key: value
                                    for key, value in metrics.items()
                                    if not (key == "train/loss" or key.startswith("train/loss_"))
                                }
                            )
                        periodic_metrics: dict[str, float] = {}
                        if periodic_training_metrics is not None and samples_seen >= next_training_metrics:
                            periodic_metrics = periodic_training_metrics.finalize()
                            update_metadata.update(periodic_metrics)
                            while next_training_metrics <= samples_seen:
                                next_training_metrics += metric_interval
                        deferred_device_metrics = dict(accumulated_metrics)
                        deferred_device_metrics["train/gradient_norm"] = gradient_norm.detach()
                        deferred_losses.append(
                            deferred_device_metrics,
                            origins=accumulated_origins,
                            step=samples_seen,
                            metadata=update_metadata,
                        )
                        assert batch is not None or exhausted
                        reporter_metrics = dict(metrics)
                        reporter_metrics["train/amp_scale"] = update_metadata["train/amp_scale"]
                        reporter_metrics["train/learning_rate"] = update_metadata[
                            "train/learning_rate"
                        ]
                        if isinstance(scheduler, EpochChunkCosineScheduler):
                            reporter_metrics["train/epoch_peak_learning_rate"] = (
                                update_metadata["train/epoch_peak_learning_rate"]
                            )
                            reporter_metrics["train/chunk_cosine_progress"] = (
                                update_metadata["train/chunk_cosine_progress"]
                            )
                        reporter_metrics.update(periodic_metrics)
                        reporter.update(
                            reporter_metrics,
                            tickers=batch.tickers if batch is not None else (),
                            dates=batch.local_dates if batch is not None else (),
                            unit_indices=batch.unit_indices if batch is not None else (),
                            block_offsets=batch.block_offsets if batch is not None else (),
                        )
                        chunk_due = bool(
                            config.train.full_chunk_training
                            and current_epoch_chunk_plan is not None
                            and _chunk_repetition_complete(
                                blocks_seen=blocks_seen,
                                repetition_start_blocks=chunk_epoch_start_blocks,
                                target_blocks=current_epoch_chunk_plan.chunks[
                                    chunk_index
                                ].target_blocks,
                            )
                        )
                        if chunk_due:
                            assert current_epoch_chunk_plan is not None
                            iterator.close()
                            active_iterator = None
                            active_chunk = current_epoch_chunk_plan.chunks[chunk_index]
                            stop_chunk = evaluate_completed_chunk_epoch(active_chunk)
                            finished_outer_epoch = bool(
                                stop_chunk
                                and chunk_index + 1
                                >= current_epoch_chunk_plan.chunk_count
                            )
                            state.next_epoch_plan_ready = bool(
                                next_epoch_plan_future is not None
                                and next_epoch_plan_future.done()
                            )
                            durable_cursors = {}
                            pending_cursors = {}
                            if stop_chunk:
                                if finished_outer_epoch:
                                    exhausted = True
                                else:
                                    chunk_index += 1
                                    chunk_start_samples = samples_seen
                                    chunk_start_blocks = blocks_seen
                                    chunk_epoch = 0
                                    chunk_epoch_start_samples = samples_seen
                                    chunk_epoch_start_blocks = blocks_seen
                                    chunk_best_validation_loss = math.inf
                                    chunk_validation_reference_loss = math.inf
                                    chunk_epochs_without_improvement = 0
                                    chunk_ready_to_advance = False
                                    chunk_epoch_validated = False
                                    next_chunk = current_epoch_chunk_plan.chunks[
                                        chunk_index
                                    ]
                                    train_loader = _offline_chunk_training_loader(
                                        config,
                                        units=offline_train_units,
                                        refs=_chunk_training_refs(
                                            current_epoch_chunk_plan,
                                            full_training_refs,
                                            chunk_index,
                                        ),
                                        resume_cursors={},
                                        stream_epoch=(
                                            epoch
                                            * current_epoch_chunk_plan.chunk_count
                                            * config.train.max_chunk_epochs
                                            + chunk_index
                                            * config.train.max_chunk_epochs
                                        ),
                                    )
                                    reporter.chunk(
                                        index=chunk_index + 1,
                                        count=current_epoch_chunk_plan.chunk_count,
                                        start_origins=chunk_start_samples,
                                        origin_budget=(
                                            next_chunk.approximate_target_origins
                                            * config.train.max_chunk_epochs
                                        ),
                                        start_blocks=chunk_start_blocks,
                                        block_budget=(
                                            next_chunk.target_blocks
                                            * config.train.max_chunk_epochs
                                        ),
                                        monitor_hash=next_chunk.validation_hash,
                                    )
                                    if isinstance(
                                        scheduler, EpochChunkCosineScheduler
                                    ):
                                        scheduler.start_chunk(
                                            epoch=epoch,
                                            start_samples=chunk_start_samples,
                                            chunk_samples=(
                                                next_chunk.approximate_target_origins
                                                * config.train.max_chunk_epochs
                                            ),
                                            samples_seen=samples_seen,
                                        )
                            else:
                                chunk_epoch += 1
                                chunk_epoch_validated = False
                                chunk_epoch_start_samples = samples_seen
                                chunk_epoch_start_blocks = blocks_seen
                                train_loader = _offline_chunk_training_loader(
                                    config,
                                    units=offline_train_units,
                                    refs=_chunk_training_refs(
                                        current_epoch_chunk_plan,
                                        full_training_refs,
                                        chunk_index,
                                    ),
                                    resume_cursors={},
                                    stream_epoch=(
                                        epoch
                                        * current_epoch_chunk_plan.chunk_count
                                        * config.train.max_chunk_epochs
                                        + chunk_index
                                        * config.train.max_chunk_epochs
                                        + chunk_epoch
                                    ),
                                )
                            if not finished_outer_epoch:
                                # StopIteration may have been observed to flush
                                # a partial accumulation at the exact replay
                                # boundary. A newly prepared replay/chunk owns
                                # the continuation state from here.
                                exhausted = False
                            if not exhausted:
                                reporter.chunk_epoch(
                                    index=chunk_epoch + 1,
                                    total=config.train.max_chunk_epochs,
                                    start_origins=chunk_epoch_start_samples,
                                    start_blocks=chunk_epoch_start_blocks,
                                    best_validation_loss=(
                                        chunk_best_validation_loss
                                        if math.isfinite(chunk_best_validation_loss)
                                        else None
                                    ),
                                    epochs_without_improvement=(
                                        chunk_epochs_without_improvement
                                    ),
                                )
                                iterator = _training_prefetcher(
                                    train_loader, config, device
                                )
                                active_iterator = iterator
                            checkpoint_after_validation()
                        validation_due = (
                            not config.train.full_chunk_training
                            and
                            samples_seen >= next_validation
                            and validation_runs_in_epoch < len(epoch_validation_milestones) - 1
                        )
                        if telemetry_due and not validation_due:
                            # One batched device-to-host transfer flushes exact
                            # per-update losses; file and W&B writes happen on
                            # the background metric-writer thread.
                            deferred_losses.flush(metrics_logger)
                            while next_log <= samples_seen:
                                next_log += log_interval
                        if validation_due:
                            preserve_training_prefetch = _preserve_training_prefetch_during_validation(train_loader)
                            if preserve_training_prefetch:
                                # The fixed validation panel is already materialized and
                                # immutable offline workers naturally block once the bounded
                                # host cache is full. Retaining this iterator avoids a costly
                                # Windows worker-process rebuild without advancing durable
                                # cursors, which still describe only consumed batches.
                                reporter.message("Training prefetch retained across offline validation")
                            else:
                                # Live ClickHouse workers must stop before isolated
                                # validation. Rebuilding from durable cursors safely replays
                                # any discarded prefetched blocks.
                                iterator.close()
                                active_iterator = None
                                reporter.message("Training prefetch paused for isolated validation")
                            if not validation_cache.ready:
                                reporter.message("Waiting for initial fixed validation panel preparation")
                            reporter.phase("validating")
                            last_val = validate(
                                model,
                                validation_cache,
                                config,
                                device,
                                namespace="monitor",
                                max_batches=(
                                    None if config.train.validation_batches == 0
                                    else config.train.validation_batches
                                ),
                                max_origins=config.train.monitor_evaluation_origins,
                            )
                            if not deferred_losses.merge_last(last_val):
                                metrics_logger.log(last_val, samples_seen)
                            deferred_losses.flush(metrics_logger)
                            if telemetry_due:
                                while next_log <= samples_seen:
                                    next_log += log_interval
                            reporter.validation(last_val)
                            last_validation_samples = samples_seen
                            validation_runs_in_epoch += 1
                            validation_evaluations_completed += 1
                            checkpoint_after_validation()
                            if validation_runs_in_epoch < len(epoch_validation_milestones):
                                next_validation = epoch_start_samples + epoch_validation_milestones[validation_runs_in_epoch]
                                reporter.schedule_validation(next_validation)
                            if not preserve_training_prefetch and not _INTERRUPTED and not (
                                training_limit > 0 and samples_seen >= training_limit
                            ):
                                train_loader, _unused_validation = _loaders(
                                    config,
                                    args,
                                    resume_cursors=durable_cursors,
                                    sequential_plan=sequential_block_plan,
                                    validation_plan=bounded_validation_plan,
                                    offline_train_units=offline_train_units,
                                    offline_validation_units=offline_validation_units,
                                    offline_manifest=discovery_manifest,
                                )
                                if isinstance(train_loader.dataset, (BarGPTIterableDataset, OfflineShardDataset)):
                                    train_loader.dataset.epoch = epoch
                                iterator = _training_prefetcher(train_loader, config, device)
                                active_iterator = iterator
                                pending_cursors = dict(durable_cursors)
                                reporter.message("Training prefetch resumed from durable worker cursors")
                        accumulation_count = 0
                        accumulated_origins = 0
                        accumulated_loader_wait = 0.0
                        accumulated_gpu_seconds = 0.0
                        accumulated_loader_stages.clear()
                        gpu_event_pairs = []
                        accumulated_metrics = {}
                        finite_checks = []
                        finite_check_names = ()
                        finite_check_batches = []
                        accumulated_blocks = 0
                        accumulated_units = set()
                        accumulated_condition_blocks = 0
                        update_wall_started = time.perf_counter()
                        periodic_training_metrics = None
                    if exhausted or _INTERRUPTED or (training_limit > 0 and samples_seen >= training_limit):
                        if _INTERRUPTED and accumulation_count:
                            optimizer.zero_grad(set_to_none=True)
                            reporter.message(
                                f"Discarded {accumulation_count} incomplete accumulation microbatches; they will replay on resume"
                            )
                        break
                iterator.close()
                active_iterator = None
                reached_diagnostic_limit = _reached_diagnostic_limit(
                    training_limit=training_limit,
                    planned_samples=planned_samples,
                    samples_seen=samples_seen,
                )
                if _INTERRUPTED or reached_diagnostic_limit:
                    break
                if config.train.full_chunk_training:
                    assert current_epoch_chunk_plan is not None
                    if chunk_index != current_epoch_chunk_plan.chunk_count - 1:
                        raise RuntimeError(
                            "training stream ended before every planned chunk boundary"
                        )
                    if not chunk_ready_to_advance:
                        raise RuntimeError(
                            "training stream ended before the final chunk satisfied "
                            "its adaptive stopping contract"
                        )
                needs_epoch_validation = (
                    last_full_validation_samples != samples_seen
                    if discovery_manifest is not None
                    else last_validation_samples != samples_seen
                )
                if needs_epoch_validation:
                    boundary_namespace = _epoch_boundary_evaluation_namespace(
                        epoch=epoch + 1,
                        epochs=config.train.epochs,
                        full_validation_final_epoch_only=(
                            config.train.full_validation_final_epoch_only
                        ),
                    )
                    reporter.phase("validating")
                    if boundary_namespace == "monitor":
                        reporter.message(
                            "Running bounded monitor at repeated-panel epoch boundary; "
                            "complete validation remains reserved for the final epoch"
                        )
                        last_val = validate(
                            model,
                            validation_cache,
                            config,
                            device,
                            namespace="monitor",
                            max_batches=(
                                None if config.train.validation_batches == 0
                                else config.train.validation_batches
                            ),
                            max_origins=config.train.monitor_evaluation_origins,
                        )
                        last_validation_samples = samples_seen
                    else:
                        reporter.message(
                            "Running fixed training-population epoch evaluation"
                        )
                        last_epoch_train = validate(
                            model,
                            epoch_training_cache,
                            config,
                            device,
                            namespace="epoch_train",
                            max_batches=None,
                            max_origins=config.train.epoch_train_evaluation_origins,
                        )
                        metrics_logger.log(last_epoch_train, samples_seen)
                        if not full_validation_cache.ready:
                            reporter.message("Waiting for full fixed validation panel preparation")
                        last_epoch_validation = validate(
                            model,
                            full_validation_cache,
                            config,
                            device,
                            namespace="validation",
                            max_batches=(
                                None if discovery_manifest is not None or config.train.validation_batches == 0
                                else config.train.validation_batches
                            ),
                        )
                        last_generalization_gap = _generalization_gap_metrics(
                            last_epoch_train, last_epoch_validation
                        )
                        last_val = {
                            **last_epoch_validation,
                            **last_generalization_gap,
                        }
                        if config.train.full_chunk_training:
                            observed_loss = float(
                                last_epoch_validation["validation_loss/total"]
                            )
                            (
                                best_outer_validation_loss,
                                early_stopping_reference_loss,
                                outer_epochs_without_improvement,
                                early_stopped,
                            ) = _outer_early_stopping_update(
                                observed_loss=observed_loss,
                                best_loss=best_outer_validation_loss,
                                reference_loss=early_stopping_reference_loss,
                                epochs_without_improvement=outer_epochs_without_improvement,
                                patience=config.train.outer_early_stopping_patience,
                                minimum_relative_delta=(
                                    config.train.outer_early_stopping_min_relative_delta
                                ),
                            )
                            last_val.update(
                                {
                                    "training_control/best_validation_loss": float(
                                        best_outer_validation_loss
                                    ),
                                    "training_control/epochs_without_improvement": float(
                                        outer_epochs_without_improvement
                                    ),
                                    "training_control/early_stopped": float(
                                        early_stopped
                                    ),
                                }
                            )
                        last_validation_samples = samples_seen
                        last_full_validation_samples = samples_seen
                    if not deferred_losses.merge_last(last_val):
                        metrics_logger.log(last_val, samples_seen)
                    deferred_losses.flush(metrics_logger)
                    reporter.validation(last_val)
                    validation_runs_in_epoch += 1
                    validation_evaluations_completed += 1
                    # Advance the durable epoch cursor only after every
                    # end-of-epoch evaluation succeeded. If evaluation fails,
                    # the failure checkpoint stays at the exhausted prior
                    # epoch; resume then reaches EOF without replaying an
                    # optimizer update and retries the missing evaluation.
                    current_epoch = epoch + 1
                    epoch_start_samples = samples_seen
                    epoch_start_blocks = blocks_seen
                    chunk_index = 0
                    chunk_start_samples = samples_seen
                    chunk_start_blocks = blocks_seen
                    chunk_epoch = 0
                    chunk_epoch_start_samples = samples_seen
                    chunk_epoch_start_blocks = blocks_seen
                    chunk_best_validation_loss = math.inf
                    chunk_validation_reference_loss = math.inf
                    chunk_epochs_without_improvement = 0
                    chunk_ready_to_advance = False
                    chunk_epoch_validated = False
                    durable_cursors = {}
                    # The durable cursor and epoch clock already describe the
                    # next epoch at this boundary. Its validation schedule must
                    # therefore resume from zero, not inherit the completed
                    # epoch's final evaluation count.
                    validation_runs_in_epoch = 0
                    reporter.phase("checkpointing")
                    reporter.message(
                        f"Staging immutable epoch checkpoint {current_epoch:04d}"
                    )
                    schedule_checkpoint(
                        force=True,
                        epoch_checkpoint=current_epoch,
                    )
                    reporter.phase("running")
                    if early_stopped:
                        reporter.message(
                            "Global early stopping selected after "
                            f"{outer_epochs_without_improvement} non-improving outer epochs"
                        )
                        break
                else:
                    # An evaluation may already have run exactly at the epoch
                    # boundary in ordinary (non-chunk) training. Preserve its
                    # existing behavior while still advancing the next epoch.
                    current_epoch = epoch + 1
                    epoch_start_samples = samples_seen
                    epoch_start_blocks = blocks_seen
                    chunk_index = 0
                    chunk_start_samples = samples_seen
                    chunk_start_blocks = blocks_seen
                    chunk_epoch = 0
                    chunk_epoch_start_samples = samples_seen
                    chunk_epoch_start_blocks = blocks_seen
                    chunk_best_validation_loss = math.inf
                    chunk_validation_reference_loss = math.inf
                    chunk_epochs_without_improvement = 0
                    chunk_ready_to_advance = False
                    chunk_epoch_validated = False
                    durable_cursors = {}
            if optimizer_steps == 0:
                raise RuntimeError("coverage epoch produced no optimizer updates")
            stopped_at_limit = _reached_diagnostic_limit(
                training_limit=training_limit,
                planned_samples=planned_samples,
                samples_seen=samples_seen,
            )
            completed_normally = not _INTERRUPTED and not stopped_at_limit
            reporter.state.state = (
                "interrupted" if _INTERRUPTED else ("stopped_at_limit" if stopped_at_limit else "completed")
            )
            if last_checkpoint_samples != samples_seen:
                reporter.message("Staging final resumable checkpoint")
                schedule_checkpoint(force=True)
            else:
                reporter.message("Final state already captured by the last validation checkpoint")
    except BaseException as failure:
        try:
            (paths.run_root / "failure_traceback.log").write_text(
                "".join(traceback.format_exception(failure)),
                encoding="utf-8",
            )
        except BaseException as traceback_failure:
            failure.add_note(
                "failure traceback persistence also failed: "
                f"{traceback_failure.__class__.__name__}: {traceback_failure}"
            )
        if active_iterator is not None:
            try:
                active_iterator.close()
            except BaseException as close_failure:
                failure.add_note(
                    f"training prefetch shutdown also failed: {close_failure.__class__.__name__}: {close_failure}"
                )
            finally:
                active_iterator = None
        optimizer.zero_grad(set_to_none=True)
        reporter.state.state = "failed"
        reporter.message(
            f"Saving failure checkpoint at durable origin {samples_seen:,}: "
            f"{failure.__class__.__name__}"
        )
        try:
            schedule_checkpoint(force=True)
        except BaseException as checkpoint_failure:
            failure.add_note(
                f"failure checkpoint also failed: {checkpoint_failure.__class__.__name__}: {checkpoint_failure}"
            )
        raise
    finally:
        original_failure = sys.exc_info()[1]
        cleanup_failures: list[BaseException] = []
        if chunk_planner is not None:
            try:
                chunk_planner.shutdown(wait=True, cancel_futures=False)
            except BaseException as exc:
                cleanup_failures.append(exc)
        if active_iterator is not None:
            try:
                active_iterator.close()
            except BaseException as exc:
                cleanup_failures.append(exc)
        try:
            validation_cache.close()
            if full_validation_cache is not validation_cache:
                full_validation_cache.close()
            epoch_training_cache.close()
        except BaseException as exc:
            cleanup_failures.append(exc)
        try:
            checkpointer.close(wait=True, timeout=300)
        except BaseException as exc:
            cleanup_failures.append(exc)
        try:
            deferred_losses.flush(metrics_logger)
            metrics_logger.close(timeout=300)
        except BaseException as exc:
            cleanup_failures.append(exc)
        if wandb_run is not None:
            try:
                exit_code = 130 if _INTERRUPTED else (1 if original_failure is not None else 0)
                wandb_run.finish(exit_code=exit_code)
            except BaseException as exc:
                cleanup_failures.append(exc)
        if cleanup_failures:
            if original_failure is not None:
                for cleanup_failure in cleanup_failures:
                    original_failure.add_note(
                        "training cleanup also failed: "
                        f"{cleanup_failure.__class__.__name__}: {cleanup_failure}"
                    )
            else:
                primary = cleanup_failures[0]
                for cleanup_failure in cleanup_failures[1:]:
                    primary.add_note(
                        "additional cleanup failure: "
                        f"{cleanup_failure.__class__.__name__}: {cleanup_failure}"
                    )
                raise primary
    write_model_card(
        paths.run_root / "model_card.json",
        {
            "model_family": MODEL_FAMILY,
            "version": MODEL_VERSION,
            "learning_contract": LEARNING_CONTRACT,
            "run_name": run_name,
            "samples_seen": samples_seen,
            "batches_seen": batches_seen,
            "optimizer_steps": optimizer_steps,
            "blocks_seen": blocks_seen,
            "units_seen": len(units_seen),
            "condition_blocks_seen": condition_blocks_seen,
            "coverage_plan": plan.to_dict(),
            "completed_normally": completed_normally,
            "full_chunk_training": config.train.full_chunk_training,
            "chunks_completed": chunks_completed,
            "early_stopped": early_stopped,
            "best_outer_validation_loss": (
                best_outer_validation_loss
                if math.isfinite(best_outer_validation_loss)
                else None
            ),
            "outer_epochs_without_improvement": outer_epochs_without_improvement,
            "stopped_at_limit": _reached_diagnostic_limit(
                training_limit=training_limit,
                planned_samples=planned_samples,
                samples_seen=samples_seen,
            ),
            "validation_tickers": validation_tickers,
            "identity_holdout_tickers": identity_holdouts,
            "parameters": parameter_summary(_unwrap(model)),
        },
    )
    return 130 if _INTERRUPTED else 0


if __name__ == "__main__":
    raise SystemExit(main())
