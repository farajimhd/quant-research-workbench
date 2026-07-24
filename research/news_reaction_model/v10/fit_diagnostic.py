from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Iterable

import torch

from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v10 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v10.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v10.data import (
    ClickHouseNewsReactionDataset,
    audit_prepared_dataset,
)
from research.news_reaction_model.v10.metrics import OpportunityAccumulator
from research.news_reaction_model.v10.model import NewsReactionModelV10
from research.news_reaction_model.v10.opportunity import OPPORTUNITY_CLASS_NAMES


REPO_ROOT = Path(__file__).resolve().parents[3]
FIT_METRICS = ("accuracy", "balanced_accuracy", "macro_f1", "log_loss", "mean_confidence")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate one V10 checkpoint in eval mode over both its complete training "
            "and chronological validation populations."
        )
    )
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--train-start", default="2019-01-01")
    parser.add_argument("--train-end-exclusive", default="2026-01-01")
    parser.add_argument("--validation-start", default="2026-01-01")
    parser.add_argument("--validation-end-exclusive", default="2027-01-01")
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def evaluate_classification_range(
    model: NewsReactionModelV10,
    loader_config: LoaderConfig,
    *,
    device: torch.device,
    split: str,
    start: str,
    end_exclusive: str,
    amp: bool,
) -> dict[str, Any]:
    audit = audit_prepared_dataset(loader_config, start, end_exclusive)
    dataset = ClickHouseNewsReactionDataset(
        loader_config,
        start=start,
        end_exclusive=end_exclusive,
        shuffle_months=False,
    )
    accumulator = OpportunityAccumulator()
    articles = 0
    started = time.perf_counter()
    model.eval()
    try:
        for batch_index, cpu_batch in enumerate(dataset.iter_batches(), start=1):
            device_batch = cpu_batch.to(device)
            with torch.inference_mode(), torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=device.type == "cuda" and amp,
            ):
                output = model(device_batch.x)
            accumulator.add(output, device_batch.return_targets, device_batch.label_mask)
            articles += cpu_batch.sample_count
            if batch_index == 1 or batch_index % 25 == 0:
                elapsed = time.perf_counter() - started
                print(
                    f"FIT {split} batches={batch_index:,} "
                    f"articles={articles:,}/{audit['rows']:,} "
                    f"rate={articles / max(elapsed, 1e-9):,.0f} articles/s",
                    flush=True,
                )
    finally:
        dataset.stop()
    metrics = accumulator.compute(prefix=split)
    return {
        "split": split,
        "range": [start, end_exclusive],
        "articles": articles,
        "expected_articles": int(audit["rows"]),
        "elapsed_seconds": time.perf_counter() - started,
        "metrics": metrics,
    }


def fit_comparison(
    training: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, float]:
    train_metrics = training["metrics"]
    validation_metrics = validation["metrics"]
    result: dict[str, float] = {}
    for metric in FIT_METRICS:
        train_value = float(train_metrics[f"train/{metric}"])
        validation_value = float(validation_metrics[f"validation/{metric}"])
        result[f"train_{metric}"] = train_value
        result[f"validation_{metric}"] = validation_value
        result[f"train_minus_validation_{metric}"] = train_value - validation_value
    return result


def write_metrics_csv(
    path: Path,
    splits: tuple[dict[str, Any], ...],
) -> None:
    fields = (
        "split",
        "horizon",
        "samples",
        *FIT_METRICS,
        *(f"{name}_support" for name in OPPORTUNITY_CLASS_NAMES),
        *(f"{name}_recall" for name in OPPORTUNITY_CLASS_NAMES),
        *(f"{name}_predicted_share" for name in OPPORTUNITY_CLASS_NAMES),
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for split_result in splits:
            split = str(split_result["split"])
            metrics = split_result["metrics"]
            for horizon in ("overall", *HORIZONS):
                prefix = split if horizon == "overall" else f"{split}/{horizon}"
                row: dict[str, Any] = {
                    "split": split,
                    "horizon": horizon,
                    "samples": metrics[f"{prefix}/samples"],
                }
                for metric in FIT_METRICS:
                    row[metric] = metrics[f"{prefix}/{metric}"]
                for name in OPPORTUNITY_CLASS_NAMES:
                    for suffix in ("support", "recall", "predicted_share"):
                        row[f"{name}_{suffix}"] = metrics[f"{prefix}/{name}/{suffix}"]
                writer.writerow(row)


def diagnose_checkpoint(
    checkpoint: Path,
    *,
    output_dir: Path | None = None,
    train_start: str = "2019-01-01",
    train_end_exclusive: str = "2026-01-01",
    validation_start: str = "2026-01-01",
    validation_end_exclusive: str = "2027-01-01",
    amp: bool = True,
) -> dict[str, Any]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location=device, weights_only=True)
    loader_config = LoaderConfig(**state["config"]["loader"])
    model = NewsReactionModelV10(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    destination = output_dir or checkpoint.parent.parent / "fit_diagnostic"
    destination.mkdir(parents=True, exist_ok=True)

    training = evaluate_classification_range(
        model,
        loader_config,
        device=device,
        split="train",
        start=train_start,
        end_exclusive=train_end_exclusive,
        amp=amp,
    )
    validation = evaluate_classification_range(
        model,
        loader_config,
        device=device,
        split="validation",
        start=validation_start,
        end_exclusive=validation_end_exclusive,
        amp=amp,
    )
    summary: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "checkpoint": str(checkpoint),
        "device": str(device),
        "eval_mode": True,
        "dropout_active": False,
        "amp": bool(amp and device.type == "cuda"),
        "training": training,
        "validation": validation,
        "comparison": fit_comparison(training, validation),
        "interpretation_contract": (
            "Training and validation use the same checkpoint, label contract, metrics, "
            "inference mode, and dropout-disabled model. A small gap with weak scores indicates "
            "underfitting or irreducible labels; a large favorable training gap indicates "
            "overfitting or temporal shift."
        ),
    }
    summary_path = destination / "fit_diagnostic_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    write_metrics_csv(destination / "fit_diagnostic_metrics.csv", (training, validation))
    comparison = summary["comparison"]
    print(
        "COMPLETED "
        f"train_accuracy={comparison['train_accuracy']:.4f} "
        f"validation_accuracy={comparison['validation_accuracy']:.4f} "
        f"accuracy_gap={comparison['train_minus_validation_accuracy']:.4f} "
        f"summary={summary_path}",
        flush=True,
    )
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    diagnose_checkpoint(
        Path(args.checkpoint),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        train_start=args.train_start,
        train_end_exclusive=args.train_end_exclusive,
        validation_start=args.validation_start,
        validation_end_exclusive=args.validation_end_exclusive,
        amp=args.amp,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
