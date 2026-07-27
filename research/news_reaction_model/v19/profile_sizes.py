from __future__ import annotations

import argparse
import itertools
import json
import time

import torch

from research.news_reaction_model.v19.config import ExperimentConfig, LoaderConfig, ModelConfig
from research.news_reaction_model.v19.data import PreparedEpisodeDataset
from research.news_reaction_model.v19.losses import compute_loss
from research.news_reaction_model.v19.model import NewsReactionModelV19
from research.news_reaction_model.v19.targets import fit_training_statistics


def parse_ints(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item.strip()]


def attention_heads_for_width(width: int) -> int:
    for heads in (6, 8, 4, 2, 1):
        if width % heads == 0:
            return heads
    raise AssertionError(f"No compatible attention-head count for width {width}.")


def main(argv: list[str] | None = None) -> int:
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(description="Profile V19 over read-only V18 arrays.")
    parser.add_argument("--model-sizes", default="256,384,512")
    parser.add_argument("--batch-sizes", default="512,1024,2048,4096,8192")
    parser.add_argument("--layers", default="1,2,3")
    parser.add_argument("--prepared-root", default="")
    parser.add_argument("--v15-root", default="")
    args = parser.parse_args(argv)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    base_loader = LoaderConfig(batch_size=max(parse_ints(args.batch_sizes)))
    if args.prepared_root:
        base_loader.prepared_dataset_root = type(base_loader.prepared_dataset_root)(
            args.prepared_root
        )
    if args.v15_root:
        base_loader.v15_prepared_root = type(base_loader.v15_prepared_root)(args.v15_root)
    source = PreparedEpisodeDataset(
        base_loader,
        start=base_loader.train_start,
        end_exclusive=base_loader.train_end_exclusive,
    )
    try:
        statistics = fit_training_statistics(
            source,
            beta=defaults.train.effective_number_beta,
            minimum_class_weight=defaults.train.minimum_class_weight,
            maximum_class_weight=defaults.train.maximum_class_weight,
            scale_quantile=defaults.train.regression_scale_quantile,
            scale_floor_pct=defaults.train.regression_scale_floor_pct,
            minimum_scale_rows=defaults.train.regression_scale_minimum_rows,
        )
        full_batch = next(source.iter_batches())
    finally:
        source.stop()
    results: list[dict[str, object]] = []
    for d_model, layers, batch_size in itertools.product(
        parse_ints(args.model_sizes),
        parse_ints(args.layers),
        parse_ints(args.batch_sizes),
    ):
        try:
            attention_heads = attention_heads_for_width(d_model)
            keep = min(batch_size, full_batch.sample_count)
            indices = torch.arange(keep)
            batch = type(full_batch)(
                x={key: value[indices].to(device) for key, value in full_batch.x.items()},
                direction=full_batch.direction[indices].to(device),
                path=full_batch.path[indices].to(device),
                flow=full_batch.flow[indices].to(device),
                regression_targets=full_batch.regression_targets[indices].to(device),
                target_mask=full_batch.target_mask[indices].to(device),
                identity={},
                sample_count=keep,
            )
            model = NewsReactionModelV19(
                ModelConfig(
                    d_model=d_model,
                    tower_hidden_dim=d_model,
                    feedforward_dim=3 * d_model,
                    transformer_layers=layers,
                    attention_heads=attention_heads,
                ),
                statistics,
            ).to(device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
            if device.type == "cuda":
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
            started = time.perf_counter()
            output = model(batch.x)
            loss = compute_loss(output, batch, statistics).loss
            loss.backward()
            optimizer.step()
            if device.type == "cuda":
                torch.cuda.synchronize()
            elapsed = time.perf_counter() - started
            row = {
                "d_model": d_model,
                "attention_heads": attention_heads,
                "layers": layers,
                "batch_size": keep,
                "seconds": elapsed,
                "samples_per_second": keep / elapsed,
                "parameters": sum(parameter.numel() for parameter in model.parameters()),
                "peak_gpu_gib": (
                    torch.cuda.max_memory_allocated() / 2**30
                    if device.type == "cuda"
                    else 0.0
                ),
            }
            print(json.dumps(row, sort_keys=True), flush=True)
            results.append(row)
            del model, optimizer, batch
        except torch.OutOfMemoryError:
            print(
                json.dumps(
                    {
                        "d_model": d_model,
                        "attention_heads": attention_heads_for_width(d_model),
                        "layers": layers,
                        "batch_size": batch_size,
                        "oom": True,
                    }
                ),
                flush=True,
            )
        finally:
            if device.type == "cuda":
                torch.cuda.empty_cache()
    viable = [row for row in results if float(row["peak_gpu_gib"]) <= 90.0]
    if viable:
        best = max(viable, key=lambda row: float(row["samples_per_second"]))
        print("RECOMMENDED " + json.dumps(best, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
