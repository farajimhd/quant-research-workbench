from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import torch

from research.news_reaction_model.v18.episode_contract import (
    ROLE_NAMES,
    ROOT_FAMILY_NAMES,
)
from research.news_reaction_model.v20.evaluate import (
    _decode,
    _duration_bucket,
    _group_metrics,
    _grouped,
)
from research.news_reaction_model.v21.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v21.data import PreparedEpisodeDataset
from research.news_reaction_model.v21.losses import signed_opportunity_torch
from research.news_reaction_model.v21.metrics import HierarchicalAccumulator
from research.news_reaction_model.v21.model import NewsReactionModelV21
from research.news_reaction_model.v21.targets import (
    DIRECTION_NAMES,
    MAGNITUDE_BUCKET_NAMES,
    PRICE_REGIME_NAMES,
    PUBLICATION_SESSION_NAMES,
    RETURN_BUCKET_NAMES,
    TrainingStatistics,
)


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
    if checkpoint.get("model_version") != "v21":
        raise RuntimeError(f"Expected V21 checkpoint at {checkpoint_path}.")
    loader = loader_config or LoaderConfig(**checkpoint["loader_config"])
    statistics = TrainingStatistics.from_dict(checkpoint["training_statistics"])
    model = NewsReactionModelV21(
        ModelConfig(**checkpoint["model_config"]), statistics
    )
    runtime_device = device or torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )
    model.to(runtime_device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    dataset = PreparedEpisodeDataset(
        loader,
        start=start,
        end_exclusive=end_exclusive,
    )
    accumulator = HierarchicalAccumulator(statistics)
    collected: dict[str, list[np.ndarray]] = {
        key: []
        for key in (
            "indices",
            "direction_true",
            "direction_pred",
            "direction_confidence",
            "direction_probabilities",
            "signed_return_true",
            "terminal_return_true",
            "expected_return",
            "expected_up_return",
            "expected_down_return",
            "return_bucket_pred",
        )
    }
    try:
        for batch in dataset.iter_batches():
            moved = batch.to(runtime_device)
            output = model(moved.x)
            accumulator.add(output, moved)
            collected["indices"].append(
                np.asarray(batch.identity["prepared_row_index"], dtype=np.int64)
            )
            collected["direction_true"].append(batch.direction.numpy())
            collected["direction_pred"].append(
                output.direction_probabilities.argmax(-1).cpu().numpy()
            )
            collected["direction_confidence"].append(
                output.direction_probabilities.amax(-1).cpu().numpy()
            )
            collected["direction_probabilities"].append(
                output.direction_probabilities.cpu().numpy()
            )
            collected["signed_return_true"].append(
                signed_opportunity_torch(batch).numpy()
            )
            collected["terminal_return_true"].append(
                batch.regression_targets[:, 2].numpy()
            )
            collected["expected_return"].append(output.expected_return.cpu().numpy())
            collected["expected_up_return"].append(
                output.expected_up_return.cpu().numpy()
            )
            collected["expected_down_return"].append(
                output.expected_down_return.cpu().numpy()
            )
            collected["return_bucket_pred"].append(
                output.joint_return_probabilities.argmax(-1).cpu().numpy()
            )
        values = {key: np.concatenate(parts) for key, parts in collected.items()}
        rows = values["indices"].astype(np.int64)
        source = np.asarray(dataset.arrays["source_index"][rows], dtype=np.int64)
        values["anchor"] = np.asarray(
            dataset.arrays["anchor_price"][rows], dtype=np.float64
        )
        episode_counts = Counter(_decode(value) for value in dataset.arrays["episode_id"])
        episode_ids = np.asarray(
            [_decode(value) for value in dataset.arrays["episode_id"][rows]],
            dtype=object,
        )
        positions = np.asarray(dataset.arrays["node_position"][rows], dtype=np.int64)
        structure = np.asarray(
            [
                "singleton"
                if episode_counts[episode] == 1
                else "multi_root"
                if position == 0
                else "multi_followup"
                for episode, position in zip(episode_ids, positions, strict=True)
            ],
            dtype=object,
        )
        roles = np.asarray(
            [ROLE_NAMES[int(value)] for value in dataset.arrays["node_role"][rows]],
            dtype=object,
        )
        families = np.asarray(
            [
                ROOT_FAMILY_NAMES[int(value)]
                for value in dataset.arrays["root_family"][rows]
            ],
            dtype=object,
        )
        sessions = np.asarray(PUBLICATION_SESSION_NAMES, dtype=object)[
            np.asarray(dataset.v15["time_features"][source, :4]).argmax(axis=1)
        ]
        prices = np.asarray(PRICE_REGIME_NAMES, dtype=object)[
            np.select(
                (
                    values["anchor"] < 1,
                    values["anchor"] < 5,
                    values["anchor"] < 10,
                    values["anchor"] < 20,
                ),
                (0, 1, 2, 3),
                default=4,
            )
        ]
        duration_seconds = (
            np.asarray(dataset.arrays["target_end_us"][rows], dtype=np.int64)
            - np.asarray(dataset.arrays["target_start_us"][rows], dtype=np.int64)
        ) / 1_000_000.0
        durations = np.asarray(
            [_duration_bucket(value) for value in duration_seconds],
            dtype=object,
        )
        confidence: dict[str, Any] = {}
        for threshold in (0.40, 0.50, 0.60, 0.70, 0.80):
            selected = np.flatnonzero(
                (values["direction_confidence"] >= threshold)
                & (values["direction_pred"] != 0)
            )
            confidence[f"{threshold:.2f}"] = {
                "coverage": float(selected.size / max(rows.size, 1)),
                **_group_metrics(selected, values),
            }
        payload = {
            "checkpoint": str(checkpoint_path),
            "selected_checkpoint_epoch": int(checkpoint["epoch"]),
            "start": start,
            "end_exclusive": end_exclusive,
            "metrics": accumulator.compute("test"),
            "cohorts": {
                "episode_structure": _grouped(structure, values),
                "node_role": _grouped(roles, values),
                "root_family": _grouped(families, values),
                "publication_session_et": _grouped(sessions, values),
                "anchor_price_bucket": _grouped(prices, values),
                "interval_wall_duration": _grouped(durations, values),
            },
            "direction_confidence_sweep": confidence,
            "prediction_contract": {
                "factorization": (
                    "P(direction) multiplied by "
                    "P(magnitude bucket conditioned on upside/downside)"
                ),
                "direction_probability_order": list(DIRECTION_NAMES),
                "magnitude_bucket_order": list(MAGNITUDE_BUCKET_NAMES),
                "signed_return_bucket_order": list(RETURN_BUCKET_NAMES),
                "return_values": (
                    "Expected signed dominant episode excursion in percent. "
                    "The unconditional value is P(upside)*E[magnitude|upside] "
                    "- P(downside)*E[magnitude|downside]."
                ),
            },
            "dataset_manifest": dataset.manifest,
            "training_statistics": statistics.as_dict(),
            "pnl_semantics": (
                "Terminal P&L is a descriptive one-share diagnostic using the "
                "predicted direction and observed episode terminal return. It "
                "excludes costs, fills, overlap, capital constraints, and does "
                "not treat predicted excursion magnitude as realized P&L."
            ),
        }
    finally:
        dataset.stop()
    output_path = checkpoint_path.parent.parent / "evaluation_v21.json"
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), flush=True)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a V21 checkpoint.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    args = parser.parse_args(argv)
    evaluate_checkpoint(
        args.checkpoint,
        start=args.start,
        end_exclusive=args.end_exclusive,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
