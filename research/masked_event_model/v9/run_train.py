from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "data_source": "sample_cache",
    "clickhouse_url": "",
    "clickhouse_database": "market_sip_compact",
    "events_table": "events",
    "train_index_table": "train_2019_to_2025",
    "validation_index_table": "validation_2026",
    "index_table": "",
    "canonical_root": r"D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_compact_v1",
    "precomputed_chunk_root": "",
    "sample_cache_root": r"D:\market-data\prepared\event_sample_cache",
    "reference_dir": None,
    "train_start_date": "2025-11-01",
    "train_end_date": "2025-11-30",
    "validation_start_date": "2025-12-01",
    "validation_end_date": "2025-12-05",
    "tickers": "ALL",
    "events_per_chunk": 128,
    "num_spans": 128,
    "origins_per_span": 32,
    "min_origin_stride": 1,
    "max_origin_stride": 16,
    "query_bundle_spans": 64,
    "clickhouse_max_threads": 8,
    "clickhouse_max_memory_usage": "80G",
    "sample_cache_prefetch_shards": 2,
    "sample_cache_shuffle_records": True,
    "sample_cache_drop_last": True,
    "batch_size": 4096,
    "max_steps": 10000,
    "epochs": 5,
    "num_workers": 0,
    "progress_layout": "auto",
    "profile_first_steps": 0,
    "profile_training_every_steps": 10,
    "profile_inference_every_steps": 10,
    "device": "cuda",
    "event_mask_ratio": 0.70,
    "min_masked_events": 1,
    "header_bit_corruption_prob": 0.20,
    "header_bit_corruption_ratio": 0.05,
    "event_bit_corruption_prob": 0.30,
    "event_bit_corruption_ratio": 0.20,
    "d_byte": 24,
    "d_model": 128,
    "embedding_dim": 32,
    "n_heads": 4,
    "encoder_layers": 6,
    "decoder_layers": 2,
    "compile_model": True,
    "learning_rate": 2e-4,
    "wandb_project": "June2026-event-token-mae-v9-unweighted",
    "wandb_entity": "mehdifaraji",
    "wandb_mode": "online",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch v9 event-token masked autoencoder training with editable defaults.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    known, extra = parser.parse_known_args()
    defaults = dict(DEFAULTS)
    defaults["reference_dir"] = str(repo_root() / "research" / "market_references" / "massive")
    argv = build_argv(defaults)
    if known.dry_run:
        argv.append("--dry-run")
    if known.fresh_start:
        argv.append("--fresh-start")
    argv.extend(extra)
    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if known.print_only:
        return
    raise SystemExit(subprocess.call(argv, cwd=str(repo_root())))


def build_argv(values: dict[str, Any]) -> list[str]:
    argv = [sys.executable, "-m", "research.masked_event_model.v9.train"]
    for key, value in values.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        elif value is None or value == "":
            continue
        else:
            argv.extend([flag, str(value)])
    return argv


def repo_root() -> Path:
    return next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())


if __name__ == "__main__":
    main()
