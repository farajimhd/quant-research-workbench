from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch

from research.news_reaction_model.v18.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v18.data import PreparedEpisodeDataset
from research.news_reaction_model.v18.episode_contract import ROLE_NAMES, ROOT_FAMILY_NAMES
from research.news_reaction_model.v18.metrics import EpisodeAccumulator
from research.news_reaction_model.v18.model import NewsReactionModelV18


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
    model = NewsReactionModelV18(model_config).to(runtime_device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    dataset = PreparedEpisodeDataset(
        loader, start=start, end_exclusive=end_exclusive
    )
    accumulator = EpisodeAccumulator()
    pnl = defaultdict(lambda: {"positions": 0, "wins": 0, "gross_pnl": 0.0})
    try:
        for batch in dataset.iter_batches():
            indices = np.asarray(batch.identity["prepared_row_index"], dtype=np.int64)
            anchors = np.asarray(dataset.arrays["anchor_price"][indices], dtype=np.float64)
            roles = np.asarray(dataset.arrays["node_role"][indices], dtype=np.int8)
            families = np.asarray(dataset.arrays["root_family"][indices], dtype=np.int8)
            target_terminal_pct = batch.regression_targets[:, 2].numpy()
            moved = batch.to(runtime_device)
            output = model(moved.x)
            accumulator.add(output, moved)
            predicted = output.direction_logits.argmax(dim=-1).detach().cpu().numpy()
            sides = np.where(predicted == 1, 1.0, np.where(predicted == 2, -1.0, 0.0))
            realized = sides * anchors * target_terminal_pct / 100.0
            for index, value in enumerate(realized):
                if sides[index] == 0:
                    continue
                keys = (
                    "all",
                    f"role/{ROLE_NAMES[int(roles[index])]}",
                    f"root_family/{ROOT_FAMILY_NAMES[int(families[index])]}",
                    "long" if sides[index] > 0 else "short",
                )
                for key in keys:
                    pnl[key]["positions"] += 1
                    pnl[key]["wins"] += int(value > 0)
                    pnl[key]["gross_pnl"] += float(value)
    finally:
        dataset.stop()
    metrics = accumulator.compute("val")
    payload = {
        "checkpoint": str(checkpoint_path),
        "start": start,
        "end_exclusive": end_exclusive,
        "metrics": metrics,
        "terminal_one_share_pnl": {
            key: {
                **values,
                "win_rate": values["wins"] / max(values["positions"], 1),
            }
            for key, values in sorted(pnl.items())
        },
        "dataset_manifest": dataset.manifest,
        "pnl_semantics": (
            "Descriptive one-share terminal P&L: upside prediction buys one share, "
            "downside prediction shorts one share, neutral opens no position, and "
            "the exit is the observed terminal trade of the article-to-boundary interval. "
            "It excludes costs, slippage, overlap, and capital constraints."
        ),
    }
    output = checkpoint_path.parent.parent / "evaluation_v18.json"
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), flush=True)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a V18 episode checkpoint.")
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
