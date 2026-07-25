from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch

from research.news_reaction_model.v17.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v17.data import PreparedNewsResponseDataset
from research.news_reaction_model.v17.metrics import ResponseAccumulator
from research.news_reaction_model.v17.model import NewsResponseModelV17


@torch.no_grad()
def evaluate_checkpoint(
    checkpoint_path: Path,
    *,
    loader_config: LoaderConfig | None = None,
    start: str = "2026-01-01",
    end_exclusive: str = "2027-01-01",
    device: torch.device | None = None,
) -> dict[str, Any]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    loader = loader_config or LoaderConfig(**checkpoint["loader_config"])
    model_config = ModelConfig(**checkpoint["model_config"])
    runtime_device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = NewsResponseModelV17(model_config).to(runtime_device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    dataset = PreparedNewsResponseDataset(
        loader, start=start, end_exclusive=end_exclusive
    )
    accumulator = ResponseAccumulator()
    try:
        for batch in dataset.iter_batches():
            batch = batch.to(runtime_device)
            accumulator.add(model(batch.x), batch)
    finally:
        dataset.stop()
    metrics = accumulator.compute("val")
    payload = {
        "checkpoint": str(checkpoint_path),
        "start": start,
        "end_exclusive": end_exclusive,
        "metrics": metrics,
        "target_manifest": dataset.target_manifest,
    }
    output_path = checkpoint_path.parent.parent / "evaluation_v17.json"
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), flush=True)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a V17 response-archetype checkpoint.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    args = parser.parse_args(argv)
    evaluate_checkpoint(
        args.checkpoint, start=args.start, end_exclusive=args.end_exclusive
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
