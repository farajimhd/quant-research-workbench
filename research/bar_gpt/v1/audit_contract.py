from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Iterable

import torch

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.data import TIMEFRAME_US_BY_NAME
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.loader import BarGPTIterableDataset, ClickHouseBarStreamConfig, make_dataloader
from research.bar_gpt.v1.targets import TARGET_NAMES
from research.bar_gpt.v1.train import preflight
from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, discover_clickhouse_env_files
from research.mlops.env import load_env_files


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\audit_contract")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BarGPT feature variance, redundancy, masks and target coverage on real loader batches.")
    parser.add_argument("--start-date", default="2025-10-01")
    parser.add_argument("--end-date", default="2026-01-01")
    parser.add_argument("--tickers", default="MSFT,AMD,INTC,JPM")
    parser.add_argument("--batches", type=int, default=16)
    parser.add_argument("--correlation-rows", type=int, default=100_000)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args(list(argv) if argv is not None else None)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batches <= 0 or args.correlation_rows <= 0:
        raise ValueError("batches and correlation rows must be positive")
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    tickers = tuple(item.strip().upper() for item in args.tickers.split(",") if item.strip())
    data = DataConfig(
        tickers=tickers,
        start_date=args.start_date,
        end_date=args.end_date,
        validation_start_date=args.end_date,
        validation_slices=(),
        loader_workers=4,
        batch_size=2,
        coverage_blocks_per_unit=16,
    )
    data.validate()
    url, user, password = default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    evidence = preflight(ClickHouseHttpClient(url, user, password), data)
    stream = ClickHouseBarStreamConfig(
        url=url, user=user, password=password, database=data.database, table=data.one_second_table,
        max_threads=data.clickhouse_max_threads_per_worker, max_block_size=data.clickhouse_max_block_size,
        max_memory_usage=data.clickhouse_max_memory_usage,
        query_days=data.clickhouse_query_days,
        max_bytes_before_external_sort=data.clickhouse_max_bytes_before_external_sort,
    )
    loader = make_dataloader(BarGPTIterableDataset(data_config=data, stream_config=stream, split="train", seed=17), data, drop_last=True)
    samples: dict[str, list[torch.Tensor]] = {name: [] for name in TIMEFRAME_US_BY_NAME}
    target_valid = torch.zeros((len(data.horizons_us), len(TARGET_NAMES)), dtype=torch.float64)
    target_total = torch.zeros_like(target_valid)
    origins = condition_blocks = 0
    iterator = iter(loader)
    started = time.perf_counter()
    for index in range(args.batches):
        batch = next(iterator).to("cpu")
        origins += batch.origin_count
        condition_blocks += sum(batch.condition_blocks)
        for name, values in batch.views.items():
            remaining = args.correlation_rows - sum(item.shape[0] for item in samples[name])
            if remaining > 0:
                samples[name].append(values.reshape(-1, values.shape[-1])[:remaining].float())
        assert batch.horizon_mask is not None
        mask = batch.horizon_mask & batch.origin_mask[:, :, None, None]
        target_valid += mask.sum((0, 1)).double()
        target_total += batch.origin_mask.sum().double() * torch.ones_like(target_total)
        if args.progress_layout != "none":
            print(f"audit batch {index + 1}/{args.batches} origins={origins:,}", flush=True)
    feature_report: dict[str, object] = {}
    for timeframe, parts in samples.items():
        if not parts:
            continue
        values = torch.cat(parts, dim=0)
        mean = values.mean(0)
        std = values.std(0)
        standardized = (values - mean) / std.clamp_min(1e-12)
        correlation = standardized.T @ standardized / max(1, values.shape[0] - 1)
        correlation.fill_diagonal_(0)
        pairs = []
        for flat in torch.topk(correlation.abs().flatten(), k=min(20, correlation.numel())).indices.tolist():
            left, right = divmod(flat, correlation.shape[1])
            if left < right:
                pairs.append({"left": MODEL_FEATURE_NAMES[left], "right": MODEL_FEATURE_NAMES[right], "correlation": float(correlation[left, right])})
        feature_report[timeframe] = {
            "rows": values.shape[0],
            "near_constant": [MODEL_FEATURE_NAMES[i] for i in torch.where(std < 1e-8)[0].tolist()],
            "zero_fraction": {MODEL_FEATURE_NAMES[i]: float((values[:, i] == 0).float().mean()) for i in range(values.shape[1])},
            "highest_absolute_correlations": pairs[:10],
        }
    target_report = {
        f"{horizon // 1_000_000}s": {
            name: float(target_valid[horizon_index, target_index] / target_total[horizon_index, target_index].clamp_min(1))
            for target_index, name in enumerate(TARGET_NAMES)
        }
        for horizon_index, horizon in enumerate(data.horizons_us)
    }
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": time.perf_counter() - started,
        "evidence": evidence,
        "origins": origins,
        "condition_blocks": condition_blocks,
        "features": feature_report,
        "target_valid_fraction": target_report,
    }
    output = Path(args.output_root) / time.strftime("%Y%m%d_%H%M%S") / "audit.json"
    _atomic_json(output, payload)
    print(f"contract audit complete: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
