from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterable

import torch

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.model_artifacts import parameter_summary
from research.news_reaction_model.v8.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v8.data import ClickHouseNewsReactionDataset, NewsReactionBatch, audit_prepared_dataset, make_dummy_batch
from research.news_reaction_model.v8.losses import compute_loss
from research.news_reaction_model.v8.model import NewsReactionModelV8
from research.news_reaction_model.v8.train import amp_dtype, set_seed

REPO_ROOT = Path(__file__).resolve().parents[3]


def csv_ints(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    loader = LoaderConfig()
    parser = argparse.ArgumentParser(description="Profile news reaction model sizes and batch sizes.")
    parser.add_argument("--model-sizes", default="128,192,256,384")
    parser.add_argument("--batch-sizes", default="512,1024,2048,4096,8192,16384,32768")
    parser.add_argument("--layers", default="1,2,4")
    parser.add_argument("--warmup-steps", type=int, default=2)
    parser.add_argument("--profile-steps", type=int, default=5)
    parser.add_argument("--real-data", action="store_true")
    parser.add_argument("--data-start", default="2019-01-01")
    parser.add_argument("--data-end-exclusive", default="2027-01-01")
    parser.add_argument("--dataset-database", default=loader.dataset_database)
    parser.add_argument("--dataset-table", default=loader.dataset_table)
    parser.add_argument("--dataset-version", default=loader.dataset_version)
    parser.add_argument("--openai-embedding-dim", type=int, default=loader.openai_embedding_dim)
    parser.add_argument("--stock-state-dim", type=int, default=loader.stock_state_dim)
    parser.add_argument("--amp-dtype", choices=("bf16", "fp16", "float32"), default="bf16")
    parser.add_argument("--output-root", default=r"D:\TradingML\runtimes\news-reaction-model\v8\profiles")
    parser.add_argument("--seed", type=int, default=17)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv); set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    run_dir = Path(args.output_root) / f"size_sweep_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    report = run_dir / "profile.jsonl"
    max_batch = max(csv_ints(args.batch_sizes))
    loader = LoaderConfig(
        dataset_database=args.dataset_database, dataset_table=args.dataset_table, dataset_version=args.dataset_version,
        openai_embedding_dim=max(1, args.openai_embedding_dim),
        stock_state_dim=max(1, args.stock_state_dim),
        batch_size=max_batch, query_batch_articles=max_batch,
    )
    if args.real_data:
        audit = audit_prepared_dataset(loader, args.data_start, args.data_end_exclusive)
        print(f"DATASET ready | articles={audit['rows']:,} | version={loader.dataset_version}", flush=True)
    source = load_real_sample(loader, args.data_start, args.data_end_exclusive, max_batch) if args.real_data else make_dummy_batch(max_batch, loader)
    if source.sample_count < max_batch:
        raise RuntimeError(
            f"Profile source contains {source.sample_count:,} articles but the largest requested batch is "
            f"{max_batch:,}. Expand --data-start/--data-end-exclusive or request smaller batches; "
            "the profiler will not silently report a truncated batch as the requested size."
        )
    rows = []
    for d_model in csv_ints(args.model_sizes):
        for layers in csv_ints(args.layers):
            for batch_size in csv_ints(args.batch_sizes):
                row = profile_configuration(source, batch_size, d_model, layers, device, args, loader)
                rows.append(row); append_jsonl(report, row)
                if row["status"] == "ok":
                    print(
                        f"PROFILE d={d_model:<4} layers={layers} batch={int(row['batch_size']):<5} "
                        f"params={int(row['parameters']):,} step={float(row['step_seconds']) * 1000:.2f}ms "
                        f"rate={float(row['samples_per_second']):,.0f} articles/s peak={int(row['peak_memory_bytes']) / 2**20:.0f}MiB",
                        flush=True,
                    )
                else:
                    print(
                        f"PROFILE d={d_model} layers={layers} batch={batch_size} {str(row['status']).upper()} | "
                        f"{row.get('error', '')}",
                        flush=True,
                    )
    viable = [row for row in rows if row["status"] == "ok"]
    best = max(viable, key=lambda row: row["samples_per_second"]) if viable else None
    summary = {"event": "summary", "device": str(device), "configurations": len(rows), "successful": len(viable), "fastest": best}
    append_jsonl(report, summary); (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if best:
        print(
            f"COMPLETED {len(viable)}/{len(rows)} configurations | fastest d={best['d_model']} layers={best['layers']} "
            f"batch={best['batch_size']} rate={best['samples_per_second']:,.0f} articles/s | report={report}",
            flush=True,
        )
    else:
        print(f"FAILED no viable configurations | report={report}", flush=True)
    return 0 if viable else 1


def load_real_sample(loader: LoaderConfig, start: str, end_exclusive: str, required_articles: int) -> NewsReactionBatch:
    """Collect an exact bounded profile sample across month-partitioned loader batches."""
    dataset = ClickHouseNewsReactionDataset(loader, start=start, end_exclusive=end_exclusive)
    iterator = dataset.iter_batches()
    batches: list[NewsReactionBatch] = []
    collected = 0
    try:
        for batch in iterator:
            remaining = required_articles - collected
            if remaining <= 0:
                break
            selected = slice_batch(batch, min(remaining, batch.sample_count), torch.device("cpu"))
            batches.append(selected)
            collected += selected.sample_count
            if collected >= required_articles:
                break
    finally:
        dataset.stop()
        iterator.close()
    if not batches:
        raise RuntimeError(f"No prepared articles were loaded from [{start}, {end_exclusive}).")
    return concatenate_batches(batches)


def concatenate_batches(batches: list[NewsReactionBatch]) -> NewsReactionBatch:
    if not batches:
        raise ValueError("At least one batch is required.")
    x = {
        "channel_mask": torch.cat([batch.x["channel_mask"] for batch in batches], dim=0),
        "openai_embedding": torch.cat([batch.x["openai_embedding"] for batch in batches], dim=0),
        "stock_state": torch.cat([batch.x["stock_state"] for batch in batches], dim=0),
    }
    return NewsReactionBatch(
        x=x,
        return_targets=torch.cat([batch.return_targets for batch in batches], dim=0),
        label_mask=torch.cat([batch.label_mask for batch in batches], dim=0),
        identity={
            key: [value for batch in batches for value in batch.identity[key]]
            for key in batches[0].identity
        },
        sample_count=sum(batch.sample_count for batch in batches),
    )


def slice_batch(batch: NewsReactionBatch, size: int, device: torch.device) -> NewsReactionBatch:
    size = min(size, batch.sample_count)
    x = {
        "channel_mask": batch.x["channel_mask"][:size].to(device),
        "openai_embedding": batch.x["openai_embedding"][:size].to(device),
        "stock_state": batch.x["stock_state"][:size].to(device),
    }
    return NewsReactionBatch(
        x=x,
        return_targets=batch.return_targets[:size].to(device), label_mask=batch.label_mask[:size].to(device),
        identity={key: value[:size] for key, value in batch.identity.items()}, sample_count=size,
    )


def profile_configuration(source: NewsReactionBatch, batch_size: int, d_model: int, layers: int, device: torch.device, args: argparse.Namespace, loader: LoaderConfig | None = None) -> dict[str, object]:
    loader = loader or LoaderConfig()
    config = ModelConfig(
        openai_embedding_dim=loader.openai_embedding_dim,
        stock_state_dim=loader.stock_state_dim,
        d_model=d_model, hidden_dim=d_model, layers=layers,
    )
    try:
        model = NewsReactionModelV8(config).to(device); optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
        batch = slice_batch(source, batch_size, device); dtype = amp_dtype(args.amp_dtype)
        if device.type == "cuda": torch.cuda.reset_peak_memory_stats()
        timings = []
        for step in range(args.warmup_steps + args.profile_steps):
            started = time.perf_counter(); optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type=device.type, dtype=dtype, enabled=device.type == "cuda" and args.amp_dtype != "float32"):
                output = model(batch.x); loss = compute_loss(output, batch).loss
            loss.backward(); optimizer.step()
            if device.type == "cuda": torch.cuda.synchronize()
            if step >= args.warmup_steps: timings.append(time.perf_counter() - started)
        seconds = sum(timings) / max(len(timings), 1)
        return {"status": "ok", "d_model": d_model, "layers": layers, "batch_size": batch.sample_count,
                "parameters": parameter_summary(model)["total_parameters"], "step_seconds": seconds,
                "samples_per_second": batch.sample_count / max(seconds, 1e-9),
                "peak_memory_bytes": torch.cuda.max_memory_allocated() if device.type == "cuda" else 0}
    except torch.cuda.OutOfMemoryError as exc:
        if device.type == "cuda": torch.cuda.empty_cache()
        return {"status": "oom", "d_model": d_model, "layers": layers, "batch_size": batch_size, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - retain a structured per-configuration failure
        return {
            "status": "error",
            "d_model": d_model,
            "layers": layers,
            "batch_size": batch_size,
            "error": f"{type(exc).__name__}: {exc}",
        }


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle: handle.write(json.dumps(row, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
