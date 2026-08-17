from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Iterable

import torch

from research.bar_gpt.v3.inference import BarGPTEncoder, load_pretrained
from research.bar_gpt.v3.loader import BarGPTIterableDataset, ClickHouseBarStreamConfig, make_dataloader
from research.bar_gpt.v3.train import preflight
from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, discover_clickhouse_env_files
from research.mlops.env import load_env_files


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v3\embeddings")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export causal BarGPT origin embeddings from a trained checkpoint.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--split", choices=("train", "validation"), default="validation")
    parser.add_argument("--max-origins", type=int, default=100_000)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_origins <= 0:
        raise ValueError("max-origins must be positive")
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available() else ("cpu" if args.device == "auto" else args.device))
    model, data, payload = load_pretrained(args.checkpoint, device=device)
    url, user, password = default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    evidence = preflight(ClickHouseHttpClient(url, user, password), data)
    stream = ClickHouseBarStreamConfig(
        url=url, user=user, password=password, database=data.database, table=data.one_second_table,
        max_threads=data.clickhouse_max_threads_per_worker, max_block_size=data.clickhouse_max_block_size,
        max_memory_usage=data.clickhouse_max_memory_usage,
        query_days=data.clickhouse_query_days,
        max_bytes_before_external_sort=data.clickhouse_max_bytes_before_external_sort,
    )
    loader = make_dataloader(
        BarGPTIterableDataset(data_config=data, stream_config=stream, split=args.split, seed=17), data, drop_last=False
    )
    encoder = BarGPTEncoder(model, data)
    embeddings: list[torch.Tensor] = []
    timestamps: list[torch.Tensor] = []
    tickers: list[str] = []
    dates: list[str] = []
    collected = 0
    with torch.no_grad():
        for raw_batch in loader:
            batch = raw_batch.to(device)
            values, valid = encoder(batch)
            for row, ticker in enumerate(batch.tickers):
                selected = valid[row]
                take = min(int(selected.sum()), args.max_origins - collected)
                if take <= 0:
                    break
                embeddings.append(values[row][selected][:take].float().cpu())
                timestamps.append(batch.origin_timestamps_us[row][selected][:take].cpu())
                tickers.extend([ticker] * take)
                dates.extend([batch.local_dates[row]] * take)
                collected += take
            print(f"exported {collected:,}/{args.max_origins:,} origins", flush=True)
            if collected >= args.max_origins:
                break
    if not embeddings:
        raise RuntimeError("no embeddings were exported")
    run_root = Path(args.output_root) / time.strftime("%Y%m%d_%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)
    artifact = {
        "embeddings": torch.cat(embeddings),
        "timestamps_us": torch.cat(timestamps),
        "tickers": tickers,
        "local_dates": dates,
        "checkpoint": str(Path(args.checkpoint).resolve()),
        "contract_hash": payload["contract_hash"],
    }
    torch.save(artifact, run_root / "embeddings.pt")
    (run_root / "manifest.json").write_text(json.dumps({
        "origins": collected,
        "width": artifact["embeddings"].shape[-1],
        "checkpoint": artifact["checkpoint"],
        "contract_hash": artifact["contract_hash"],
        "data_evidence": evidence,
    }, indent=2), encoding="utf-8")
    print(f"embedding export complete: {run_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
