from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import torch

from research.bar_gpt.v1.audit_offline_shards import DEFAULT_PILOT_ROOT
from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig
from research.bar_gpt.v1.metrics import ValidationAccumulator
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.offline_shards import (
    collate_compiled_blocks,
    discover_offline_units,
    load_shard,
    materialize_block,
)
from research.bar_gpt.v1.train import _forward


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deliberately overfit a tiny certified v4 pilot panel before the full shard build."
    )
    parser.add_argument("--shard-root", type=Path, default=DEFAULT_PILOT_ROOT)
    parser.add_argument("--tickers", default="AAPL,GOOGL")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2019-02-01")
    parser.add_argument("--max-blocks", type=int, default=2)
    parser.add_argument("--steps", type=int, default=500)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--minimum-loss-improvement", type=float, default=0.25)
    parser.add_argument("--d-model", type=int, default=384)
    parser.add_argument("--n-layers", type=int, default=8)
    parser.add_argument("--n-heads", type=int, default=8)
    parser.add_argument("--n-kv-heads", type=int, default=4)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.max_blocks <= 0 or args.steps <= 0 or args.learning_rate <= 0:
        parser.error("max blocks, steps, and learning rate must be positive")
    return args


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
    data = replace(
        DataConfig(),
        tickers=tickers,
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
    blocks = []
    for unit in units:
        shard = load_shard(unit.path)
        for session_index, session in enumerate(shard["sessions"]):
            for block_index in range(len(session["blocks"])):
                blocks.append(materialize_block(shard, session_index, block_index))
                if len(blocks) >= int(args.max_blocks):
                    break
            if len(blocks) >= int(args.max_blocks):
                break
        if len(blocks) >= int(args.max_blocks):
            break
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
    batches = [batch.to(device) for batch in host_batches]
    before = _evaluate(model, batches, config, device, "overfit_before")
    started = time.perf_counter()
    for step in range(1, int(args.steps) + 1):
        model.train()
        batch = batches[(step - 1) % len(batches)]
        optimizer.zero_grad(set_to_none=True)
        _output, result = _forward(model, batch, config)
        result.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), train_config.grad_clip_norm)
        optimizer.step()
        if step == 1 or step % max(1, int(args.steps) // 10) == 0:
            print(f"step {step:>5}/{args.steps} loss={float(result.loss):.6f}", flush=True)
    after = _evaluate(model, batches, config, device, "overfit_after")
    before_loss = float(before["overfit_before_loss/total"])
    after_loss = float(after["overfit_after_loss/total"])
    improvement = 1.0 - after_loss / max(before_loss, 1e-12)
    passed = improvement >= float(args.minimum_loss_improvement)
    report = {
        "contract": "sparse-event-v4-overfit-1",
        "device": str(device),
        "tickers": list(tickers),
        "blocks": len(blocks),
        "origins": sum(block.origin_indices.numel() for block in blocks),
        "steps": int(args.steps),
        "elapsed_seconds": time.perf_counter() - started,
        "loss_before": before_loss,
        "loss_after": after_loss,
        "loss_improvement": improvement,
        "minimum_loss_improvement": float(args.minimum_loss_improvement),
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
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
