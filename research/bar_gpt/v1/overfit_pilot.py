from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import torch

from research.bar_gpt.v1.audit_offline_shards import DEFAULT_PILOT_ROOT
from research.bar_gpt.v1.config import BarGPTConfig, ExperimentConfig, TrainConfig
from research.bar_gpt.v1.data import AUTOREGRESSIVE_VIEW_NAMES, BarGPTBatch
from research.bar_gpt.v1.metrics import ValidationAccumulator
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.offline_shards import (
    collate_compiled_blocks,
    CompiledBlock,
    discover_offline_units,
    load_shard_storage_config,
    load_shard,
    materialize_block,
)
from research.bar_gpt.v1.train import _forward
from research.bar_gpt.v1.targets import DIRECTION_TARGET_COUNT, DIRECTION_TARGET_NAMES


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deliberately overfit a tiny certified v12 sparse-event shard panel."
    )
    parser.add_argument("--shard-root", type=Path, default=DEFAULT_PILOT_ROOT)
    parser.add_argument("--tickers", default="AAPL,GOOGL")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2019-02-01")
    parser.add_argument("--max-blocks", type=int, default=2)
    parser.add_argument("--origins-per-block", type=int, default=256)
    parser.add_argument("--ar-transitions-per-view", type=int, default=256)
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--minimum-loss-improvement", type=float, default=0.80)
    parser.add_argument("--minimum-direction-balanced-accuracy", type=float, default=0.90)
    parser.add_argument("--minimum-direction-mcc", type=float, default=0.80)
    parser.add_argument("--minimum-direction-examples", type=int, default=32)
    parser.add_argument("--minimum-direction-class-examples", type=int, default=8)
    parser.add_argument("--minimum-ar-views", type=int, default=4)
    parser.add_argument("--d-model", type=int, default=384)
    parser.add_argument("--n-layers", type=int, default=8)
    parser.add_argument("--n-heads", type=int, default=8)
    parser.add_argument("--n-kv-heads", type=int, default=4)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if any(int(value) <= 0 for value in (
        args.max_blocks, args.origins_per_block, args.ar_transitions_per_view,
        args.steps, args.minimum_direction_examples, args.minimum_direction_class_examples,
        args.minimum_ar_views,
    )) or args.learning_rate <= 0:
        parser.error("population limits, steps, gate counts, and learning rate must be positive")
    return args


def _limit_block_origins(block: CompiledBlock, maximum: int) -> CompiledBlock:
    """Crop a compiled block to a genuinely small, causally complete population."""
    count = min(int(maximum), int(block.origin_indices.numel()))
    if count <= 0:
        raise ValueError("overfit block contains no origins")
    selected_positions = _evenly_spaced(torch.arange(block.origin_indices.numel()), count)
    origin_indices = block.origin_indices[selected_positions].clone()
    asof_indices = {
        name: value[selected_positions].clone() for name, value in block.asof_indices.items()
    }
    view_lengths: dict[str, int] = {}
    for name, view in block.views.items():
        if name == "1s":
            last = int(origin_indices[-1])
        else:
            selected = asof_indices[name]
            valid = selected[selected >= 0]
            last = int(valid.max()) if valid.numel() else 0
        view_lengths[name] = min(int(view.shape[0]), max(1, last + 1))
    return replace(
        block,
        views={name: value[: view_lengths[name]].clone() for name, value in block.views.items()},
        view_start_us={name: value[: view_lengths[name]].clone() for name, value in block.view_start_us.items()},
        view_end_us={name: value[: view_lengths[name]].clone() for name, value in block.view_end_us.items()},
        view_available_at_us={
            name: value[: view_lengths[name]].clone() for name, value in block.view_available_at_us.items()
        },
        origin_indices=origin_indices,
        origin_timestamps_us=block.origin_timestamps_us[selected_positions].clone(),
        asof_indices=asof_indices,
        autoregressive_targets={
            name: value[: max(0, view_lengths[name] - 1)].clone()
            for name, value in block.autoregressive_targets.items()
        },
        autoregressive_mask={
            name: value[: max(0, view_lengths[name] - 1)].clone()
            for name, value in block.autoregressive_mask.items()
        },
        horizon_targets=block.horizon_targets[selected_positions].clone(),
        horizon_mask=block.horizon_mask[selected_positions].clone(),
    )


def _evenly_spaced(indices: torch.Tensor, count: int) -> torch.Tensor:
    row_count = int(indices.shape[0])
    if count >= row_count:
        return indices
    positions = torch.linspace(0, row_count - 1, steps=count).round().long()
    return indices[positions]


def _limit_ar_transitions(batch: BarGPTBatch, maximum: int, neutral_bps: float) -> None:
    """Keep a bounded, approximately class-balanced subset under the production loss."""
    threshold = math.asinh(float(neutral_bps) / 100.0)
    for name in AUTOREGRESSIVE_VIEW_NAMES:
        original = batch.autoregressive_mask[name]
        keep = torch.zeros_like(original)
        row_keep = torch.zeros_like(original[..., 0])
        for target_index in range(DIRECTION_TARGET_COUNT):
            target = batch.autoregressive_targets[name][..., target_index]
            valid = original[..., target_index] & (target.abs() > threshold)
            positive = torch.nonzero(valid & (target > threshold), as_tuple=False)
            negative = torch.nonzero(valid & (target < -threshold), as_tuple=False)
            positive_count = min(int(positive.shape[0]), int(maximum) // 2)
            negative_count = min(int(negative.shape[0]), int(maximum) // 2)
            remaining = int(maximum) - positive_count - negative_count
            if remaining > 0:
                positive_count += min(remaining, int(positive.shape[0]) - positive_count)
                remaining = int(maximum) - positive_count - negative_count
                negative_count += min(remaining, int(negative.shape[0]) - negative_count)
            channel_keep = torch.zeros_like(valid)
            for selected in (
                _evenly_spaced(positive, positive_count),
                _evenly_spaced(negative, negative_count),
            ):
                if selected.numel():
                    channel_keep[selected[:, 0], selected[:, 1]] = True
            keep[..., target_index] = original[..., target_index] & channel_keep
            row_keep |= channel_keep
        keep[..., DIRECTION_TARGET_COUNT:] = original[..., DIRECTION_TARGET_COUNT:] & row_keep.unsqueeze(-1)
        batch.autoregressive_mask[name] = keep


def _direction_support(batch: BarGPTBatch, neutral_bps: float) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    threshold = math.asinh(float(neutral_bps) / 100.0)
    physical: dict[str, dict[str, int]] = {}
    assert batch.horizon_targets is not None and batch.horizon_mask is not None
    for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
        for horizon_index, horizon_us in enumerate(batch.horizons_us):
            target = batch.horizon_targets[:, :, horizon_index, target_index]
            valid = batch.horizon_mask[:, :, horizon_index, target_index] & batch.origin_mask
            directional = valid & (target.abs() > threshold)
            physical[f"{target_name}/{horizon_us // 1_000_000}s"] = {
                "total": int(directional.sum()),
                "up": int((directional & (target > threshold)).sum()),
                "down": int((directional & (target < -threshold)).sum()),
            }
    autoregressive: dict[str, dict[str, int]] = {}
    for name in AUTOREGRESSIVE_VIEW_NAMES:
        for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
            target = batch.autoregressive_targets[name][..., target_index]
            valid = batch.autoregressive_mask[name][..., target_index]
            directional = valid & (target.abs() > threshold)
            autoregressive[f"{name}/{target_name}"] = {
                "total": int(directional.sum()),
                "up": int((directional & (target > threshold)).sum()),
                "down": int((directional & (target < -threshold)).sum()),
            }
    return physical, autoregressive


def _score_direction_gate(
    metrics: dict[str, float],
    *,
    namespace: str,
    physical_support: dict[str, dict[str, int]],
    ar_support: dict[str, dict[str, int]],
    minimum_examples: int,
    minimum_class_examples: int,
    minimum_balanced: float,
    minimum_mcc: float,
    minimum_ar_views: int,
) -> tuple[bool, list[dict[str, object]], list[str]]:
    records: list[dict[str, object]] = []
    violations: list[str] = []
    for label, support in physical_support.items():
        target_name, horizon = label.split("/")
        metric_name = target_name.removesuffix("_return")
        balanced = float(metrics[f"{namespace}_{metric_name}_direction/balanced_accuracy_{horizon}"])
        mcc = float(metrics[f"{namespace}_{metric_name}_direction_quality/mcc_{horizon}"])
        eligible = support["total"] >= minimum_examples and min(support["up"], support["down"]) >= minimum_class_examples
        passed = not eligible or (
            math.isfinite(balanced)
            and math.isfinite(mcc)
            and balanced >= minimum_balanced
            and mcc >= minimum_mcc
        )
        records.append({"task": f"physical/{label}", **support, "eligible": eligible, "balanced_accuracy": balanced, "mcc": mcc, "passed": passed})
        if eligible and not passed:
            violations.append(f"physical/{label}: support={support} balanced={balanced:.3f} mcc={mcc:.3f}")
    eligible_ar_by_target = {target_name: 0 for target_name in DIRECTION_TARGET_NAMES}
    for label, support in ar_support.items():
        name, target_name = label.split("/")
        balanced = float(metrics[f"{namespace}_ar_{target_name}_direction_balanced/balanced_accuracy_{name}"])
        mcc = float(metrics[f"{namespace}_ar_{target_name}_direction_mcc/mcc_{name}"])
        eligible = support["total"] >= minimum_examples and min(support["up"], support["down"]) >= minimum_class_examples
        eligible_ar_by_target[target_name] += int(eligible)
        passed = not eligible or (math.isfinite(balanced) and math.isfinite(mcc) and balanced >= minimum_balanced and mcc >= minimum_mcc)
        records.append({"task": f"autoregressive/{label}", **support, "eligible": eligible, "balanced_accuracy": balanced, "mcc": mcc, "passed": passed})
        if eligible and not passed:
            violations.append(f"autoregressive/{label}: support={support} balanced={balanced:.3f} mcc={mcc:.3f}")
    for target_name, eligible_views in eligible_ar_by_target.items():
        if eligible_views < minimum_ar_views:
            violations.append(
                f"autoregressive/{target_name}: only {eligible_views} eligible views; "
                f"require {minimum_ar_views}"
            )
    return not violations, records, violations


def _quick_balanced_accuracy(logits: torch.Tensor, target: torch.Tensor, mask: torch.Tensor, threshold: float) -> float:
    directional = mask & (target.abs() > threshold)
    positive = target > threshold
    predicted = logits.detach() > 0
    positive_count = (directional & positive).sum()
    negative_count = (directional & ~positive).sum()
    if not bool(positive_count) or not bool(negative_count):
        return float("nan")
    true_positive_rate = (directional & positive & predicted).sum().float() / positive_count
    true_negative_rate = (directional & ~positive & ~predicted).sum().float() / negative_count
    return float((true_positive_rate + true_negative_rate) * 0.5)


def _quick_direction_scores(output, batch: BarGPTBatch, neutral_bps: float) -> tuple[float, float]:
    threshold = math.asinh(float(neutral_bps) / 100.0)
    assert output.horizon_direction_logits is not None
    assert batch.horizon_targets is not None and batch.horizon_mask is not None
    physical = _quick_balanced_accuracy(
        output.horizon_direction_logits,
        batch.horizon_targets[..., :DIRECTION_TARGET_COUNT],
        batch.horizon_mask[..., :DIRECTION_TARGET_COUNT] & batch.origin_mask[:, :, None, None],
        threshold,
    )
    ar_logits = []
    ar_targets = []
    ar_masks = []
    for name in AUTOREGRESSIVE_VIEW_NAMES:
        logits = output.autoregressive_direction_logits[name]
        ar_logits.append(logits.reshape(-1))
        ar_targets.append(
            batch.autoregressive_targets[name][:, : logits.shape[1], :DIRECTION_TARGET_COUNT].reshape(-1)
        )
        ar_masks.append(
            batch.autoregressive_mask[name][:, : logits.shape[1], :DIRECTION_TARGET_COUNT].reshape(-1)
        )
    autoregressive = _quick_balanced_accuracy(
        torch.cat(ar_logits), torch.cat(ar_targets), torch.cat(ar_masks), threshold
    )
    return physical, autoregressive


def _merge_support(
    destination: dict[str, dict[str, int]], source: dict[str, dict[str, int]],
) -> None:
    for name, counts in source.items():
        current = destination.setdefault(name, {"total": 0, "up": 0, "down": 0})
        for key in current:
            current[key] += int(counts[key])


def _evaluate(
    model: BarGPTV1,
    batches: list,
    config: ExperimentConfig,
    device: torch.device,
    namespace: str,
) -> dict[str, float]:
    model.eval()
    accumulator = ValidationAccumulator(
        config.data.horizons_us,
        config.model.quantiles,
        namespace=namespace,
        direction_neutral_bps=config.train.direction_neutral_bps,
        include_condition_metrics=False,
        include_ranking_metrics=False,
        include_confidence_metrics=False,
    )
    with torch.inference_mode():
        for batch in batches:
            output, loss = _forward(model, batch, config)
            accumulator.update(output, batch, loss)
    return accumulator.finalize()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    tickers = tuple(item.strip().upper() for item in str(args.tickers).split(",") if item.strip())
    if len(tickers) < 2:
        raise ValueError("pilot overfit requires at least two tickers")
    storage_data = load_shard_storage_config(args.shard_root)
    data = replace(
        storage_data,
        tickers=tickers,
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        validation_start_date=str(args.end_date),
        validation_slices=(),
        batch_size=min(int(args.max_blocks), 2),
        loader_workers=0,
        balance_activity_regimes=False,
    )
    units = discover_offline_units(
        args.shard_root,
        data,
        tickers=tickers,
        start_date=str(args.start_date),
        end_date=str(args.end_date),
    )
    unit_blocks: list[tuple[dict, list[tuple[int, int]]]] = []
    for unit in units:
        shard = load_shard(unit.path)
        refs: list[tuple[int, int]] = []
        for session_index, session in enumerate(shard["sessions"]):
            for block_index in range(len(session["blocks"])):
                refs.append((session_index, block_index))
        if refs:
            unit_blocks.append((shard, refs))
    blocks = []
    round_index = 0
    while len(blocks) < int(args.max_blocks):
        added = False
        for shard, refs in unit_blocks:
            if round_index >= len(refs):
                continue
            session_index, block_index = refs[round_index]
            blocks.append(_limit_block_origins(
                materialize_block(shard, session_index, block_index),
                int(args.origins_per_block),
            ))
            added = True
            if len(blocks) >= int(args.max_blocks):
                break
        if not added:
            break
        round_index += 1
    if len(blocks) < int(args.max_blocks):
        raise RuntimeError(f"pilot exposes only {len(blocks)} blocks; {args.max_blocks} required")
    model_config = BarGPTConfig(
        d_model=int(args.d_model),
        n_layers=int(args.n_layers),
        n_heads=int(args.n_heads),
        n_kv_heads=int(args.n_kv_heads),
        dropout=0.0,
    )
    train_config = replace(
        TrainConfig(),
        learning_rate=float(args.learning_rate),
        weight_decay=0.0,
        wandb_mode="disabled",
    )
    config = ExperimentConfig(model=model_config, data=data, train=train_config)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BarGPTV1(model_config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=train_config.learning_rate, weight_decay=0.0)
    host_batches = [
        collate_compiled_blocks(
            blocks[left : left + data.batch_size],
            horizons_us=data.horizons_us,
            base_timeframe_us=data.base_timeframe_us,
        )
        for left in range(0, len(blocks), data.batch_size)
    ]
    physical_support: dict[str, dict[str, int]] = {}
    ar_support: dict[str, dict[str, int]] = {}
    for batch in host_batches:
        _limit_ar_transitions(batch, int(args.ar_transitions_per_view), train_config.direction_neutral_bps)
        batch_physical, batch_ar = _direction_support(batch, train_config.direction_neutral_bps)
        _merge_support(physical_support, batch_physical)
        _merge_support(ar_support, batch_ar)
    eligible_ar_tasks = sum(
        counts["total"] >= int(args.minimum_direction_examples)
        and min(counts["up"], counts["down"]) >= int(args.minimum_direction_class_examples)
        for counts in ar_support.values()
    )
    print(
        f"Overfit population: blocks={len(blocks)} origins={sum(block.origin_indices.numel() for block in blocks):,} "
        f"AR transitions/task<={int(args.ar_transitions_per_view):,} eligible_AR_tasks={eligible_ar_tasks} "
        f"steps={int(args.steps):,}",
        flush=True,
    )
    print(
        f"Pass gates: loss_improvement>={float(args.minimum_loss_improvement):.0%} "
        f"direction_balanced>={float(args.minimum_direction_balanced_accuracy):.2f} "
        f"direction_MCC>={float(args.minimum_direction_mcc):.2f}",
        flush=True,
    )
    batches = [batch.to(device) for batch in host_batches]
    before = _evaluate(model, batches, config, device, "overfit_before")
    started = time.perf_counter()
    for step in range(1, int(args.steps) + 1):
        model.train()
        batch = batches[(step - 1) % len(batches)]
        optimizer.zero_grad(set_to_none=True)
        output, result = _forward(model, batch, config)
        result.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), train_config.grad_clip_norm)
        optimizer.step()
        if step == 1 or step % max(1, int(args.steps) // 10) == 0:
            physical_balanced, ar_balanced = _quick_direction_scores(
                output, batch, train_config.direction_neutral_bps
            )
            print(
                f"step {step:>5}/{args.steps} loss={float(result.loss.detach()):.6f} "
                f"horizon_direction_loss={float(result.metrics['train/loss_horizon_direction'].detach()):.6f} "
                f"ar_direction_loss={float(result.metrics['train/loss_ar_direction'].detach()):.6f} "
                f"direction_balanced={physical_balanced:.3f} ar_balanced={ar_balanced:.3f}",
                flush=True,
            )
    after = _evaluate(model, batches, config, device, "overfit_after")
    before_loss = float(before["overfit_before_loss/total"])
    after_loss = float(after["overfit_after_loss/total"])
    improvement = 1.0 - after_loss / max(before_loss, 1e-12)
    loss_passed = improvement >= float(args.minimum_loss_improvement)
    direction_passed, direction_records, direction_violations = _score_direction_gate(
        after,
        namespace="overfit_after",
        physical_support=physical_support,
        ar_support=ar_support,
        minimum_examples=int(args.minimum_direction_examples),
        minimum_class_examples=int(args.minimum_direction_class_examples),
        minimum_balanced=float(args.minimum_direction_balanced_accuracy),
        minimum_mcc=float(args.minimum_direction_mcc),
        minimum_ar_views=int(args.minimum_ar_views),
    )
    return_mae: list[dict[str, object]] = []
    for target_name in DIRECTION_TARGET_NAMES:
        metric_name = target_name.removesuffix("_return")
        for horizon_us in data.horizons_us:
            label = f"{horizon_us // 1_000_000}s"
            return_mae.append({
                "task": f"{target_name}/{label}",
                "mae_bps": float(after[f"overfit_after_{metric_name}_return_error/mae_bps_{label}"]),
            })
    passed = loss_passed and direction_passed
    report = {
        "contract": "bar_gpt_v12_sparse_event_overfit_v1",
        "device": str(device),
        "tickers": list(tickers),
        "blocks": len(blocks),
        "origins": sum(block.origin_indices.numel() for block in blocks),
        "origins_per_block": int(args.origins_per_block),
        "ar_transitions_per_view": int(args.ar_transitions_per_view),
        "steps": int(args.steps),
        "elapsed_seconds": time.perf_counter() - started,
        "loss_before": before_loss,
        "loss_after": after_loss,
        "loss_improvement": improvement,
        "minimum_loss_improvement": float(args.minimum_loss_improvement),
        "minimum_direction_balanced_accuracy": float(args.minimum_direction_balanced_accuracy),
        "minimum_direction_mcc": float(args.minimum_direction_mcc),
        "minimum_direction_examples": int(args.minimum_direction_examples),
        "minimum_direction_class_examples": int(args.minimum_direction_class_examples),
        "minimum_ar_views": int(args.minimum_ar_views),
        "loss_gate_passed": loss_passed,
        "direction_gate_passed": direction_passed,
        "return_mae_bps": return_mae,
        "direction_gates": direction_records,
        "violations": direction_violations,
        "status": "passed" if passed else "failed",
        "before": before,
        "after": after,
    }
    destination = args.output or (
        Path(r"D:\TradingML\runtimes\bar_gpt\v1\overfit_pilot")
        / f"overfit-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"overfit {report['status']}: loss {before_loss:.6f} -> {after_loss:.6f} "
        f"({improvement:.1%} improvement); report={destination}",
        flush=True,
    )
    if not passed:
        for violation in report["violations"]:
            print(f"  FAIL {violation}", flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
