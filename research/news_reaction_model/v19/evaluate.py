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
from research.news_reaction_model.v19.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v19.data import PreparedEpisodeDataset
from research.news_reaction_model.v19.metrics import EpisodeAccumulator
from research.news_reaction_model.v19.model import NewsReactionModelV19
from research.news_reaction_model.v19.targets import (
    DIRECTION_NAMES,
    FLOW_NAMES,
    PATH_NAMES,
    PRICE_REGIME_NAMES,
    PUBLICATION_SESSION_NAMES,
    TrainingStatistics,
)


def _decode(value: object) -> str:
    return bytes(value).rstrip(b"\x00").decode("utf-8")


def _macro_metrics(
    truth: np.ndarray,
    predicted: np.ndarray,
    classes: int,
) -> tuple[float, float, float]:
    matrix = np.zeros((classes, classes), dtype=np.int64)
    np.add.at(matrix, (truth, predicted), 1)
    accuracy = float(np.trace(matrix) / max(int(matrix.sum()), 1))
    recalls = np.diag(matrix) / np.maximum(matrix.sum(axis=1), 1)
    precision = np.diag(matrix) / np.maximum(matrix.sum(axis=0), 1)
    class_f1 = 2 * precision * recalls / np.maximum(precision + recalls, 1e-12)
    return accuracy, float(np.mean(recalls)), float(np.mean(class_f1))


def _group_metrics(index: np.ndarray, values: dict[str, np.ndarray]) -> dict[str, Any]:
    result: dict[str, Any] = {"samples": int(index.size)}
    if not index.size:
        return result
    for name, classes in (("direction", 3), ("path", 6), ("flow", 3)):
        accuracy, balanced, macro_f1 = _macro_metrics(
            values[f"{name}_true"][index],
            values[f"{name}_pred"][index],
            classes,
        )
        result[f"{name}_accuracy"] = accuracy
        result[f"{name}_balanced_accuracy"] = balanced
        result[f"{name}_macro_f1"] = macro_f1
    error = values["regression_pred"][index] - values["regression_true"][index]
    for column, name in enumerate(("high", "low", "terminal")):
        result[f"{name}_mae_pct"] = float(np.mean(np.abs(error[:, column])))
    direction = values["direction_pred"][index]
    path = values["path_pred"][index]
    sides = np.where(direction == 1, 1.0, np.where(direction == 2, -1.0, 0.0))
    realized = (
        sides
        * values["anchor"][index]
        * values["regression_true"][index, 2]
        / 100.0
    )
    positioned = sides != 0
    sustained = positioned & (path == 1)
    for name, mask in (("terminal_all", positioned), ("terminal_sustained", sustained)):
        result[f"{name}_positions"] = int(mask.sum())
        result[f"{name}_gross_pnl"] = float(realized[mask].sum())
        result[f"{name}_win_rate"] = (
            float(np.mean(realized[mask] > 0)) if mask.any() else 0.0
        )
        result[f"{name}_pnl_per_position"] = (
            float(np.mean(realized[mask])) if mask.any() else 0.0
        )
    return result


def _grouped(
    groups: np.ndarray,
    values: dict[str, np.ndarray],
) -> dict[str, dict[str, Any]]:
    return {
        str(group): _group_metrics(np.flatnonzero(groups == group), values)
        for group in sorted(set(groups.tolist()))
    }


def _duration_bucket(seconds: float) -> str:
    if seconds <= 5 * 60:
        return "up_to_5m"
    if seconds <= 30 * 60:
        return "5_to_30m"
    if seconds <= 2 * 3600:
        return "30m_to_2h"
    if seconds <= 6.5 * 3600:
        return "2h_to_6_5h"
    if seconds <= 24 * 3600:
        return "6_5h_to_24h"
    if seconds <= 3 * 86400:
        return "1_to_3d"
    return "over_3d"


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
    statistics = TrainingStatistics.from_dict(checkpoint["training_statistics"])
    model = NewsReactionModelV19(
        ModelConfig(**checkpoint["model_config"]),
        statistics,
    )
    runtime_device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(runtime_device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()
    dataset = PreparedEpisodeDataset(
        loader,
        start=start,
        end_exclusive=end_exclusive,
    )
    accumulator = EpisodeAccumulator(statistics.regression_training_median)
    collected: dict[str, list[np.ndarray]] = {
        key: []
        for key in (
            "indices",
            "direction_true",
            "path_true",
            "flow_true",
            "regression_true",
            "direction_pred",
            "path_pred",
            "flow_pred",
            "regression_pred",
            "direction_confidence",
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
            collected["path_true"].append(batch.path.numpy())
            collected["flow_true"].append(batch.flow.numpy())
            collected["regression_true"].append(batch.regression_targets.numpy())
            collected["direction_pred"].append(
                output.direction_logits.argmax(-1).cpu().numpy()
            )
            collected["path_pred"].append(output.path_logits.argmax(-1).cpu().numpy())
            collected["flow_pred"].append(output.flow_logits.argmax(-1).cpu().numpy())
            collected["regression_pred"].append(output.regression.cpu().numpy())
            collected["direction_confidence"].append(
                torch.softmax(output.direction_logits.float(), dim=-1)
                .amax(-1)
                .cpu()
                .numpy()
            )
        values = {key: np.concatenate(parts) for key, parts in collected.items()}
        rows = values["indices"].astype(np.int64)
        source = np.asarray(dataset.arrays["source_index"][rows], dtype=np.int64)
        values["anchor"] = np.asarray(dataset.arrays["anchor_price"][rows], dtype=np.float64)
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
            "dataset_manifest": dataset.manifest,
            "training_statistics": statistics.as_dict(),
            "class_names": {
                "direction": DIRECTION_NAMES,
                "path": PATH_NAMES,
                "flow": FLOW_NAMES,
            },
            "pnl_semantics": (
                "Descriptive one-share terminal P&L only. The sustained-only view "
                "opens positions only when predicted path is sustained. Both exclude "
                "costs, fills, overlap, and capital constraints."
            ),
        }
    finally:
        dataset.stop()
    output_path = checkpoint_path.parent.parent / "evaluation_v19.json"
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), flush=True)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a V19 checkpoint.")
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
