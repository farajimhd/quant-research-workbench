from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import re
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import torch
from torch.utils.data import DataLoader, IterableDataset

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.bar_gpt.v1 import MODEL_FAMILY, MODEL_VERSION
from research.bar_gpt.v1.cohort import BAR_GPT_TRAINING_TICKERS
from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig, to_dict
from research.bar_gpt.v1.data import PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, BarGPTBatch, BarGPTExample, BarView, collate_examples
from research.bar_gpt.v1.loader import (
    BarGPTIterableDataset,
    ClickHouseBarStreamConfig,
    build_session_examples,
    make_dataloader,
)
from research.bar_gpt.v1.prefetch import DeviceBatchPrefetcher
from research.bar_gpt.v1.sampling import CoverageCursor, coverage_plan_summary
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.metrics import ValidationAccumulator
from research.bar_gpt.v1.objectives import BarGPTLoss, compute_loss
from research.bar_gpt.v1.progress import TrainingProgressState, TrainingReporter
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
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
from research.mlops.metrics import JsonlMetricLogger
from research.mlops.model_artifacts import parameter_summary, write_model_card
from research.mlops.paths import RunPaths, default_run_root
from research.mlops.schedulers import SampleWarmupCosineScheduler
from research.mlops.seeds import set_seed
from research.mlops.wandb_utils import init_wandb


JOB_TYPE = "train"
_INTERRUPTED = False


def _handle_interrupt(_signum: int, _frame: Any) -> None:
    global _INTERRUPTED
    _INTERRUPTED = True


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip().upper() for item in value.split(",") if item.strip())


def _int_csv(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in value.split(",") if item.strip())


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    data, model, train = DataConfig(), BarGPTConfig(), TrainConfig()
    parser = argparse.ArgumentParser(description="Pretrain BarGPT v1 from certified one-second and daily bars.")
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
    parser.add_argument("--coverage-blocks-per-unit", type=int, default=data.coverage_blocks_per_unit)
    parser.add_argument("--validation-blocks-per-slice", type=int, default=data.validation_blocks_per_slice)
    parser.add_argument("--daily-context-bars", type=int, default=data.daily_context_bars)
    parser.add_argument("--batch-size", type=int, default=data.batch_size)
    parser.add_argument("--loader-workers", type=int, default=data.loader_workers)
    parser.add_argument("--ready-queue-blocks", type=int, default=data.ready_queue_blocks)
    parser.add_argument("--clickhouse-max-threads-per-worker", type=int, default=data.clickhouse_max_threads_per_worker)
    parser.add_argument("--clickhouse-max-memory-usage", type=int, default=data.clickhouse_max_memory_usage)
    parser.add_argument("--clickhouse-query-days", type=int, default=data.clickhouse_query_days)
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
    parser.add_argument("--validation-interval-samples", type=int, default=train.validation_interval_samples)
    parser.add_argument("--validation-batches", type=int, default=train.validation_batches)
    parser.add_argument("--validation-runs-per-epoch", type=int, default=train.validation_runs_per_epoch)
    parser.add_argument("--warmup-samples", type=int, default=train.warmup_samples)
    parser.add_argument("--minimum-learning-rate", type=float, default=train.minimum_learning_rate)
    parser.add_argument("--checkpoint-latest-samples", type=int, default=train.checkpoint_latest_samples)
    parser.add_argument("--checkpoint-archive-samples", type=int, default=train.checkpoint_archive_samples)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default=train.progress_layout)
    parser.add_argument("--autoregressive-weight", type=float, default=train.autoregressive_weight)
    parser.add_argument("--horizon-weight", type=float, default=train.horizon_weight)
    parser.add_argument("--availability-weight", type=float, default=train.availability_weight)
    parser.add_argument("--condition-positive-weight", type=float, default=train.condition_positive_weight)
    parser.add_argument("--latent-prediction-weight", type=float, default=train.latent_prediction_weight)
    parser.add_argument("--wandb-project", default=train.wandb_project)
    parser.add_argument("--wandb-entity", default=train.wandb_entity)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default=train.wandb_mode)
    parser.add_argument("--wandb-init-timeout", type=int, default=train.wandb_init_timeout)
    parser.add_argument("--resume-checkpoint", default="")
    parser.add_argument("--seed", type=int, default=train.seed)
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
        coverage_blocks_per_unit=int(args.coverage_blocks_per_unit),
        validation_blocks_per_slice=int(args.validation_blocks_per_slice),
        daily_context_bars=int(args.daily_context_bars),
        batch_size=int(args.batch_size),
        maximum_target_horizon_us=max(horizons),
        loader_workers=int(args.loader_workers),
        ready_queue_blocks=int(args.ready_queue_blocks),
        clickhouse_max_threads_per_worker=int(args.clickhouse_max_threads_per_worker),
        clickhouse_max_memory_usage=int(args.clickhouse_max_memory_usage),
        clickhouse_query_days=int(args.clickhouse_query_days),
        clickhouse_max_bytes_before_external_sort=int(args.clickhouse_max_bytes_before_external_sort),
        balance_activity_regimes=bool(args.balance_activity_regimes),
    )
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
        validation_interval_samples=int(args.validation_interval_samples),
        validation_batches=int(args.validation_batches),
        validation_runs_per_epoch=int(args.validation_runs_per_epoch),
        warmup_samples=int(args.warmup_samples),
        minimum_learning_rate=float(args.minimum_learning_rate),
        checkpoint_latest_samples=int(args.checkpoint_latest_samples),
        checkpoint_archive_samples=int(args.checkpoint_archive_samples),
        progress_layout=str(args.progress_layout),
        autoregressive_weight=float(args.autoregressive_weight),
        horizon_weight=float(args.horizon_weight),
        availability_weight=float(args.availability_weight),
        condition_positive_weight=float(args.condition_positive_weight),
        latent_prediction_weight=float(args.latent_prediction_weight),
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
        config.condition_table,
        config.condition_status_table,
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
    certified_condition_tickers = (
        BAR_GPT_TRAINING_TICKERS
        if set(config.tickers) <= set(BAR_GPT_TRAINING_TICKERS)
        else config.tickers
    )
    condition_artifact = config.condition_table + ":tickers=" + ",".join(sorted(certified_condition_tickers))
    expected_condition_days = (dt.date.fromisoformat(config.end_date) - dt.date.fromisoformat(config.start_date)).days
    condition_sql = f"""
SELECT count()
FROM {quote_ident(config.database)}.{quote_ident(config.condition_status_table)} FINAL
WHERE artifact_name={sql_string(condition_artifact)}
  AND status='complete'
  AND local_date>=toDate({sql_string(config.start_date)})
  AND local_date<toDate({sql_string(config.end_date)})
FORMAT TSVRaw
"""
    condition_days = int(client.query_tsv(condition_sql).strip() or "0")
    if condition_days != expected_condition_days:
        raise RuntimeError(
            f"exact condition-label sidecar is not certified for the requested range: "
            f"{condition_days}/{expected_condition_days} calendar days; run "
            "python -B -m research.bar_gpt.v1.run_build_conditions_1s"
        )
    return {
        "certified_start": config.start_date,
        "certified_end": cursor,
        "certified_ranges": str(len(intervals)),
        "alias_certified_end": alias_cursor,
        "alias_certified_ranges": str(len(alias_intervals)),
        "alias_identity_ranges_checked": str(alias_checks),
        "daily_certified_end": daily_cursor,
        "daily_certified_ranges": str(daily_ranges),
        "condition_certified_days": str(condition_days),
    }


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
    )


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
) -> tuple[DataLoader[Any], DataLoader[Any]]:
    if args.dummy_data:
        example = _dummy_example(config.data)
        train_dataset = _DummyDataset(example)
        validation_dataset = _DummyDataset(example)
        common = dict(batch_size=config.data.batch_size, num_workers=0, collate_fn=collate_examples)
        return DataLoader(train_dataset, **common), DataLoader(validation_dataset, **common)
    stream = _stream_config(config.data)
    train_dataset = BarGPTIterableDataset(
        data_config=config.data,
        stream_config=stream,
        split="train",
        seed=config.train.seed,
        resume_cursors=resume_cursors,
    )
    validation_dataset = BarGPTIterableDataset(data_config=config.data, stream_config=stream, split="validation", seed=config.train.seed)
    return make_dataloader(train_dataset, config.data, drop_last=True), make_dataloader(validation_dataset, config.data, drop_last=False)


def _amp_dtype(name: str) -> torch.dtype:
    return {"bf16": torch.bfloat16, "fp16": torch.float16, "float32": torch.float32}[name]


def _unwrap(model: torch.nn.Module) -> torch.nn.Module:
    return getattr(model, "_orig_mod", model)


def _forward(model: torch.nn.Module, batch: BarGPTBatch, config: ExperimentConfig) -> tuple[Any, BarGPTLoss]:
    output = model(
        batch.views,
        timeframe_us=TIMEFRAME_US_BY_NAME,
        pathway_ids=PATHWAY_ID_BY_NAME,
        base_view="1s",
        origin_indices=batch.origin_indices,
        asof_indices=batch.asof_indices,
        horizon_ids=torch.arange(len(config.data.horizons_us), device=batch.origin_indices.device),
    )
    return output, compute_loss(output, batch, config.train, config.model.quantiles)


@torch.no_grad()
def validate(
    model: torch.nn.Module,
    loader: DataLoader[Any],
    config: ExperimentConfig,
    device: torch.device,
) -> dict[str, float]:
    model.eval()
    accumulator = ValidationAccumulator(config.data.horizons_us, config.model.quantiles)
    iterator = DeviceBatchPrefetcher(loader, device, enabled=config.train.cuda_prefetch)
    for _ in range(max(1, config.train.validation_batches)):
        try:
            batch = next(iterator)
        except StopIteration:
            break
        with torch.autocast(device_type=device.type, dtype=_amp_dtype(config.train.amp_dtype), enabled=config.train.amp and device.type == "cuda"):
            output, result = _forward(model, batch, config)
        accumulator.update(output, batch, result)
    model.train()
    return accumulator.finalize()


def checkpoint_payload(
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    scheduler: SampleWarmupCosineScheduler,
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
    data_cursors: dict[int, CoverageCursor],
    plan_hash: str,
    last_latest_samples: int,
) -> dict[str, Any]:
    return {
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
        "data_cursors": {str(worker): asdict(cursor) for worker, cursor in data_cursors.items()},
        "plan_hash": plan_hash,
        "last_latest_samples": last_latest_samples,
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
    scheduler: SampleWarmupCosineScheduler,
    device: torch.device,
    config: ExperimentConfig,
    plan_hash: str,
) -> dict[str, Any]:
    if not path:
        return {
            "samples_seen": 0, "batches_seen": 0, "optimizer_steps": 0,
            "blocks_seen": 0, "units_seen": [], "condition_blocks_seen": 0,
            "epoch": 0, "data_cursors": {}, "checkpointer": {},
        }
    payload = torch.load(path, map_location=device, weights_only=False)
    saved_config = payload.get("config", {})
    current = to_dict(config)
    if saved_config.get("model") != current.get("model") or saved_config.get("data") != current.get("data"):
        raise RuntimeError("resume checkpoint model/data contract does not match the requested run")
    if payload.get("plan_hash") != plan_hash:
        raise RuntimeError("resume checkpoint coverage plan does not match the requested run")
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


def _cursor_map(values: dict[str, Any] | None) -> dict[int, CoverageCursor]:
    return {
        int(worker): CoverageCursor(unit_index=int(cursor["unit_index"]), block_offset=int(cursor["block_offset"]))
        for worker, cursor in (values or {}).items()
    }


def _advance_cursors(cursors: dict[int, CoverageCursor], batch: BarGPTBatch) -> dict[int, CoverageCursor]:
    updated = dict(cursors)
    for worker, unit, block in zip(batch.worker_ids, batch.unit_indices, batch.block_offsets, strict=True):
        candidate = CoverageCursor(int(unit), int(block))
        current = updated.get(int(worker))
        if current is None or (candidate.unit_index, candidate.block_offset) > (current.unit_index, current.block_offset):
            updated[int(worker)] = candidate
    return updated


def main(argv: Iterable[str] | None = None) -> int:
    global _INTERRUPTED
    _INTERRUPTED = False
    signal.signal(signal.SIGINT, _handle_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_interrupt)
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args(argv)
    config = build_config(args)
    set_seed(config.train.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type == "cuda":
        torch.cuda.manual_seed_all(config.train.seed)
        torch.set_float32_matmul_precision("high")
    evidence = {"mode": "dummy"} if args.dummy_data else preflight(
        ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()), config.data
    )
    run_name = args.run_name or f"bar-gpt-v1-{time.strftime('%Y%m%d-%H%M%S')}"
    config.train.run_name = run_name
    run_root = Path(config.train.output_root) / run_name if args.output_root else default_run_root(MODEL_FAMILY, MODEL_VERSION, JOB_TYPE, run_name)
    paths = RunPaths.create(run_root)
    holdout = tuple(sorted({ticker for ticker, _start, _end in config.data.validation_slices}))
    plan = coverage_plan_summary(
        start_date=config.data.start_date,
        end_date=config.data.validation_start_date,
        training_tickers=config.data.training_tickers,
        blocks_per_unit=config.data.coverage_blocks_per_unit,
        origin_bars=config.data.origin_bars_1s,
        epochs=config.train.epochs,
        seed=config.train.seed,
    )
    planned_samples = plan.expected_origins
    if args.dummy_data and config.train.max_samples == 0:
        planned_samples = config.data.batch_size * config.data.origin_bars_1s * config.train.gradient_accumulation_steps
    training_limit = config.train.max_samples if config.train.max_samples > 0 else planned_samples
    # A diagnostic/safety cap must not shorten the epoch learning-rate curve.
    schedule_samples = max(2, training_limit if args.dummy_data else planned_samples)
    validation_interval = (
        config.train.validation_interval_samples
        if config.train.validation_interval_samples > 0
        else max(1, math.ceil(plan.expected_origins / config.train.epochs / config.train.validation_runs_per_epoch))
    )
    (paths.run_root / "config.json").write_text(json.dumps(to_dict(config), indent=2, default=str), encoding="utf-8")
    (paths.run_root / "coverage_plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    model: torch.nn.Module = BarGPTV1(config.model).to(device)
    if config.train.compile_model and hasattr(torch, "compile"):
        model = torch.compile(model, dynamic=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay, foreach=device.type == "cuda")
    scheduler = SampleWarmupCosineScheduler(
        optimizer,
        warmup_samples=min(config.train.warmup_samples, schedule_samples - 1),
        total_samples=schedule_samples,
        minimum_lr=config.train.minimum_learning_rate,
    )
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and config.train.amp_dtype == "fp16" and device.type == "cuda")
    restored = restore_checkpoint(args.resume_checkpoint, model, optimizer, scaler, scheduler, device, config, plan.plan_hash)
    resume_cursors = _cursor_map(restored.get("data_cursors"))
    train_loader, validation_loader = _loaders(config, args, resume_cursors=resume_cursors)
    wandb_run = init_wandb(
        entity=config.train.wandb_entity,
        project=config.train.wandb_project,
        run_name=run_name,
        config=to_dict(config),
        run_dir=paths.wandb_dir,
        mode=config.train.wandb_mode,
        timeout_seconds=config.train.wandb_init_timeout,
    )
    metrics_logger = JsonlMetricLogger(paths.metrics_path, wandb_run)
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
            "data_evidence": evidence,
            "validation_tickers": holdout,
            "coverage_plan": plan.to_dict(),
            "resolved_training_limit": training_limit,
            "resolved_validation_interval": validation_interval,
        },
        data_roots={
            "clickhouse": default_clickhouse_url(),
            "database": config.data.database,
            "one_second_table": config.data.one_second_table,
            "one_second_manifest_table": config.data.manifest_table,
            "daily_table": config.data.daily_table,
            "daily_manifest_table": config.data.daily_manifest_table,
            "identity_database": config.data.identity_database,
            "identity_interval_table": config.data.identity_interval_table,
        },
        output_root=paths.run_root,
        source_checkpoint=Path(args.resume_checkpoint) if args.resume_checkpoint else None,
        wandb_info={"project": config.train.wandb_project, "entity": config.train.wandb_entity, "run_name": run_name},
    )
    checkpointer = AsyncCheckpointManager(
        paths.checkpoints_dir,
        paths.checkpoint_manifest_path,
        CheckpointPolicy(
            latest_steps=max(1, config.train.checkpoint_latest_samples),
            archive_steps=max(0, config.train.checkpoint_archive_samples),
            monitor_train_key="train/loss",
            monitor_val_key="val/loss",
            threshold_intervals=True,
            # The final forced save must queue behind an in-flight best/latest save;
            # skipping it would lose the newest durable data cursor on shutdown.
            skip_latest_if_busy=False,
            clock_name="origin",
            archive_prefix="checkpoint_origin",
            archive_on_force=False,
        ),
    )
    checkpointer.load_state_dict(restored.get("checkpointer"))
    samples_seen = int(restored.get("samples_seen", 0))
    batches_seen = int(restored.get("batches_seen", 0))
    optimizer_steps = int(restored.get("optimizer_steps", 0))
    blocks_seen = int(restored.get("blocks_seen", 0))
    units_seen = set(str(value) for value in restored.get("units_seen", []))
    condition_blocks_seen = int(restored.get("condition_blocks_seen", 0))
    resume_epoch = int(restored.get("epoch", 0))
    last_latest_samples = int(restored.get("last_latest_samples", 0))
    durable_cursors = dict(resume_cursors)
    state = TrainingProgressState(
        run_name=run_name,
        device=str(device),
        precision=config.train.amp_dtype if config.train.amp else "float32",
        output_dir=str(paths.run_root),
        model_parameters=int(parameter_summary(_unwrap(model))["total_parameters"]),
        max_samples=training_limit,
        samples_seen=samples_seen,
        batches_seen=batches_seen,
        optimizer_steps=optimizer_steps,
        blocks_seen=blocks_seen,
        units_seen=len(units_seen),
        condition_blocks_seen=condition_blocks_seen,
        planned_units=plan.units * config.train.epochs,
        planned_blocks=plan.expected_blocks,
        gradient_accumulation_steps=config.train.gradient_accumulation_steps,
        cuda_prefetch=config.train.cuda_prefetch and device.type == "cuda",
    )
    reporter = TrainingReporter(state, layout=config.train.progress_layout)
    next_log = samples_seen
    next_validation = samples_seen + validation_interval
    last_validation_samples = -1
    epoch_plan_origins = max(1, math.ceil(plan.expected_origins / config.train.epochs))
    validation_runs_in_epoch = min(
        config.train.validation_runs_per_epoch - 1,
        max(0, samples_seen - resume_epoch * epoch_plan_origins) // validation_interval,
    )
    last_metrics: dict[str, float] = {"train/loss": math.inf}
    last_val: dict[str, float] = {}
    current_epoch = resume_epoch
    completed_normally = False
    try:
        with reporter:
            checkpointer.set_message_callback(reporter.message)
            reporter.message(
                f"Certified source; plan={plan.plan_hash[:12]} units={plan.units:,} "
                f"blocks={plan.expected_blocks:,}; held-out tickers={len(holdout)}"
            )
            for epoch in range(resume_epoch, max(1, config.train.epochs)):
                current_epoch = epoch
                if epoch != resume_epoch:
                    train_loader, _unused_validation = _loaders(config, args, resume_cursors={})
                    durable_cursors = {}
                    validation_runs_in_epoch = 0
                if isinstance(train_loader.dataset, BarGPTIterableDataset):
                    train_loader.dataset.epoch = epoch
                if durable_cursors:
                    reporter.message(
                        "Resuming directly from "
                        + ", ".join(
                            f"worker {worker}: unit {cursor.unit_index:,}/block {cursor.block_offset}"
                            for worker, cursor in sorted(durable_cursors.items())
                        )
                    )
                iterator = DeviceBatchPrefetcher(train_loader, device, enabled=config.train.cuda_prefetch)
                optimizer.zero_grad(set_to_none=True)
                accumulation_count = 0
                accumulated_origins = 0
                accumulated_loader_wait = 0.0
                accumulated_gpu_seconds = 0.0
                accumulated_metrics: dict[str, float] = {}
                accumulated_blocks = 0
                accumulated_units: set[str] = set()
                accumulated_condition_blocks = 0
                pending_cursors = dict(durable_cursors)
                exhausted = False
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
                        gpu_started = time.perf_counter()
                        with torch.autocast(device_type=device.type, dtype=_amp_dtype(config.train.amp_dtype), enabled=config.train.amp and device.type == "cuda"):
                            _, result = _forward(model, batch, config)
                            scaled_loss = result.loss / config.train.gradient_accumulation_steps
                        if not torch.isfinite(result.loss):
                            raise FloatingPointError(f"non-finite training loss at microbatch {batches_seen + accumulation_count + 1}")
                        if scaler.is_enabled():
                            scaler.scale(scaled_loss).backward()
                        else:
                            scaled_loss.backward()
                        if device.type == "cuda":
                            torch.cuda.synchronize()
                        micro_gpu_seconds = time.perf_counter() - gpu_started
                        origins = batch.origin_count
                        accumulation_count += 1
                        accumulated_origins += origins
                        accumulated_loader_wait += loader_wait
                        accumulated_gpu_seconds += micro_gpu_seconds
                        accumulated_blocks += len(batch.tickers)
                        accumulated_units.update(f"{epoch}:{unit}" for unit in batch.unit_indices)
                        accumulated_condition_blocks += sum(batch.condition_blocks)
                        pending_cursors = _advance_cursors(pending_cursors, batch)
                        for key, value in result.metrics.items():
                            accumulated_metrics[key] = accumulated_metrics.get(key, 0.0) + float(value) * origins

                    full_update = accumulation_count == config.train.gradient_accumulation_steps
                    partial_final_update = exhausted and accumulation_count > 0 and not _INTERRUPTED
                    if full_update or partial_final_update:
                        step_started = time.perf_counter()
                        if scaler.is_enabled():
                            previous_scale = scaler.get_scale()
                            scaler.unscale_(optimizer)
                        if partial_final_update:
                            correction = config.train.gradient_accumulation_steps / accumulation_count
                            for parameter in model.parameters():
                                if parameter.grad is not None:
                                    parameter.grad.mul_(correction)
                        torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
                        if scaler.is_enabled():
                            scaler.step(optimizer)
                            scaler.update()
                            if scaler.get_scale() < previous_scale:
                                raise FloatingPointError("FP16 optimizer step overflowed; durable cursor was not advanced")
                        else:
                            optimizer.step()
                        optimizer.zero_grad(set_to_none=True)
                        if device.type == "cuda":
                            torch.cuda.synchronize()
                        accumulated_gpu_seconds += time.perf_counter() - step_started
                        samples_seen += accumulated_origins
                        batches_seen += accumulation_count
                        optimizer_steps += 1
                        blocks_seen += accumulated_blocks
                        units_seen.update(accumulated_units)
                        condition_blocks_seen += accumulated_condition_blocks
                        durable_cursors = dict(pending_cursors)
                        scheduler.step(samples_seen)
                        metrics = {
                            key: value / max(1, accumulated_origins)
                            for key, value in accumulated_metrics.items()
                        }
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
                                "train/origins_per_second": accumulated_origins / max(accumulated_loader_wait + accumulated_gpu_seconds, 1e-9),
                            }
                        )
                        last_metrics = metrics
                        assert batch is not None or exhausted
                        reporter.update(metrics, tickers=batch.tickers if batch is not None else (), dates=batch.local_dates if batch is not None else ())
                        if samples_seen >= next_log or optimizer_steps == 1:
                            metrics_logger.log(metrics, samples_seen)
                            next_log = samples_seen + max(1, config.train.logging_samples)
                        if (
                            samples_seen >= next_validation
                            and validation_runs_in_epoch < config.train.validation_runs_per_epoch - 1
                        ):
                            last_val = validate(model, validation_loader, config, device)
                            metrics_logger.log(last_val, samples_seen)
                            reporter.validation(last_val["val/loss"])
                            last_validation_samples = samples_seen
                            validation_runs_in_epoch += 1
                            next_validation = samples_seen + validation_interval
                        latest_due = samples_seen // max(1, config.train.checkpoint_latest_samples) > checkpointer.last_latest_bucket
                        payload_latest_samples = samples_seen if latest_due else last_latest_samples
                        snapshot_cursors = dict(durable_cursors)
                        checkpointer.maybe_save(
                            step=samples_seen,
                            payload_factory=lambda cursors=snapshot_cursors, latest=payload_latest_samples: checkpoint_payload(
                                model, optimizer, scaler, scheduler, checkpointer, config,
                                samples_seen=samples_seen, batches_seen=batches_seen,
                                optimizer_steps=optimizer_steps, epoch=current_epoch,
                                blocks_seen=blocks_seen, units_seen=units_seen,
                                condition_blocks_seen=condition_blocks_seen,
                                data_cursors=cursors, plan_hash=plan.plan_hash,
                                last_latest_samples=latest,
                            ),
                            train_metrics=last_metrics,
                            val_metrics=last_val,
                        )
                        if latest_due:
                            last_latest_samples = samples_seen
                        accumulation_count = 0
                        accumulated_origins = 0
                        accumulated_loader_wait = 0.0
                        accumulated_gpu_seconds = 0.0
                        accumulated_metrics = {}
                        accumulated_blocks = 0
                        accumulated_units = set()
                        accumulated_condition_blocks = 0
                    if exhausted or _INTERRUPTED or (training_limit > 0 and samples_seen >= training_limit):
                        if _INTERRUPTED and accumulation_count:
                            optimizer.zero_grad(set_to_none=True)
                            reporter.message(
                                f"Discarded {accumulation_count} incomplete accumulation microbatches; they will replay on resume"
                            )
                        break
                if _INTERRUPTED or (training_limit > 0 and samples_seen >= training_limit):
                    break
                current_epoch = epoch + 1
                durable_cursors = {}
                if last_validation_samples != samples_seen:
                    last_val = validate(model, validation_loader, config, device)
                    metrics_logger.log(last_val, samples_seen)
                    reporter.validation(last_val["val/loss"])
                    last_validation_samples = samples_seen
            if optimizer_steps == 0:
                raise RuntimeError("coverage epoch produced no optimizer updates")
            stopped_at_limit = training_limit < planned_samples and samples_seen >= training_limit
            completed_normally = not _INTERRUPTED and not stopped_at_limit
            reporter.state.state = (
                "interrupted" if _INTERRUPTED else ("stopped_at_limit" if stopped_at_limit else "completed")
            )
            reporter.message("Saving final resumable checkpoint")
            snapshot_cursors = dict(durable_cursors)
            checkpointer.maybe_save(
                step=samples_seen,
                payload_factory=lambda cursors=snapshot_cursors: checkpoint_payload(
                    model, optimizer, scaler, scheduler, checkpointer, config,
                    samples_seen=samples_seen, batches_seen=batches_seen,
                    optimizer_steps=optimizer_steps, epoch=current_epoch,
                    blocks_seen=blocks_seen, units_seen=units_seen,
                    condition_blocks_seen=condition_blocks_seen,
                    data_cursors=cursors, plan_hash=plan.plan_hash,
                    last_latest_samples=samples_seen,
                ),
                train_metrics=last_metrics,
                val_metrics=last_val,
                force=True,
            )
    finally:
        checkpointer.close(wait=True, timeout=300)
        if wandb_run is not None:
            try:
                wandb_run.finish()
            except Exception:
                pass
    write_model_card(
        paths.run_root / "model_card.json",
        {
            "model_family": MODEL_FAMILY,
            "version": MODEL_VERSION,
            "run_name": run_name,
            "samples_seen": samples_seen,
            "batches_seen": batches_seen,
            "optimizer_steps": optimizer_steps,
            "blocks_seen": blocks_seen,
            "units_seen": len(units_seen),
            "condition_blocks_seen": condition_blocks_seen,
            "coverage_plan": plan.to_dict(),
            "completed_normally": completed_normally,
            "stopped_at_limit": training_limit < planned_samples and samples_seen >= training_limit,
            "validation_tickers": holdout,
            "parameters": parameter_summary(_unwrap(model)),
        },
    )
    return 130 if _INTERRUPTED else 0


if __name__ == "__main__":
    raise SystemExit(main())
