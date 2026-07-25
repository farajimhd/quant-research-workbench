from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch

from research.news_reaction_model.v17.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v17.data import PreparedNewsResponseDataset
from research.news_reaction_model.v17.losses import compute_loss
from research.news_reaction_model.v17.model import NewsResponseModelV17


def main(argv: list[str] | None = None) -> int:
    defaults = LoaderConfig()
    parser = argparse.ArgumentParser(description="Profile V17 model and batch sizes.")
    parser.add_argument("--prepared-root", default=str(defaults.prepared_dataset_root))
    parser.add_argument("--target-root", default=str(defaults.target_root))
    parser.add_argument("--model-sizes", default="256,384,512")
    parser.add_argument("--batch-sizes", default="512,1024,2048,4096")
    parser.add_argument("--layers", default="2,4")
    parser.add_argument("--output", type=Path, default=Path("runtime/news-reaction-model/v17/profile.json"))
    args = parser.parse_args(argv)
    if not torch.cuda.is_available():
        raise RuntimeError("V17 profiling requires CUDA.")
    device = torch.device("cuda")
    maximum_batch = max(int(value) for value in args.batch_sizes.split(","))
    loader = LoaderConfig(
        prepared_dataset_root=Path(args.prepared_root),
        target_root=Path(args.target_root),
        batch_size=maximum_batch,
    )
    dataset = PreparedNewsResponseDataset(
        loader, start=loader.train_start, end_exclusive=loader.train_end_exclusive
    )
    try:
        source = next(dataset.iter_batches()).to(device)
    finally:
        dataset.stop()
    results: list[dict[str, float | int | str]] = []
    for d_model in (int(value) for value in args.model_sizes.split(",")):
        for layer_count in (int(value) for value in args.layers.split(",")):
            for batch_size in (int(value) for value in args.batch_sizes.split(",")):
                model = NewsResponseModelV17(
                    ModelConfig(
                        d_model=d_model,
                        hidden_dim=d_model,
                        layers=layer_count,
                    )
                ).to(device)
                optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
                indices = slice(0, batch_size)
                batch = type(source)(
                    x={key: value[indices] for key, value in source.x.items()},
                    direction=source.direction[indices],
                    path=source.path[indices],
                    flow=source.flow[indices],
                    window_mask=source.window_mask[indices],
                    persistence=source.persistence[indices],
                    persistence_mask=source.persistence_mask[indices],
                    raw_metrics=source.raw_metrics[indices],
                    identity=source.identity,
                    sample_count=batch_size,
                )
                try:
                    torch.cuda.reset_peak_memory_stats()
                    started = time.perf_counter()
                    with torch.autocast("cuda", dtype=torch.bfloat16):
                        loss = compute_loss(model(batch.x), batch).loss
                    loss.backward()
                    optimizer.step()
                    torch.cuda.synchronize()
                    results.append(
                        {
                            "d_model": d_model,
                            "layers": layer_count,
                            "batch_size": batch_size,
                            "status": "ok",
                            "step_seconds": time.perf_counter() - started,
                            "peak_gib": torch.cuda.max_memory_allocated() / 2**30,
                        }
                    )
                except torch.cuda.OutOfMemoryError:
                    results.append(
                        {
                            "d_model": d_model,
                            "layers": layer_count,
                            "batch_size": batch_size,
                            "status": "oom",
                        }
                    )
                finally:
                    del model, optimizer
                    torch.cuda.empty_cache()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
