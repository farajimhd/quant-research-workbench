from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from research.bar_gpt.v2 import LEARNING_CONTRACT
from research.bar_gpt.v2.config import (
    OFFLINE_PRODUCTION_LENGTH_BUCKET_BATCHES,
    OFFLINE_PRODUCTION_LOADER_WORKERS,
    OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS,
    OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES,
    DataConfig,
)
from research.bar_gpt.v2.offline_shards import (
    OfflineBlockRef,
    OfflineShardUnit,
    discover_offline_units,
    load_shard,
    hydrate_offline_runtime_config,
    shard_compatibility_hash,
    verify_shard_catalog_lock,
)


DISCOVERY_CONTRACT_VERSION = 9
DISCOVERY_WANDB_PROJECT = "bar gpt model discovery"
DISCOVERY_MANIFEST_NAME = "fixed_panels_v9.json"
DISCOVERY_CAMPAIGN_STATE_NAME = "campaign_state_3class_1bp_v1.json"
DISCOVERY_ORIGIN_BARS_1S = 4_096
DISCOVERY_TRAIN_ORIGINS_PER_EPOCH = 100_000_000
DISCOVERY_EPOCHS = 2
DEFAULT_SHARD_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v2\model_discovery")


@dataclass(frozen=True, slots=True)
class Architecture:
    name: str
    d_model: int
    n_layers: int
    n_heads: int
    n_kv_heads: int
    microbatch: int
    accumulation: int


ARCHITECTURE_GRID: tuple[Architecture, ...] = (
    Architecture("anchor_384x8", 384, 8, 8, 4, 16, 2),
    Architecture("width_512x8", 512, 8, 8, 4, 16, 2),
    Architecture("depth_384x12", 384, 12, 8, 4, 16, 2),
    Architecture("medium_512x12", 512, 12, 8, 4, 8, 4),
    Architecture("depth_512x16", 512, 16, 8, 4, 8, 4),
    Architecture("mid_768x12", 768, 12, 12, 6, 8, 4),
    Architecture("width_1024x12", 1024, 12, 16, 8, 8, 4),
)


def discovery_data_config(shard_root: Path | None = None) -> DataConfig:
    """Return the manifest-authoritative model-ready discovery contract."""
    runtime = replace(DataConfig(), origin_bars_1s=DISCOVERY_ORIGIN_BARS_1S)
    return hydrate_offline_runtime_config(shard_root, runtime) if shard_root is not None else runtime


def discovery_storage_config(config: DataConfig) -> DataConfig:
    """Return the immutable build-time config used to certify discovery shards.

    Training preflight updates ``condition_target_active`` from positive-count
    evidence after the shards and manifest have already been certified.  That
    flag controls loss masking only; it cannot change a stored shard tensor.
    Normalize it to the build-time value so the manifest remains valid across
    that runtime transition.
    """
    return replace(
        config,
        condition_target_active=(True, True, True, True),
    )


def discovery_shard_compatibility_hash(config: DataConfig) -> str:
    """Hash only the immutable shard contract used by discovery manifests."""
    return shard_compatibility_hash(discovery_storage_config(config))


def _hash(seed: int, label: str, *values: object) -> bytes:
    raw = "|".join((str(seed), label, *(str(value) for value in values)))
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _balanced_units(
    units: Sequence[OfflineShardUnit],
    *,
    units_per_ticker: int,
    seed: int,
    label: str,
) -> tuple[OfflineShardUnit, ...]:
    """Select time-spread ticker-months before opening multi-gigabyte shards."""
    by_ticker: dict[str, list[OfflineShardUnit]] = {}
    for unit in units:
        by_ticker.setdefault(unit.unit_key.partition(":")[0], []).append(unit)
    selected: list[OfflineShardUnit] = []
    for ticker in sorted(by_ticker):
        ordered = sorted(by_ticker[ticker], key=lambda unit: unit.unit_key)
        count = min(int(units_per_ticker), len(ordered))
        for slot in range(count):
            left = slot * len(ordered) // count
            right = max(left + 1, (slot + 1) * len(ordered) // count)
            segment = ordered[left:right]
            selected.append(min(segment, key=lambda unit: _hash(seed, label, ticker, unit.unit_key)))
    return tuple(selected)


def enumerate_block_refs(
    units: Sequence[OfflineShardUnit],
    *,
    label: str = "panel",
    cache_path: Path | None = None,
) -> tuple[OfflineBlockRef, ...]:
    units = tuple(units)
    unit_keys_hash = hashlib.sha256(
        "\n".join(unit.unit_key for unit in units).encode("utf-8")
    ).hexdigest()
    cached: dict[str, tuple[OfflineBlockRef, ...]] = {}
    rewrite_cache = False
    if cache_path is not None and cache_path.is_file():
        lines = cache_path.read_text(encoding="utf-8").splitlines()
        try:
            header = json.loads(lines[0])
            if (
                int(header.get("contract_version", -1)) == DISCOVERY_CONTRACT_VERSION
                and header.get("unit_keys_hash") == unit_keys_hash
            ):
                for line in lines[1:]:
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        rewrite_cache = True
                        break
                    key = str(row["unit_key"])
                    if key in cached:
                        raise RuntimeError(f"duplicate cached shard index row: {key}")
                    cached[key] = tuple(OfflineBlockRef(**item) for item in row["refs"])
            else:
                print(f"Discarding incompatible cached {label} shard index", flush=True)
                rewrite_cache = True
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            print(f"Discarding incomplete cached {label} shard index", flush=True)
            cached = {}
            rewrite_cache = True
    if cache_path is not None and (not cache_path.is_file() or rewrite_cache):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        header = json.dumps({
                "contract_version": DISCOVERY_CONTRACT_VERSION,
                "unit_keys_hash": unit_keys_hash,
                "units": len(units),
                "label": label,
            }, sort_keys=True)
        rows = [header]
        rows.extend(
            json.dumps({
                "unit_key": key,
                "refs": [asdict(ref) for ref in values],
            }, separators=(",", ":"))
            for key, values in cached.items()
        )
        temporary = cache_path.with_suffix(cache_path.suffix + f".tmp.{os.getpid()}")
        temporary.write_text("\n".join(rows) + "\n", encoding="utf-8")
        os.replace(temporary, cache_path)
    refs: list[OfflineBlockRef] = []
    started = time.perf_counter()
    for index, unit in enumerate(units, start=1):
        if unit.unit_key in cached:
            unit_refs = cached[unit.unit_key]
            refs.extend(unit_refs)
            print(
                f"Indexing {label} shards {index:,}/{len(units):,}: "
                f"{unit.unit_key} cached | blocks={len(refs):,}",
                flush=True,
            )
            continue
        shard = load_shard(unit.path)
        unit_refs: list[OfflineBlockRef] = []
        for session_index, session in enumerate(shard["sessions"]):
            for block_index, block in enumerate(session["blocks"]):
                unit_refs.append(OfflineBlockRef(
                    unit_key=unit.unit_key,
                    session_index=session_index,
                    block_index=block_index,
                    origins=int(block["origin_indices"].numel()),
                    ticker=str(session["ticker"]),
                    local_date=str(session["local_date"]),
                    activity_regime=int(block["activity_regime"]),
                    session_phase=str(block["session_phase"]),
                    has_condition_target=bool(block["has_condition_target"]),
                    unit_index=int(block["unit_index"]),
                    block_offset=int(block["block_offset"]),
                ))
        refs.extend(unit_refs)
        del shard
        if cache_path is not None:
            with cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "unit_key": unit.unit_key,
                    "refs": [asdict(ref) for ref in unit_refs],
                }, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        elapsed = max(time.perf_counter() - started, 1e-9)
        rate = index / elapsed
        eta = (len(units) - index) / rate if rate else 0.0
        print(
            f"Indexing {label} shards {index:,}/{len(units):,}: {unit.unit_key} | "
            f"blocks={len(refs):,} rate={rate:.2f} shards/s eta={eta / 60:.1f}m",
            flush=True,
        )
    return tuple(refs)


def _balanced_sample(
    refs: Sequence[OfflineBlockRef],
    *,
    target_origins: int,
    seed: int,
    label: str,
    excluded_dates: set[tuple[str, str]] | None = None,
) -> tuple[OfflineBlockRef, ...]:
    """Deterministically interleave ticker-month buckets until the origin budget is met."""
    excluded = excluded_dates or set()
    buckets: dict[tuple[str, str], list[OfflineBlockRef]] = {}
    for ref in refs:
        if (ref.ticker, ref.local_date) in excluded:
            continue
        buckets.setdefault((ref.ticker, ref.local_date[:7]), []).append(ref)
    for key, values in buckets.items():
        values.sort(key=lambda ref: _hash(seed, label, key, ref.local_date, ref.block_offset))
    keys = sorted(buckets, key=lambda key: _hash(seed, label, *key))
    selected: list[OfflineBlockRef] = []
    origins = 0
    cursor = 0
    while origins < target_origins and keys:
        next_keys: list[tuple[str, str]] = []
        for key in keys:
            values = buckets[key]
            if cursor < len(values):
                ref = values[cursor]
                selected.append(ref)
                origins += ref.origins
            if cursor + 1 < len(values):
                next_keys.append(key)
            if origins >= target_origins:
                break
        cursor += 1
        keys = next_keys
    if origins < target_origins:
        raise RuntimeError(f"panel {label} has only {origins:,} eligible origins; {target_origins:,} required")
    return tuple(selected)


def _held_out_panel(
    refs: Sequence[OfflineBlockRef],
    *,
    target_origins: int,
    seed: int,
    label: str,
    used_dates: set[tuple[str, str]],
    reserve_dates_per_ticker: int = 0,
    require_every_ticker: bool = True,
) -> tuple[OfflineBlockRef, ...]:
    """Select a ticker-balanced panel while reserving dates for later panels."""
    if reserve_dates_per_ticker < 0:
        raise ValueError("reserve_dates_per_ticker cannot be negative")
    dates: dict[str, dict[str, list[OfflineBlockRef]]] = {}
    for ref in refs:
        if (ref.ticker, ref.local_date) not in used_dates:
            dates.setdefault(ref.ticker, {}).setdefault(ref.local_date, []).append(ref)
    tickers = sorted(dates)
    if not tickers:
        raise RuntimeError(f"no dates remain for {label}")
    per_ticker = max(1, (target_origins + len(tickers) - 1) // len(tickers))
    selected: list[OfflineBlockRef] = []
    for ticker in tickers:
        ticker_origins = 0
        ordered_dates = sorted(dates[ticker], key=lambda day: _hash(seed, label, ticker, day))
        selectable_count = max(0, len(ordered_dates) - reserve_dates_per_ticker)
        for day in ordered_dates[:selectable_count]:
            day_refs = sorted(
                dates[ticker][day],
                key=lambda ref: _hash(seed, label, ticker, day, ref.block_offset),
            )
            used_dates.add((ticker, day))
            for ref in day_refs:
                selected.append(ref)
                ticker_origins += ref.origins
                if ticker_origins >= per_ticker:
                    break
            if ticker_origins >= per_ticker:
                break
        if ticker_origins == 0 and require_every_ticker:
            raise RuntimeError(f"ticker {ticker} has no unused date for {label}")
    selected.sort(key=lambda ref: _hash(seed, label, ref.ticker, ref.local_date, ref.block_offset))
    origins = sum(ref.origins for ref in selected)
    if origins < target_origins:
        raise RuntimeError(f"panel {label} has only {origins:,} origins; {target_origins:,} required")
    return tuple(selected)


def _panel_summary(refs: Sequence[OfflineBlockRef]) -> dict[str, Any]:
    return {
        "blocks": len(refs),
        "origins": sum(ref.origins for ref in refs),
        "tickers": len({ref.ticker for ref in refs}),
        "ticker_dates": len({(ref.ticker, ref.local_date) for ref in refs}),
        "condition_blocks": sum(ref.has_condition_target for ref in refs),
        "activity_regimes": {
            str(regime): sum(ref.activity_regime == regime for ref in refs) for regime in range(3)
        },
    }


def build_discovery_manifest(
    *,
    shard_root: Path,
    output_path: Path,
    train_origins: int = DISCOVERY_TRAIN_ORIGINS_PER_EPOCH,
    monitor_origins: int = 500_000,
    validation_origins: int = 5_000_000,
    locked_test_origins: int = 5_000_000,
    seed: int = 17,
    training_tickers: Sequence[str] | None = None,
    evaluation_tickers: Sequence[str] | None = None,
) -> dict[str, Any]:
    verify_shard_catalog_lock(shard_root)
    config = discovery_data_config(shard_root)
    catalog_tickers = set(config.tickers)
    resolved_training_tickers = tuple(
        ticker.upper()
        for ticker in (config.training_tickers if training_tickers is None else training_tickers)
    )
    default_evaluation_tickers = tuple(
        sorted({ticker for ticker, _start, _end in config.validation_slices})
    )
    resolved_evaluation_tickers = tuple(
        ticker.upper()
        for ticker in (
            default_evaluation_tickers if evaluation_tickers is None else evaluation_tickers
        )
    )
    for label, tickers in (
        ("training", resolved_training_tickers),
        ("evaluation", resolved_evaluation_tickers),
    ):
        if not tickers:
            raise ValueError(f"{label} tickers cannot be empty")
        if len(set(tickers)) != len(tickers):
            raise ValueError(f"{label} tickers must be unique")
        unknown = sorted(set(tickers) - catalog_tickers)
        if unknown:
            raise ValueError(f"unknown {label} tickers: {unknown}")
    for label, target in (
        ("train_origins", train_origins),
        ("monitor_origins", monitor_origins),
        ("validation_origins", validation_origins),
        ("locked_test_origins", locked_test_origins),
    ):
        if int(target) < 0 or (label != "locked_test_origins" and int(target) == 0):
            requirement = "non-negative" if label == "locked_test_origins" else "positive"
            raise ValueError(f"{label} must be {requirement}")
    training_units = discover_offline_units(
        shard_root,
        config,
        tickers=resolved_training_tickers,
        start_date="2019-01-01",
        end_date="2026-01-01",
    )
    held_out_units = discover_offline_units(
        shard_root,
        config,
        tickers=resolved_evaluation_tickers,
        start_date="2026-01-01",
        end_date="2026-08-01",
    )
    selected_training_units = _balanced_units(
        training_units,
        units_per_ticker=3,
        seed=seed,
        label="train_units",
    )
    selected_held_out_units = _balanced_units(
        held_out_units,
        units_per_ticker=2,
        seed=seed,
        label="held_out_units",
    )
    index_root = output_path.parent / "manifest_index_v2"
    print(
        f"Selected {len(selected_training_units):,}/{len(training_units):,} training shards "
        f"and {len(selected_held_out_units):,}/{len(held_out_units):,} held-out shards "
        "using deterministic time-stratified ticker coverage",
        flush=True,
    )
    training_refs = enumerate_block_refs(
        selected_training_units,
        label="training",
        cache_path=index_root / "training.jsonl",
    )
    held_out_refs = enumerate_block_refs(
        selected_held_out_units,
        label="held-out",
        cache_path=index_root / "held_out.jsonl",
    )
    evaluation_dates = {ticker: set() for ticker in resolved_evaluation_tickers}
    for ref in held_out_refs:
        evaluation_dates[ref.ticker].add(ref.local_date)
    evaluation_date_counts = {
        ticker: len(evaluation_dates[ticker]) for ticker in sorted(evaluation_dates)
    }
    missing_evaluation = sorted(
        ticker for ticker, count in evaluation_date_counts.items() if count == 0
    )
    if missing_evaluation:
        raise RuntimeError(
            "evaluation tickers have no eligible held-out dates: "
            + ", ".join(missing_evaluation)
        )
    train = _balanced_sample(
        training_refs,
        target_origins=train_origins,
        seed=seed,
        label="train",
    )
    used_dates: set[tuple[str, str]] = set()
    monitor = _held_out_panel(
        held_out_refs,
        target_origins=monitor_origins,
        seed=seed,
        label="monitor",
        used_dates=used_dates,
        reserve_dates_per_ticker=1 + int(locked_test_origins > 0),
        require_every_ticker=False,
    )
    validation = _held_out_panel(
        held_out_refs,
        target_origins=validation_origins,
        seed=seed,
        label="validation",
        used_dates=used_dates,
        reserve_dates_per_ticker=int(locked_test_origins > 0),
        require_every_ticker=locked_test_origins == 0,
    )
    locked_test = (
        _held_out_panel(
            held_out_refs,
            target_origins=locked_test_origins,
            seed=seed,
            label="locked_test",
            used_dates=used_dates,
        )
        if locked_test_origins > 0
        else ()
    )
    panels = {
        "train": [asdict(ref) for ref in train],
        "monitor": [asdict(ref) for ref in monitor],
        "validation": [asdict(ref) for ref in validation],
    }
    if locked_test:
        panels["locked_test"] = [asdict(ref) for ref in locked_test]
    value = {
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "seed": seed,
        "shard_root": str(shard_root),
        "shard_config_hash": discovery_shard_compatibility_hash(config),
        "cohorts": {
            "training_tickers": sorted(resolved_training_tickers),
            "evaluation_tickers": sorted(resolved_evaluation_tickers),
            "evaluation_available_ticker_dates": evaluation_date_counts,
        },
        "ranges": {"train": ["2019-01-01", "2026-01-01"], "held_out": ["2026-01-01", "2026-08-01"]},
        "targets": {
            "train_origins_per_epoch": train_origins,
            "monitor_origins": monitor_origins,
            "validation_origins": validation_origins,
            "locked_test_origins": locked_test_origins,
        },
        "summaries": {name: _panel_summary(tuple(OfflineBlockRef(**item) for item in rows)) for name, rows in panels.items()},
        "panels": panels,
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    value["manifest_hash"] = hashlib.sha256(canonical).hexdigest()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, output_path)
    return value


def load_discovery_manifest(path: Path, *, shard_root: Path, config: DataConfig) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if int(value.get("contract_version", -1)) != DISCOVERY_CONTRACT_VERSION:
        raise RuntimeError("unsupported BarGPT discovery manifest contract")
    if Path(value.get("shard_root", "")) != shard_root:
        raise RuntimeError("discovery manifest shard root does not match the requested shard root")
    if value.get("shard_config_hash") != discovery_shard_compatibility_hash(config):
        raise RuntimeError("discovery manifest is incompatible with the configured shard contract")
    stored_hash = str(value.get("manifest_hash", ""))
    unsigned = dict(value)
    unsigned.pop("manifest_hash", None)
    observed = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if stored_hash != observed:
        raise RuntimeError("discovery manifest content hash mismatch")
    return value


def panel_refs(manifest: dict[str, Any], name: str) -> tuple[OfflineBlockRef, ...]:
    try:
        rows = manifest["panels"][name]
    except KeyError as exc:
        raise RuntimeError(f"discovery manifest has no {name!r} panel") from exc
    refs = tuple(OfflineBlockRef(**row) for row in rows)
    if not refs:
        raise RuntimeError(f"discovery manifest panel {name!r} is empty")
    return refs


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the complete BarGPT size/quality discovery campaign sequentially.")
    parser.add_argument("--execute", action="store_true", help="Build/reuse the fixed manifest and run every pending experiment.")
    parser.add_argument("--shard-root", default=str(DEFAULT_SHARD_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--wandb-project", default=DISCOVERY_WANDB_PROJECT)
    parser.add_argument("--wandb-mode", choices=("auto", "online", "offline", "disabled"), default="online")
    parser.add_argument("--workers", type=int, default=OFFLINE_PRODUCTION_LOADER_WORKERS)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--architectures", default="all", help="Comma-separated architecture names or all.")
    return parser.parse_args(list(argv) if argv is not None else None)


def _trainer_command(
    architecture: Architecture,
    *,
    shard_root: Path,
    manifest_path: Path,
    output_root: Path,
    project: str,
    wandb_mode: str,
    workers: int,
    seed: int,
    run_name: str,
    learning_rate: float = 3e-4,
    dropout: float = 0.08,
) -> list[str]:
    return [
        sys.executable, "-B", "-m", "research.bar_gpt.v2.train",
        "--data-source", "offline",
        "--offline-shard-root", str(shard_root),
        "--experiment-manifest", str(manifest_path),
        "--output-root", str(output_root / "runs"),
        "--run-name", run_name,
        "--wandb-project", project,
        "--wandb-mode", wandb_mode,
        "--epochs", str(DISCOVERY_EPOCHS),
        "--origin-bars-1s", str(DISCOVERY_ORIGIN_BARS_1S),
        "--batch-size", str(architecture.microbatch),
        "--gradient-accumulation-steps", str(architecture.accumulation),
        "--loader-workers", str(workers),
        "--ready-queue-blocks", str(OFFLINE_PRODUCTION_READY_QUEUE_BLOCKS),
        "--worker-prefetch-batches", str(OFFLINE_PRODUCTION_WORKER_PREFETCH_BATCHES),
        "--offline-length-bucket-batches", str(OFFLINE_PRODUCTION_LENGTH_BUCKET_BATCHES),
        "--d-model", str(architecture.d_model),
        "--n-layers", str(architecture.n_layers),
        "--n-heads", str(architecture.n_heads),
        "--n-kv-heads", str(architecture.n_kv_heads),
        "--dropout", str(dropout),
        "--learning-rate", str(learning_rate),
        "--weight-decay", "0.1",
        "--warmup-samples", "4000000",
        "--minimum-learning-rate", "0.00003",
        "--scheduler-mode", "single-cosine",
        "--validation-runs-per-epoch", "8",
        "--validation-interval-samples", "25000000",
        "--validation-initial-samples", "25000000",
        "--validation-batches", "0",
        "--checkpoint-validation-evaluations", "1",
        "--seed", str(seed),
    ]


def _atomic_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _final_validation_metrics(run_root: Path) -> dict[str, float]:
    path = run_root / "metrics.jsonl"
    result: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if "validation_loss/total" in row:
            result = {
                key: float(value)
                for key, value in row.items()
                if key.startswith("validation_") and isinstance(value, (int, float))
            }
    if not result:
        raise RuntimeError(f"run has no complete validation metrics: {path}")
    return result


def _ranking_key(metrics: dict[str, float]) -> tuple[float, float, float, float]:
    """Quality-first ranking with three-class return quality as the first tie-breaker."""
    close_class_mcc = metrics.get("validation_close_return_class_summary/mcc_macro", float("-inf"))
    return (
        metrics.get("validation_loss/total", float("inf")),
        -close_class_mcc,
        -metrics.get("validation_ar_close_return_class_summary/mcc_macro", float("-inf")),
        metrics.get("validation_trade_summary/mae_bps_macro", float("inf")),
    )


def _resume_if_available(command: list[str], run_root: Path) -> list[str]:
    checkpoint = run_root / "checkpoints" / "checkpoint_latest.pt"
    if checkpoint.is_file():
        print(f"Resuming interrupted run from {checkpoint}", flush=True)
        return [*command, "--resume-checkpoint", str(checkpoint)]
    return command


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers <= 0:
        raise ValueError("workers must be positive")
    by_name = {item.name: item for item in ARCHITECTURE_GRID}
    names = tuple(by_name) if args.architectures == "all" else tuple(
        item.strip() for item in args.architectures.split(",") if item.strip()
    )
    unknown = sorted(set(names) - set(by_name))
    if unknown:
        raise ValueError(f"unknown architectures: {unknown}")
    output_root = Path(args.output_root)
    manifest_path = output_root / DISCOVERY_MANIFEST_NAME
    print(f"W&B project: {args.wandb_project}", flush=True)
    print("Metric namespaces: monitor_*, validation_*, locked_test_*", flush=True)
    print(
        f"Fixed campaign: {DISCOVERY_TRAIN_ORIGINS_PER_EPOCH // 1_000_000}M train origins x "
        f"{DISCOVERY_EPOCHS} epochs ({DISCOVERY_TRAIN_ORIGINS_PER_EPOCH * DISCOVERY_EPOCHS // 1_000_000}M total); "
        "500K monitor / 25M; 5M validation / epoch; 5M locked test for finalists",
        flush=True,
    )
    if not args.execute:
        print(f"Manifest: {manifest_path}", flush=True)
        for name in names:
            item = by_name[name]
            print(
                f"{item.name}: width={item.d_model} layers={item.n_layers} heads={item.n_heads} "
                f"kv={item.n_kv_heads} micro={item.microbatch} accum={item.accumulation}",
                flush=True,
            )
        print("Add --execute to build the certified panels and run pending models sequentially.", flush=True)
        return 0
    verify_shard_catalog_lock(Path(args.shard_root))
    if not manifest_path.is_file():
        print("Building deterministic certified experiment panels...", flush=True)
        build_discovery_manifest(
            shard_root=Path(args.shard_root),
            output_path=manifest_path,
            seed=int(args.seed),
        )
    else:
        load_discovery_manifest(
            manifest_path,
            shard_root=Path(args.shard_root),
            config=discovery_data_config(Path(args.shard_root)),
        )
        print(f"Reusing verified manifest: {manifest_path}", flush=True)
    state_path = output_root / DISCOVERY_CAMPAIGN_STATE_NAME
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "learning_contract": LEARNING_CONTRACT,
        "campaign_id": time.strftime("%Y%m%d-%H%M%S"),
        "manifest": str(manifest_path),
        "runs": {},
        "profiles": [],
        "locked_test": {},
    }
    if int(state.get("contract_version", -1)) != DISCOVERY_CONTRACT_VERSION:
        raise RuntimeError(f"unsupported discovery campaign state: {state_path}")
    if state.get("learning_contract") != LEARNING_CONTRACT:
        raise RuntimeError(f"discovery campaign uses an incompatible learning contract: {state_path}")
    if Path(str(state.get("manifest", ""))).resolve() != manifest_path.resolve():
        raise RuntimeError("discovery campaign state belongs to a different fixed-panel manifest")
    campaign_id = str(state["campaign_id"])
    runs: dict[str, str] = dict(state.get("runs", {}))
    for index, name in enumerate(names, start=1):
        run_key = f"architecture/{name}"
        if run_key in runs:
            print(f"[{index}/{len(names)}] {name}: already complete", flush=True)
            continue
        architecture = by_name[name]
        run_name = f"discovery-architecture-{architecture.name}-{campaign_id}"
        command = _trainer_command(
            architecture,
            shard_root=Path(args.shard_root),
            manifest_path=manifest_path,
            output_root=output_root,
            project=str(args.wandb_project),
            wandb_mode=str(args.wandb_mode),
            workers=int(args.workers),
            seed=int(args.seed),
            run_name=run_name,
        )
        command = _resume_if_available(command, output_root / "runs" / run_name)
        print(f"[{index}/{len(names)}] starting {name}", flush=True)
        print("Command: " + " ".join(shlex.quote(value) for value in command), flush=True)
        result = subprocess.run(command, cwd=str(Path(__file__).resolve().parents[3]), check=False)
        if result.returncode:
            raise RuntimeError(f"architecture {name} failed with exit code {result.returncode}; rerun the same campaign command to resume")
        runs[run_key] = run_name
        state["runs"] = runs
        _atomic_state(state_path, state)

    architecture_scores = []
    for name in names:
        run_key = f"architecture/{name}"
        metrics = _final_validation_metrics(output_root / "runs" / runs[run_key])
        architecture_scores.append((_ranking_key(metrics), name, metrics))
    architecture_scores.sort(key=lambda item: item[0])
    top_architectures = tuple(name for _score, name, _metrics in architecture_scores[:2])
    state["top_architectures"] = list(top_architectures)
    _atomic_state(state_path, state)
    print("Architecture finalists: " + ", ".join(top_architectures), flush=True)

    quality_variants = tuple(
        (learning_rate, dropout)
        for learning_rate in (1.5e-4, 3e-4)
        for dropout in (0.04, 0.08, 0.12)
    )
    quality_keys: list[str] = []
    quality_total = len(top_architectures) * len(quality_variants)
    quality_index = 0
    for architecture_name in top_architectures:
        architecture = by_name[architecture_name]
        for learning_rate, dropout in quality_variants:
            quality_index += 1
            variant = f"lr{learning_rate:g}-dropout{dropout:g}"
            run_key = f"quality/{architecture_name}/{variant}"
            quality_keys.append(run_key)
            if run_key in runs:
                print(f"[quality {quality_index}/{quality_total}] {run_key}: already complete", flush=True)
                continue
            # The architecture-grid anchor already used this exact recipe; reuse
            # its completed run instead of spending another 200M exposures.
            if learning_rate == 3e-4 and dropout == 0.08:
                runs[run_key] = runs[f"architecture/{architecture_name}"]
                state["runs"] = runs
                _atomic_state(state_path, state)
                print(f"[quality {quality_index}/{quality_total}] {run_key}: reused architecture run", flush=True)
                continue
            run_name = f"discovery-quality-{architecture_name}-{variant}-{campaign_id}"
            command = _trainer_command(
                architecture,
                shard_root=Path(args.shard_root),
                manifest_path=manifest_path,
                output_root=output_root,
                project=str(args.wandb_project),
                wandb_mode=str(args.wandb_mode),
                workers=int(args.workers),
                seed=int(args.seed),
                run_name=run_name,
                learning_rate=learning_rate,
                dropout=dropout,
            )
            command = _resume_if_available(command, output_root / "runs" / run_name)
            print(f"[quality {quality_index}/{quality_total}] starting {run_key}", flush=True)
            result = subprocess.run(command, cwd=str(Path(__file__).resolve().parents[3]), check=False)
            if result.returncode:
                raise RuntimeError(f"quality run {run_key} failed with exit code {result.returncode}; rerun to resume")
            runs[run_key] = run_name
            state["runs"] = runs
            _atomic_state(state_path, state)

    quality_scores = []
    for run_key in quality_keys:
        metrics = _final_validation_metrics(output_root / "runs" / runs[run_key])
        quality_scores.append((_ranking_key(metrics), run_key, metrics))
    quality_scores.sort(key=lambda item: item[0])
    finalists = tuple(run_key for _score, run_key, _metrics in quality_scores[:2])
    state["finalists"] = list(finalists)
    _atomic_state(state_path, state)
    print("Locked-test finalists: " + ", ".join(finalists), flush=True)

    locked_results: dict[str, str] = dict(state.get("locked_test", {}))
    for index, run_key in enumerate(finalists, start=1):
        if run_key in locked_results:
            print(f"[locked test {index}/2] {run_key}: already complete", flush=True)
            continue
        source_run = runs[run_key]
        source_root = output_root / "runs" / source_run
        checkpoint = source_root / "checkpoints" / "checkpoint_latest.pt"
        if not checkpoint.is_file():
            raise RuntimeError(f"finalist checkpoint is missing: {checkpoint}")
        evaluation_name = f"discovery-locked-test-{index}-{campaign_id}"
        command = [
            sys.executable, "-B", "-m", "research.bar_gpt.v2.evaluate_discovery_checkpoint",
            "--checkpoint", str(checkpoint),
            "--experiment-manifest", str(manifest_path),
            "--offline-shard-root", str(args.shard_root),
            "--output-root", str(output_root / "locked_test"),
            "--run-name", evaluation_name,
            "--loader-workers", str(args.workers),
            "--wandb-project", str(args.wandb_project),
            "--wandb-mode", str(args.wandb_mode),
        ]
        print(f"[locked test {index}/2] evaluating {run_key}", flush=True)
        result = subprocess.run(command, cwd=str(Path(__file__).resolve().parents[3]), check=False)
        if result.returncode:
            raise RuntimeError(f"locked-test evaluation failed for {run_key} with exit code {result.returncode}")
        locked_results[run_key] = evaluation_name
        state["locked_test"] = locked_results
        _atomic_state(state_path, state)
    print(f"Discovery campaign complete: {state_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
