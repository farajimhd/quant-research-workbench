from __future__ import annotations

import argparse
import itertools
import json
import time

import torch

from research.news_reaction_model.v18.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v18.data import PreparedEpisodeDataset
from research.news_reaction_model.v18.losses import compute_loss
from research.news_reaction_model.v18.model import NewsReactionModelV18


def parse_ints(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile V18 model and batch sizes.")
    parser.add_argument("--model-sizes", default="128,192,256,384")
    parser.add_argument("--batch-sizes", default="512,1024,2048,4096,8192,16384")
    parser.add_argument("--layers", default="1,2,4")
    parser.add_argument("--prepared-root", default="")
    parser.add_argument("--v15-root", default="")
    args = parser.parse_args(argv)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    results: list[dict[str, object]] = []
    for d_model, layers, batch_size in itertools.product(
        parse_ints(args.model_sizes),
        parse_ints(args.layers),
        parse_ints(args.batch_sizes),
    ):
        loader = LoaderConfig(batch_size=batch_size)
        if args.prepared_root:
            loader.prepared_dataset_root = type(loader.prepared_dataset_root)(args.prepared_root)
        if args.v15_root:
            loader.v15_prepared_root = type(loader.v15_prepared_root)(args.v15_root)
        dataset = PreparedEpisodeDataset(
            loader,
            start=loader.train_start,
            end_exclusive=loader.train_end_exclusive,
        )
        try:
            batch = next(dataset.iter_batches()).to(device)
            model = NewsReactionModelV18(
                ModelConfig(d_model=d_model, hidden_dim=d_model, layers=layers)
            ).to(device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
            if device.type == "cuda":
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
            started = time.perf_counter()
            output = model(batch.x)
            loss = compute_loss(output, batch).loss
            loss.backward()
            optimizer.step()
            if device.type == "cuda":
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            row = {
                "d_model": d_model,
                "layers": layers,
                "batch_size": batch.sample_count,
                "seconds": elapsed,
                "samples_per_second": batch.sample_count / elapsed,
                "peak_gpu_gib": (
                    torch.cuda.max_memory_allocated() / 2**30
                    if device.type == "cuda"
                    else 0.0
                ),
            }
            print(json.dumps(row, sort_keys=True), flush=True)
            results.append(row)
        except torch.OutOfMemoryError:
            print(
                json.dumps(
                    {"d_model": d_model, "layers": layers, "batch_size": batch_size, "oom": True}
                ),
                flush=True,
            )
            if device.type == "cuda":
                torch.cuda.empty_cache()
        finally:
            dataset.stop()
    viable = [row for row in results if float(row["peak_gpu_gib"]) <= 90.0]
    if viable:
        best = max(viable, key=lambda row: float(row["samples_per_second"]))
        print("RECOMMENDED " + json.dumps(best, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
