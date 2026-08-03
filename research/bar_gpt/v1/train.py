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
from research.bar_gpt.v1.model import BarGPTV1
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
    parser.add_argument("--daily-context-bars", type=int, default=data.daily_context_bars)
    parser.add_argument("--batch-size", type=int, default=data.batch_size)
    parser.add_argument("--loader-workers", type=int, default=data.loader_workers)
    parser.add_argument("--ready-queue-blocks", type=int, default=data.ready_queue_blocks)
    parser.add_argument("--clickhouse-max-threads-per-worker", type=int, default=data.clickhouse_max_threads_per_worker)
    parser.add_argument("--clickhouse-max-memory-usage", type=int, default=data.clickhouse_max_memory_usage)
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
    parser.add_argument("--logging-samples", type=int, default=train.logging_samples)
    parser.add_argument("--validation-interval-samples", type=int, default=train.validation_interval_samples)
    parser.add_argument("--validation-batches", type=int, default=train.validation_batches)
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
        daily_context_bars=int(args.daily_context_bars),
        batch_size=int(args.batch_size),
        maximum_target_horizon_us=max(horizons),
        loader_workers=int(args.loader_workers),
        ready_queue_blocks=int(args.ready_queue_blocks),
        clickhouse_max_threads_per_worker=int(args.clickhouse_max_threads_per_worker),
        clickhouse_max_memory_usage=int(args.clickhouse_max_memory_usage),
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
        seed=int(args.seed),
        wandb_project=str(args.wandb_project),
        wandb_entity=str(args.wandb_entity),
        wandb_mode=str(args.wandb_mode),
        wandb_init_timeout=int(args.wandb_init_timeout),
        logging_samples=int(args.logging_samples),
        validation_interval_samples=int(args.validation_interval_samples),
        validation_batches=int(args.validation_batches),
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


def _loaders(config: ExperimentConfig, args: argparse.Namespace) -> tuple[DataLoader[Any], DataLoader[Any]]:
    if args.dummy_data:
        example = _dummy_example(config.data)
        train_dataset = _DummyDataset(example)
        validation_dataset = _DummyDataset(example)
        common = dict(batch_size=config.data.batch_size, num_workers=0, collate_fn=collate_examples)
        return DataLoader(train_dataset, **common), DataLoader(validation_dataset, **common)
    stream = _stream_config(config.data)
    train_dataset = BarGPTIterableDataset(data_config=config.data, stream_config=stream, split="train", seed=config.train.seed)
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
) -> float:
    model.eval()
    losses: list[float] = []
    iterator = iter(loader)
    for _ in range(max(1, config.train.validation_batches)):
        try:
            raw_batch = next(iterator)
        except StopIteration:
            break
        batch = raw_batch.to(device)
        with torch.autocast(device_type=device.type, dtype=_amp_dtype(config.train.amp_dtype), enabled=config.train.amp and device.type == "cuda"):
            _, result = _forward(model, batch, config)
        losses.append(float(result.loss))
    model.train()
    if not losses:
        raise RuntimeError("fixed validation panel produced no batches")
    return float(sum(losses) / len(losses))


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
    epoch: int,
    batches_in_epoch: int,
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
        "epoch": epoch,
        "batches_in_epoch": batches_in_epoch,
        "last_latest_samples": last_latest_samples,
        "rng": {"python": random.getstate(), "numpy": np.random.get_state(), "torch": torch.get_rng_state()},
    }


def restore_checkpoint(
    path: str,
    model: torch.nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    scheduler: SampleWarmupCosineScheduler,
    device: torch.device,
    config: ExperimentConfig,
) -> dict[str, Any]:
    if not path:
        return {"samples_seen": 0, "batches_seen": 0, "epoch": 0, "batches_in_epoch": 0, "checkpointer": {}}
    payload = torch.load(path, map_location=device, weights_only=False)
    saved_config = payload.get("config", {})
    current = to_dict(config)
    if saved_config.get("model") != current.get("model") or saved_config.get("data") != current.get("data"):
        raise RuntimeError("resume checkpoint model/data contract does not match the requested run")
    _unwrap(model).load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    scaler.load_state_dict(payload.get("scaler", {}))
    scheduler.load_state_dict(payload.get("scheduler"))
    rng = payload.get("rng", {})
    if rng:
        random.setstate(rng["python"]); np.random.set_state(rng["numpy"]); torch.set_rng_state(rng["torch"])
    return payload


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
    (paths.run_root / "config.json").write_text(json.dumps(to_dict(config), indent=2, default=str), encoding="utf-8")
    model: torch.nn.Module = BarGPTV1(config.model).to(device)
    if config.train.compile_model and hasattr(torch, "compile"):
        model = torch.compile(model, dynamic=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate, weight_decay=config.train.weight_decay, foreach=device.type == "cuda")
    scheduler = SampleWarmupCosineScheduler(
        optimizer,
        warmup_samples=min(config.train.warmup_samples, max(0, config.train.max_samples - 1)),
        total_samples=config.train.max_samples,
        minimum_lr=config.train.minimum_learning_rate,
    )
    scaler = torch.amp.GradScaler("cuda", enabled=config.train.amp and config.train.amp_dtype == "fp16" and device.type == "cuda")
    restored = restore_checkpoint(args.resume_checkpoint, model, optimizer, scaler, scheduler, device, config)
    train_loader, validation_loader = _loaders(config, args)
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
    holdout = tuple(sorted({ticker for ticker, _start, _end in config.data.validation_slices}))
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config={**to_dict(config), "data_evidence": evidence, "validation_tickers": holdout},
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
            clock_name="origin",
            archive_prefix="checkpoint_origin",
            archive_on_force=False,
        ),
    )
    checkpointer.load_state_dict(restored.get("checkpointer"))
    samples_seen = int(restored.get("samples_seen", 0))
    batches_seen = int(restored.get("batches_seen", 0))
    resume_epoch = int(restored.get("epoch", 0))
    resume_batches = int(restored.get("batches_in_epoch", 0))
    last_latest_samples = int(restored.get("last_latest_samples", 0))
    state = TrainingProgressState(
        run_name=run_name,
        device=str(device),
        precision=config.train.amp_dtype if config.train.amp else "float32",
        output_dir=str(paths.run_root),
        model_parameters=int(parameter_summary(_unwrap(model))["total_parameters"]),
        max_samples=config.train.max_samples,
        samples_seen=samples_seen,
        batches_seen=batches_seen,
    )
    reporter = TrainingReporter(state, layout=config.train.progress_layout)
    next_log = samples_seen
    next_validation = samples_seen + max(1, config.train.validation_interval_samples)
    last_metrics: dict[str, float] = {"train/loss": math.inf}
    last_val: dict[str, float] = {}
    current_epoch = resume_epoch
    batches_in_epoch = resume_batches
    try:
        with reporter:
            checkpointer.set_message_callback(reporter.message)
            reporter.message(f"Certified source: {evidence}; held-out tickers={len(holdout)}")
            for epoch in range(resume_epoch, max(1, config.train.epochs)):
                current_epoch = epoch
                if isinstance(train_loader.dataset, BarGPTIterableDataset):
                    train_loader.dataset.epoch = epoch
                iterator = iter(train_loader)
                skip = resume_batches if epoch == resume_epoch else 0
                if skip:
                    reporter.message(f"Restoring exact data cursor by skipping {skip:,} deterministic batches")
                    for _ in range(skip):
                        next(iterator)
                batches_in_epoch = skip
                while not _INTERRUPTED:
                    if config.train.max_samples > 0 and samples_seen >= config.train.max_samples:
                        break
                    loader_started = time.perf_counter()
                    try:
                        raw_batch = next(iterator)
                    except StopIteration:
                        break
                    loader_wait = time.perf_counter() - loader_started
                    batch = raw_batch.to(device)
                    optimizer.zero_grad(set_to_none=True)
                    gpu_started = time.perf_counter()
                    with torch.autocast(device_type=device.type, dtype=_amp_dtype(config.train.amp_dtype), enabled=config.train.amp and device.type == "cuda"):
                        _, result = _forward(model, batch, config)
                    if not torch.isfinite(result.loss):
                        raise FloatingPointError(f"non-finite training loss at batch {batches_seen + 1}: {float(result.loss)}")
                    if scaler.is_enabled():
                        scaler.scale(result.loss).backward()
                        scaler.unscale_(optimizer)
                        torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
                        scaler.step(optimizer); scaler.update()
                    else:
                        result.loss.backward()
                        torch.nn.utils.clip_grad_norm_(model.parameters(), config.train.grad_clip_norm)
                        optimizer.step()
                    if device.type == "cuda":
                        torch.cuda.synchronize()
                    gpu_seconds = time.perf_counter() - gpu_started
                    origins = batch.origin_count
                    samples_seen += origins
                    scheduler.step(samples_seen)
                    batches_seen += 1
                    batches_in_epoch += 1
                    metrics = {key: float(value) for key, value in result.metrics.items()}
                    metrics.update(
                        {
                            "train/samples_seen": float(samples_seen),
                            "train/batches_seen": float(batches_seen),
                            "train/learning_rate": float(optimizer.param_groups[0]["lr"]),
                            "train/loader_wait_seconds": loader_wait,
                            "train/gpu_seconds": gpu_seconds,
                            "train/origins_per_second": origins / max(loader_wait + gpu_seconds, 1e-9),
                        }
                    )
                    last_metrics = metrics
                    reporter.update(metrics, tickers=batch.tickers, dates=batch.local_dates)
                    if samples_seen >= next_log or batches_seen == 1:
                        metrics_logger.log(metrics, samples_seen)
                        next_log = samples_seen + max(1, config.train.logging_samples)
                    if samples_seen >= next_validation:
                        val_loss = validate(model, validation_loader, config, device)
                        last_val = {"val/loss": val_loss}
                        metrics_logger.log(last_val, samples_seen)
                        reporter.validation(val_loss)
                        next_validation = samples_seen + max(1, config.train.validation_interval_samples)
                    latest_due = samples_seen // max(1, config.train.checkpoint_latest_samples) > checkpointer.last_latest_bucket
                    payload_latest_samples = samples_seen if latest_due else last_latest_samples
                    checkpointer.maybe_save(
                        step=samples_seen,
                        payload_factory=lambda: checkpoint_payload(
                            model, optimizer, scaler, scheduler, checkpointer, config,
                            samples_seen=samples_seen, batches_seen=batches_seen,
                            epoch=current_epoch, batches_in_epoch=batches_in_epoch,
                            last_latest_samples=payload_latest_samples,
                        ),
                        train_metrics=last_metrics,
                        val_metrics=last_val,
                    )
                    if latest_due:
                        last_latest_samples = samples_seen
                resume_batches = 0
                if _INTERRUPTED or (config.train.max_samples > 0 and samples_seen >= config.train.max_samples):
                    break
            reporter.state.state = "interrupted" if _INTERRUPTED else "completed"
            reporter.message("Saving final resumable checkpoint")
            if last_latest_samples != samples_seen:
                checkpointer.maybe_save(
                    step=samples_seen,
                    payload_factory=lambda: checkpoint_payload(
                        model, optimizer, scaler, scheduler, checkpointer, config,
                        samples_seen=samples_seen, batches_seen=batches_seen,
                        epoch=current_epoch, batches_in_epoch=batches_in_epoch,
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
            "validation_tickers": holdout,
            "parameters": parameter_summary(_unwrap(model)),
        },
    )
    return 130 if _INTERRUPTED else 0


if __name__ == "__main__":
    raise SystemExit(main())
