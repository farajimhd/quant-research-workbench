from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import torch

from research.bar_gpt.v2.config import MODEL_SIZE_PRESETS, PRODUCTION_MODEL_TRAINING_PRESETS, BarGPTConfig, ExperimentConfig, TrainConfig
from research.bar_gpt.v2.data import BarGPTBatch
from research.bar_gpt.v2.metrics import ValidationAccumulator, multiclass_scores
from research.bar_gpt.v2.model import BarGPTV2
from research.bar_gpt.v2.offline_shards import CompiledBlock, collate_compiled_blocks, discover_offline_units, load_shard, load_shard_storage_config, materialize_block
from research.bar_gpt.v2.targets import RETURN_CLASS_COUNT, RETURN_TARGET_COUNT, RETURN_TARGET_NAMES, physical_return_class_labels
from research.bar_gpt.v2.train import _forward


DEFAULT_SHARD_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deliberately overfit BarGPT v2 on a tiny certified v12 panel.")
    parser.add_argument("--shard-root", type=Path, default=DEFAULT_SHARD_ROOT)
    parser.add_argument("--model-size", choices=tuple(PRODUCTION_MODEL_TRAINING_PRESETS), default="current")
    parser.add_argument("--tickers", default="AAPL,GOOGL")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2019-02-01")
    parser.add_argument("--max-blocks", type=int, default=2)
    parser.add_argument("--origins-per-block", type=int, default=256)
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--minimum-loss-improvement", type=float, default=0.80)
    parser.add_argument("--minimum-balanced-accuracy", type=float, default=0.90)
    parser.add_argument("--minimum-mcc", type=float, default=0.80)
    parser.add_argument("--minimum-class-support", type=int, default=8)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if min(args.max_blocks, args.origins_per_block, args.steps, args.minimum_class_support) <= 0 or args.learning_rate <= 0:
        parser.error("block, origin, step, support, and learning-rate values must be positive")
    return args


def _evenly_spaced(count: int, maximum: int) -> torch.Tensor:
    if count <= maximum:
        return torch.arange(count)
    return torch.linspace(0, count - 1, steps=maximum).round().long()


def _limit_block(block: CompiledBlock, maximum: int) -> CompiledBlock:
    positions = _evenly_spaced(int(block.origin_indices.numel()), int(maximum))
    origins = block.origin_indices[positions].clone()
    asof = {name: value[positions].clone() for name, value in block.asof_indices.items()}
    lengths = {}
    for name, view in block.views.items():
        indices = origins if name == "1s" else asof[name][asof[name] >= 0]
        lengths[name] = min(int(view.shape[0]), max(1, int(indices.max()) + 1 if indices.numel() else 1))
    return replace(
        block,
        views={name: value[:lengths[name]].clone() for name, value in block.views.items()},
        view_mask={name: value[:lengths[name]].clone() for name, value in block.view_mask.items()},
        view_start_us={name: value[:lengths[name]].clone() for name, value in block.view_start_us.items()},
        view_end_us={name: value[:lengths[name]].clone() for name, value in block.view_end_us.items()},
        view_available_at_us={name: value[:lengths[name]].clone() for name, value in block.view_available_at_us.items()},
        origin_indices=origins,
        origin_timestamps_us=block.origin_timestamps_us[positions].clone(),
        asof_indices=asof,
        autoregressive_targets={name: value[:max(0, lengths[name] - 1)].clone() for name, value in block.autoregressive_targets.items()},
        autoregressive_mask={name: value[:max(0, lengths[name] - 1)].clone() for name, value in block.autoregressive_mask.items()},
        horizon_targets=block.horizon_targets[positions].clone(),
        horizon_mask=block.horizon_mask[positions].clone(),
    )


# Backward-compatible helper names keep shared data-path regression tests
# importable; v2 no longer filters away neutral or minority return classes.
_limit_block_origins = _limit_block


def _limit_ar_transitions(batch: BarGPTBatch, maximum: int, _unused: float = 0.0) -> None:
    for view, mask in batch.autoregressive_mask.items():
        rows = mask.any(dim=-1).nonzero(as_tuple=False)
        if rows.shape[0] <= maximum:
            continue
        selected = _evenly_spaced(int(rows.shape[0]), int(maximum))
        keep_rows = rows[selected]
        keep = torch.zeros_like(mask)
        keep[keep_rows[:, 0], keep_rows[:, 1]] = mask[keep_rows[:, 0], keep_rows[:, 1]]
        batch.autoregressive_mask[view] = keep


def _score_direction_gate(*_args, **_kwargs):
    raise RuntimeError("v2 replaced the binary direction gate with five-class close-return metrics")


def _evaluate(model: BarGPTV2, batches: list[BarGPTBatch], config: ExperimentConfig, namespace: str) -> dict[str, float]:
    model.eval()
    accumulator = ValidationAccumulator(
        config.data.horizons_us, config.model.quantiles, namespace=namespace,
        include_condition_metrics=False, include_ranking_metrics=False, include_confidence_metrics=False,
    )
    with torch.inference_mode():
        for batch in batches:
            output, result = _forward(model, batch, config)
            accumulator.update(output, batch, result)
    return accumulator.finalize()


def _quick_scores(output, batch: BarGPTBatch) -> tuple[float, float]:
    assert output.horizon_return_class_logits is not None
    assert batch.horizon_targets is not None and batch.horizon_mask is not None
    labels = physical_return_class_labels(batch.horizon_targets[..., :RETURN_TARGET_COUNT], batch.horizons_us)
    predictions = output.horizon_return_class_logits.detach().argmax(dim=-1)
    mask = batch.horizon_mask[..., :RETURN_TARGET_COUNT] & batch.origin_mask[:, :, None, None]
    confusion = torch.zeros(RETURN_CLASS_COUNT, RETURN_CLASS_COUNT, dtype=torch.float64, device=labels.device)
    encoded = labels[mask] * RETURN_CLASS_COUNT + predictions[mask]
    confusion += torch.bincount(encoded, minlength=RETURN_CLASS_COUNT**2).reshape(RETURN_CLASS_COUNT, RETURN_CLASS_COUNT)
    _accuracy, balanced, _f1, mcc, _distance = multiclass_scores(confusion.cpu())
    return balanced, mcc


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    tickers = tuple(value.strip().upper() for value in args.tickers.split(",") if value.strip())
    storage = load_shard_storage_config(args.shard_root)
    data = replace(storage, tickers=tickers, start_date=args.start_date, end_date=args.end_date, validation_start_date=args.end_date, validation_slices=(), batch_size=min(args.max_blocks, 2), loader_workers=0, balance_activity_regimes=False)
    units = discover_offline_units(args.shard_root, data, tickers=tickers, start_date=args.start_date, end_date=args.end_date)
    blocks: list[CompiledBlock] = []
    for unit in units:
        shard = load_shard(unit.path)
        for session_index, session in enumerate(shard["sessions"]):
            for block_index in range(len(session["blocks"])):
                blocks.append(_limit_block(materialize_block(shard, session_index, block_index), args.origins_per_block))
                if len(blocks) >= args.max_blocks:
                    break
            if len(blocks) >= args.max_blocks:
                break
        if len(blocks) >= args.max_blocks:
            break
    if len(blocks) < args.max_blocks:
        raise RuntimeError(f"only {len(blocks)} overfit blocks available; {args.max_blocks} required")
    model_config = BarGPTConfig(**MODEL_SIZE_PRESETS[args.model_size], dropout=0.0)
    train_config = replace(TrainConfig(), learning_rate=args.learning_rate, weight_decay=0.0, wandb_mode="disabled")
    config = ExperimentConfig(model=model_config, data=data, train=train_config)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BarGPTV2(model_config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.0)
    host_batches = [collate_compiled_blocks(blocks[left:left + data.batch_size], horizons_us=data.horizons_us, base_timeframe_us=data.base_timeframe_us) for left in range(0, len(blocks), data.batch_size)]
    batches = [batch.to(device) for batch in host_batches]
    before = _evaluate(model, batches, config, "overfit_before")
    started = time.perf_counter()
    for step in range(1, args.steps + 1):
        model.train()
        batch = batches[(step - 1) % len(batches)]
        optimizer.zero_grad(set_to_none=True)
        output, result = _forward(model, batch, config)
        result.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), train_config.grad_clip_norm)
        optimizer.step()
        if step == 1 or step % max(1, args.steps // 10) == 0:
            balanced, mcc = _quick_scores(output, batch)
            print(f"step {step:>5}/{args.steps} loss={float(result.loss.detach()):.6f} class_balanced={balanced:.3f} class_mcc={mcc:.3f}", flush=True)
    after = _evaluate(model, batches, config, "overfit_after")
    before_loss = before["overfit_before_loss/total"]
    after_loss = after["overfit_after_loss/total"]
    improvement = 1.0 - after_loss / max(before_loss, 1e-12)
    balanced = after["overfit_after_close_return_class_summary/balanced_accuracy_macro"]
    mcc = after["overfit_after_close_return_class_summary/mcc_macro"]
    passed = improvement >= args.minimum_loss_improvement and balanced >= args.minimum_balanced_accuracy and mcc >= args.minimum_mcc
    report = {
        "contract": "bar_gpt_v2_five_class_overfit_v2", "model_size": args.model_size,
        "device": str(device), "blocks": len(blocks), "steps": args.steps,
        "before_loss": before_loss, "after_loss": after_loss, "loss_improvement": improvement,
        "close_balanced_accuracy": balanced, "close_mcc": mcc, "passed": passed,
        "elapsed_seconds": time.perf_counter() - started,
    }
    output_path = args.output or Path(r"D:\TradingML\runtimes\bar_gpt\v2\overfit_pilot") / f"{args.model_size}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Overfit {'PASSED' if passed else 'FAILED'}: {output_path}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
