from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CACHE_ROOT = Path("D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02")
DEFAULT_OUTPUT_ROOT = Path("D:/TradingML/runtimes/temporal_event_model/v3/train")
DEFAULT_MONTH = "2019-02"

DEFAULT_ARGS: dict[str, str | int | float | bool] = {
    "cache-root": str(DEFAULT_CACHE_ROOT),
    "output-root": str(DEFAULT_OUTPUT_ROOT),
    "dataset-id": "temporal_v3_201902_large_bs512_full_v1",
    "split": "train",
    "val-split": "validation",
    "months": DEFAULT_MONTH,
    "batch-size": 512,
    "epochs": 1,
    "read-workers": 4,
    "materialize-workers": 4,
    "loaded-parts-per-group": 2,
    "materialize-chunk-size": 128,
    "prefetch-batches": 1,
    "chronological-replay": True,
    "time-window-seconds": 1.0,
    "scanner-index-cache-entries": 4,
    "prefetch-scanner-indexes": True,
    "scanner-prefetch-workers": 2,
    "d-model": 512,
    "event-layers": 12,
    "event-heads": 16,
    "fusion-layers": 8,
    "fusion-heads": 16,
    "side-encoder-dim": 192,
    "learning-rate": 2e-4,
    "weight-decay": 0.01,
    "grad-clip-norm": 1.0,
    "amp": True,
    "amp-dtype": "bf16",
    "fast-summary-samples": 50_000,
    "train-metric-window-samples": 500_000,
    "validation-samples": 2_000_000,
    "validation-batches": 8,
    "checkpoint-latest-samples": 1_000_000,
    "checkpoint-archive-samples": 5_000_000,
    "progress-layout": "rich",
    "wandb-project": "temporal-event-model-v3",
    "wandb-mode": "auto",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch full February 2019 temporal v3 large bs512 training.")
    parser.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--month", default=DEFAULT_MONTH)
    parser.add_argument("--run-name", default="v3-large-bs512-feb2019-full")
    parser.add_argument("--validation-reserve-days", type=int, default=1)
    parser.add_argument("--validation-days", default="", help="Comma-separated validation days. Empty reserves the last N cache days.")
    parser.add_argument("--disable-validation", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved command without executing it.")
    parser.add_argument("overrides", nargs=argparse.REMAINDER, help="Arguments passed through to train.py after --.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_root = Path(args.cache_root)
    month = str(args.month)
    plan = discover_training_plan(
        cache_root=cache_root,
        month=month,
        validation_days=split_csv(args.validation_days),
        validation_reserve_days=0 if bool(args.disable_validation) else int(args.validation_reserve_days),
    )
    command = build_command(args=args, plan=plan)
    print_plan(plan, batch_size=int(DEFAULT_ARGS["batch-size"]), command=command)
    if bool(args.dry_run):
        return 0
    return int(subprocess.call(command))


def discover_training_plan(*, cache_root: Path, month: str, validation_days: tuple[str, ...], validation_reserve_days: int) -> dict[str, Any]:
    month_dir = cache_root / f"month={month}"
    if not month_dir.exists():
        raise FileNotFoundError(f"Missing month cache directory: {month_dir}")
    day_counts: dict[str, int] = {}
    package_count = 0
    for manifest_path in sorted(month_dir.glob("ticker=*/manifest.json")):
        package_count += 1
        manifest = read_json(manifest_path)
        for part in manifest.get("parts") or ():
            if not isinstance(part, Mapping):
                continue
            source_date = source_date_from_part(part)
            origin_rows = int(part.get("origin_rows") or 0)
            if source_date and origin_rows > 0:
                day_counts[source_date] = int(day_counts.get(source_date, 0)) + origin_rows
    if not day_counts:
        raise RuntimeError(f"No origin rows found under {month_dir}")
    days = tuple(sorted(day_counts))
    if validation_days:
        val_days = tuple(day for day in validation_days if day in day_counts)
        missing = tuple(day for day in validation_days if day not in day_counts)
        if missing:
            raise RuntimeError(f"Requested validation days not found in cache: {', '.join(missing)}")
    else:
        reserve = max(0, min(int(validation_reserve_days), len(days) - 1 if len(days) > 1 else 0))
        val_days = days[-reserve:] if reserve else ()
    train_days = tuple(day for day in days if day not in set(val_days))
    if not train_days:
        raise RuntimeError("Validation reservation consumed all training days.")
    train_samples = sum(int(day_counts[day]) for day in train_days)
    val_samples = sum(int(day_counts[day]) for day in val_days)
    return {
        "cache_root": str(cache_root),
        "month": month,
        "package_count": package_count,
        "day_counts": day_counts,
        "training_days": train_days,
        "validation_days": val_days,
        "train_samples": int(train_samples),
        "validation_samples_available": int(val_samples),
    }


def build_command(*, args: argparse.Namespace, plan: Mapping[str, Any]) -> list[str]:
    script = Path(__file__).with_name("train.py")
    command = [sys.executable, "-u", str(script)]
    resolved = dict(DEFAULT_ARGS)
    resolved["cache-root"] = str(args.cache_root)
    resolved["output-root"] = str(args.output_root)
    resolved["months"] = str(args.month)
    resolved["run-name"] = str(args.run_name)
    resolved["training-days"] = ",".join(plan["training_days"])
    resolved["validation-days"] = ",".join(plan["validation_days"])
    resolved["max-samples"] = int(plan["train_samples"])
    resolved["max-origins-per-epoch"] = int(plan["train_samples"])
    for key, value in resolved.items():
        flag = f"--{key}"
        if isinstance(value, bool):
            if value:
                command.append(flag)
            continue
        command.extend([flag, str(value)])
    if bool(args.disable_validation):
        command.append("--disable-validation")
    overrides = list(args.overrides)
    if overrides and overrides[0] == "--":
        overrides = overrides[1:]
    command.extend(overrides)
    return command


def print_plan(plan: Mapping[str, Any], *, batch_size: int, command: list[str]) -> None:
    train_samples = int(plan["train_samples"])
    updates = int(math.ceil(train_samples / max(1, int(batch_size))))
    print("=" * 100, flush=True)
    print("Temporal v3 large bs512 February 2019 training plan", flush=True)
    print(f"cache_root={plan['cache_root']}", flush=True)
    print(f"month={plan['month']} ticker_packages={int(plan['package_count']):,}", flush=True)
    print(f"training_days={len(plan['training_days'])} validation_days={len(plan['validation_days'])}", flush=True)
    print(f"train_samples={train_samples:,} validation_samples_available={int(plan['validation_samples_available']):,}", flush=True)
    print(f"estimated_optimizer_updates={updates:,} batch_size={batch_size:,}", flush=True)
    print(f"train_day_range={plan['training_days'][0]} -> {plan['training_days'][-1]}", flush=True)
    if plan["validation_days"]:
        print(f"validation_days={','.join(plan['validation_days'])}", flush=True)
    print("COMMAND", flush=True)
    print(" ".join(shlex.quote(part) for part in command), flush=True)
    print("=" * 100, flush=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_date_from_part(part: Mapping[str, Any]) -> str:
    raw = str(part.get("source_date") or "")
    if raw:
        return raw[:10]
    job_id = str(part.get("job_id") or "")
    for token in job_id.split("|"):
        if len(token) >= 10 and token[4:5] == "-" and token[7:8] == "-":
            return token[:10]
    return ""


def split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip()[:10] for part in str(value or "").split(",") if part.strip())


if __name__ == "__main__":
    raise SystemExit(main())
