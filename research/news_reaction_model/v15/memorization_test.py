from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import torch

from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v15.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v15.data import (
    NewsReactionBatch,
    PreparedNewsReactionDataset,
    batch_from_indices,
)
from research.news_reaction_model.v15.losses import compute_loss
from research.news_reaction_model.v15.metrics import (
    OpportunityAccumulator,
    TrainingLossAccumulator,
)
from research.news_reaction_model.v15.model import NewsReactionModelV15
from research.news_reaction_model.v15.opportunity import (
    OPPORTUNITY_CLASS_NAMES,
    opportunity_targets,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Train a fresh V15 model on one deterministic subset and evaluate that exact "
            "same subset after every epoch to test model and optimizer memorization capacity."
        )
    )
    parser.add_argument("--reference-checkpoint", required=True)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end-exclusive", default="2026-01-01")
    parser.add_argument("--subset-size", type=int, default=10_000)
    parser.add_argument("--subset-seed", type=int, default=17)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--grad-clip-norm", type=float, default=1.0)
    parser.add_argument("--target-accuracy", type=float, default=0.99)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def load_deterministic_subset(
    config: LoaderConfig,
    *,
    start: str,
    end_exclusive: str,
    subset_size: int,
    subset_seed: int,
) -> NewsReactionBatch:
    dataset = PreparedNewsReactionDataset(
        config,
        start=start,
        end_exclusive=end_exclusive,
    )
    available = dataset.upper - dataset.lower
    if available < subset_size:
        raise RuntimeError(
            f"Requested {subset_size:,} prepared articles but only {available:,} "
            f"exist in [{start}, {end_exclusive})."
        )
    scored: list[tuple[bytes, int]] = []
    seed_bytes = str(int(subset_seed)).encode("ascii")
    for index in range(dataset.lower, dataset.upper):
        digest = hashlib.blake2b(
            seed_bytes
            + b"\0"
            + bytes(dataset.arrays["canonical_news_id"][index]).rstrip(b"\0")
            + b"\0"
            + bytes(dataset.arrays["ticker"][index]).rstrip(b"\0")
            + b"\0"
            + bytes(dataset.arrays["published_at_utc"][index]).rstrip(b"\0"),
            digest_size=16,
        ).digest()
        scored.append((digest, index))
    selected = np.asarray(
        [index for _, index in sorted(scored)[:subset_size]],
        dtype=np.int64,
    )
    return batch_from_indices(
        dataset.arrays,
        selected,
        config,
    )


def slice_batch(source: NewsReactionBatch, indices: torch.Tensor) -> NewsReactionBatch:
    index_values = indices.tolist()
    return NewsReactionBatch(
        x={key: value[indices] for key, value in source.x.items()},
        return_targets=source.return_targets[indices],
        label_mask=source.label_mask[indices],
        identity={
            key: [values[index] for index in index_values]
            for key, values in source.identity.items()
        },
        sample_count=len(index_values),
    )


@torch.no_grad()
def evaluate_same_subset(
    model: NewsReactionModelV15,
    subset: NewsReactionBatch,
    *,
    device: torch.device,
    batch_size: int,
    amp: bool,
) -> dict[str, float]:
    model.eval()
    accumulator = OpportunityAccumulator()
    for offset in range(0, subset.sample_count, batch_size):
        indices = torch.arange(offset, min(offset + batch_size, subset.sample_count))
        batch = slice_batch(subset, indices).to(device)
        with torch.autocast(
            device_type=device.type,
            dtype=torch.bfloat16,
            enabled=device.type == "cuda" and amp,
        ):
            output = model(batch.x)
        accumulator.add(output, batch.return_targets, batch.label_mask)
    return accumulator.compute(prefix="memorization")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def subset_audit(subset: NewsReactionBatch) -> dict[str, Any]:
    identities = zip(
        subset.identity["canonical_news_id"],
        subset.identity["ticker"],
        subset.identity["published_at_utc"],
    )
    identity_text = "\n".join("\t".join(map(str, identity)) for identity in identities)
    targets = opportunity_targets(subset.return_targets, subset.label_mask)
    class_counts = {name: 0 for name in OPPORTUNITY_CLASS_NAMES}
    valid_labels = 0
    for actual in targets.values():
        valid = actual >= 0
        valid_values = actual[valid]
        valid_labels += int(valid_values.numel())
        for class_index, name in enumerate(OPPORTUNITY_CLASS_NAMES):
            class_counts[name] += int((valid_values == class_index).sum())
    return {
        "identity_sha256": hashlib.sha256(identity_text.encode("utf-8")).hexdigest(),
        "articles": subset.sample_count,
        "valid_labels": valid_labels,
        "class_counts": class_counts,
    }


def run_memorization_test(args: argparse.Namespace) -> dict[str, Any]:
    if args.subset_size < 2:
        raise ValueError("--subset-size must be at least 2")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive")
    if args.epochs < 1:
        raise ValueError("--epochs must be positive")
    if not 0.0 < args.target_accuracy <= 1.0:
        raise ValueError("--target-accuracy must be in (0, 1]")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.serialization.safe_globals([type(Path())]):
        reference = torch.load(
            Path(args.reference_checkpoint),
            map_location="cpu",
            weights_only=True,
        )
    loader_config = LoaderConfig(**reference["config"]["loader"])
    model_config = ModelConfig(**reference["config"]["model"])
    set_seed(args.seed)
    subset = load_deterministic_subset(
        loader_config,
        start=args.start,
        end_exclusive=args.end_exclusive,
        subset_size=args.subset_size,
        subset_seed=args.subset_seed,
    )
    audit = subset_audit(subset)
    model = NewsReactionModelV15(model_config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
        foreach=True,
    )
    amp_enabled = device.type == "cuda" and args.amp
    destination = (
        Path(args.output_dir)
        if args.output_dir
        else Path(args.reference_checkpoint).parent.parent / "memorization_test"
    )
    destination.mkdir(parents=True, exist_ok=True)
    metrics_path = destination / "memorization_metrics.jsonl"
    metrics_path.write_text("", encoding="utf-8")
    history: list[dict[str, Any]] = []
    started = time.perf_counter()
    best_accuracy = -1.0
    best_epoch = 0
    reached_target = False

    for epoch in range(1, args.epochs + 1):
        model.train()
        generator = torch.Generator().manual_seed(args.seed + epoch)
        order = torch.randperm(subset.sample_count, generator=generator)
        training_metrics = TrainingLossAccumulator()
        for offset in range(0, subset.sample_count, args.batch_size):
            indices = order[offset : offset + args.batch_size]
            batch = slice_batch(subset, indices).to(device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=amp_enabled,
            ):
                output = model(batch.x)
                result = compute_loss(output, batch)
            result.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip_norm)
            optimizer.step()
            training_metrics.add(result)

        train_summary = training_metrics.compute("train")
        evaluation = evaluate_same_subset(
            model,
            subset,
            device=device,
            batch_size=args.batch_size,
            amp=args.amp,
        )
        accuracy = float(evaluation["memorization/accuracy"])
        record: dict[str, Any] = {
            "epoch": epoch,
            "train_mode_loss": train_summary["train/loss"],
            "train_mode_micro_log_loss": train_summary["train/micro_log_loss"],
            "train_mode_accuracy": train_summary["train/accuracy"],
            "eval_mode_accuracy": accuracy,
            "eval_mode_balanced_accuracy": float(
                evaluation["memorization/balanced_accuracy"]
            ),
            "eval_mode_macro_f1": float(evaluation["memorization/macro_f1"]),
            "eval_mode_log_loss": float(evaluation["memorization/log_loss"]),
            "eval_mode_mean_confidence": float(
                evaluation["memorization/mean_confidence"]
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
        history.append(record)
        with metrics_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_epoch = epoch
        print(
            f"MEMORIZE epoch={epoch}/{args.epochs} "
            f"train_accuracy={record['train_mode_accuracy']:.4f} "
            f"eval_accuracy={accuracy:.4f} "
            f"eval_log_loss={record['eval_mode_log_loss']:.4f}",
            flush=True,
        )
        if accuracy >= args.target_accuracy:
            reached_target = True
            break

    summary: dict[str, Any] = {
        "diagnostic": "fixed_subset_memorization",
        "reference_checkpoint": str(args.reference_checkpoint),
        "reference_weights_loaded": False,
        "architecture_loaded_from_reference": True,
        "fresh_random_initialization": True,
        "same_subset_used_for_training_and_evaluation": True,
        "dropout_active_during_training": model_config.dropout > 0,
        "dropout_active_during_evaluation": False,
        "device": str(device),
        "amp": amp_enabled,
        "range": [args.start, args.end_exclusive],
        "subset_size": args.subset_size,
        "subset_seed": args.subset_seed,
        "subset_audit": audit,
        "training_seed": args.seed,
        "batch_size": args.batch_size,
        "optimizer": {
            "name": "AdamW",
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
            "grad_clip_norm": args.grad_clip_norm,
            "scheduler": "none",
        },
        "maximum_epochs": args.epochs,
        "epochs_completed": len(history),
        "target_accuracy": args.target_accuracy,
        "reached_target": reached_target,
        "best_epoch": best_epoch,
        "best_eval_accuracy": best_accuracy,
        "final": history[-1],
        "elapsed_seconds": time.perf_counter() - started,
        "interpretation_contract": (
            "Reaching near-perfect eval-mode accuracy proves the architecture and optimizer "
            "can memorize this fixed label population. Failure to do so does not by itself prove "
            "a code defect, but identifies capacity, regularization, optimization, duplicate-input "
            "label conflicts, or target inconsistency for focused investigation."
        ),
    }
    summary_path = destination / "memorization_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    print(
        f"COMPLETED best_accuracy={best_accuracy:.4f} "
        f"reached_target={reached_target} summary={summary_path}",
        flush=True,
    )
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    run_memorization_test(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
