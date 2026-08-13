from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import torch

from research.bar_gpt.v2.model_discovery import (
    discovery_data_config,
    load_discovery_manifest,
    panel_refs,
)
from research.bar_gpt.v2.offline_shards import OfflineBlockRef, load_shard, materialize_block, shard_path
from research.bar_gpt.v2.targets import (
    AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT,
    PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT,
    RETURN_CLASS_COUNT,
    RETURN_CLASS_NAMES,
    RETURN_TARGET_COUNT,
    RETURN_TARGET_NAMES,
    autoregressive_return_class_labels,
    physical_return_class_labels,
)


DEFAULT_SHARD_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v2\return_class_analysis")


def _worker_init() -> None:
    # Each process works on independent shard units; one Torch thread prevents
    # workers from multiplying host thread pools and stalling the workstation.
    torch.set_num_threads(1)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Concurrently audit BarGPT v2 five-class return labels from certified v12 shards.")
    parser.add_argument("--experiment-manifest", required=True, help="Fixed model-comparison panel manifest.")
    parser.add_argument("--shard-root", default=str(DEFAULT_SHARD_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-name", default="percent_thresholds_v2")
    parser.add_argument("--panels", nargs="+", default=("train", "monitor", "validation"))
    parser.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _unit_result(
    shard_root: str,
    unit_key: str,
    population_refs: dict[str, list[dict[str, Any]]],
    horizons_us: tuple[int, ...],
    task_hash: str,
) -> dict[str, Any]:
    shard = load_shard(shard_path(Path(shard_root), unit_key))
    physical: dict[str, list[list[list[int]]]] = {}
    autoregressive: dict[str, dict[str, list[list[int]]]] = {}
    origins: dict[str, int] = {}
    for population, rows in population_refs.items():
        physical_counts = torch.zeros(
            len(horizons_us), RETURN_TARGET_COUNT, RETURN_CLASS_COUNT, dtype=torch.int64
        )
        ar_counts = {
            view: torch.zeros(RETURN_TARGET_COUNT, RETURN_CLASS_COUNT, dtype=torch.int64)
            for view in AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT
        }
        population_origins = 0
        for row in rows:
            ref = OfflineBlockRef(**row)
            block = materialize_block(shard, ref.session_index, ref.block_index)
            population_origins += int(block.origin_indices.numel())
            horizon_target = block.horizon_targets[..., :RETURN_TARGET_COUNT]
            horizon_mask = block.horizon_mask[..., :RETURN_TARGET_COUNT]
            labels = physical_return_class_labels(horizon_target, horizons_us)
            for horizon_index in range(len(horizons_us)):
                for target_index in range(RETURN_TARGET_COUNT):
                    selected = horizon_mask[:, horizon_index, target_index]
                    physical_counts[horizon_index, target_index] += torch.bincount(
                        labels[:, horizon_index, target_index][selected], minlength=RETURN_CLASS_COUNT
                    ).cpu()
            for view, target in block.autoregressive_targets.items():
                values = target[..., :RETURN_TARGET_COUNT]
                mask = block.autoregressive_mask[view][..., :RETURN_TARGET_COUNT]
                view_labels = autoregressive_return_class_labels(values, view)
                for target_index in range(RETURN_TARGET_COUNT):
                    selected = mask[..., target_index]
                    ar_counts[view][target_index] += torch.bincount(
                        view_labels[..., target_index][selected], minlength=RETURN_CLASS_COUNT
                    ).cpu()
        physical[population] = physical_counts.tolist()
        autoregressive[population] = {view: counts.tolist() for view, counts in ar_counts.items()}
        origins[population] = population_origins
    return {
        "unit_key": unit_key,
        "task_hash": task_hash,
        "origins": origins,
        "physical": physical,
        "autoregressive": autoregressive,
    }


def _aggregate(results: list[dict[str, Any]], panels: tuple[str, ...], horizons_us: tuple[int, ...]) -> dict[str, Any]:
    physical = {
        panel: torch.zeros(len(horizons_us), RETURN_TARGET_COUNT, RETURN_CLASS_COUNT, dtype=torch.int64)
        for panel in panels
    }
    autoregressive = {
        panel: {
            view: torch.zeros(RETURN_TARGET_COUNT, RETURN_CLASS_COUNT, dtype=torch.int64)
            for view in AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT
        }
        for panel in panels
    }
    origins = {panel: 0 for panel in panels}
    for result in results:
        for panel in panels:
            origins[panel] += int(result["origins"].get(panel, 0))
            if panel in result["physical"]:
                physical[panel] += torch.as_tensor(result["physical"][panel], dtype=torch.int64)
            for view, counts in result["autoregressive"].get(panel, {}).items():
                autoregressive[panel][view] += torch.as_tensor(counts, dtype=torch.int64)
    return {
        "origins": origins,
        "physical": {panel: counts.tolist() for panel, counts in physical.items()},
        "autoregressive": {
            panel: {view: counts.tolist() for view, counts in views.items()}
            for panel, views in autoregressive.items()
        },
    }


def _rows(summary: dict[str, Any], horizons_us: tuple[int, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for population, raw in summary["physical"].items():
        counts = torch.as_tensor(raw, dtype=torch.int64)
        for horizon_index, horizon_us in enumerate(horizons_us):
            neutral, strong = PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT[horizon_us]
            for target_index, target in enumerate(RETURN_TARGET_NAMES):
                total = int(counts[horizon_index, target_index].sum())
                for class_index, class_name in enumerate(RETURN_CLASS_NAMES):
                    count = int(counts[horizon_index, target_index, class_index])
                    rows.append({
                        "population": population, "pathway": "horizon", "scale": f"{horizon_us // 1_000_000}s",
                        "target": target, "class": class_name, "count": count,
                        "fraction": count / total if total else None,
                        "neutral_percent": neutral, "strong_percent": strong,
                    })
    for population, views in summary["autoregressive"].items():
        for view, raw in views.items():
            counts = torch.as_tensor(raw, dtype=torch.int64)
            neutral, strong = AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT[view]
            for target_index, target in enumerate(RETURN_TARGET_NAMES):
                total = int(counts[target_index].sum())
                for class_index, class_name in enumerate(RETURN_CLASS_NAMES):
                    count = int(counts[target_index, class_index])
                    rows.append({
                        "population": population, "pathway": "autoregressive", "scale": view,
                        "target": target, "class": class_name, "count": count,
                        "fraction": count / total if total else None,
                        "neutral_percent": neutral, "strong_percent": strong,
                    })
    return rows


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    manifest_path = Path(args.experiment_manifest).resolve()
    shard_root = Path(args.shard_root).resolve()
    output = Path(args.output_root).resolve() / str(args.run_name)
    manifest = load_discovery_manifest(
        manifest_path,
        shard_root=shard_root,
        config=discovery_data_config(shard_root),
    )
    panels = tuple(str(name) for name in args.panels)
    refs_by_panel = {name: panel_refs(manifest, name) for name in panels}
    expected_horizons = tuple(PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT)
    horizons_us = tuple(int(value) for value in manifest.get("horizons_us", expected_horizons))
    if horizons_us != expected_horizons:
        raise RuntimeError(
            f"manifest horizons {horizons_us} do not match the v2 class contract {expected_horizons}"
        )
    by_unit: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for panel, refs in refs_by_panel.items():
        for ref in refs:
            by_unit[ref.unit_key][panel].append(ref.__dict__ if hasattr(ref, "__dict__") else {
                field: getattr(ref, field) for field in ref.__dataclass_fields__
            })
    command = {
        "manifest": str(manifest_path), "manifest_hash": manifest.get("manifest_hash"),
        "shard_root": str(shard_root), "panels": panels, "workers": int(args.workers),
        "units": len(by_unit), "thresholds_physical": PHYSICAL_RETURN_CLASS_THRESHOLDS_PERCENT,
        "thresholds_autoregressive": AUTOREGRESSIVE_RETURN_CLASS_THRESHOLDS_PERCENT,
    }
    contract_hash = hashlib.sha256(
        json.dumps(command, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    command["contract_hash"] = contract_hash
    print(json.dumps(command, indent=2), flush=True)
    if not args.execute:
        print("Add --execute to traverse the fixed panels.", flush=True)
        return 0
    output.mkdir(parents=True, exist_ok=True)
    _atomic_json(output / "manifest.json", {**command, "started_at": dt.datetime.now().astimezone().isoformat()})
    result_dir = output / "units"
    result_dir.mkdir(exist_ok=True)
    completed: list[dict[str, Any]] = []
    pending: list[tuple[str, dict[str, list[dict[str, Any]]], str]] = []
    for unit_key, populations in sorted(by_unit.items()):
        serialized = dict(populations)
        task_hash = hashlib.sha256(
            json.dumps(
                {
                    "contract_hash": contract_hash,
                    "unit_key": unit_key,
                    "population_refs": serialized,
                    "horizons_us": horizons_us,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        result_path = result_dir / (hashlib.sha256(unit_key.encode()).hexdigest()[:20] + ".json")
        if result_path.is_file():
            value = json.loads(result_path.read_text(encoding="utf-8"))
            if value.get("unit_key") == unit_key and value.get("task_hash") == task_hash:
                completed.append(value)
                continue
        pending.append((unit_key, serialized, task_hash))
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max(1, int(args.workers)), initializer=_worker_init
    ) as pool:
        futures = {
            pool.submit(
                _unit_result, str(shard_root), unit_key, populations, horizons_us, task_hash
            ): unit_key
            for unit_key, populations, task_hash in pending
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            value = future.result()
            result_path = result_dir / (hashlib.sha256(value["unit_key"].encode()).hexdigest()[:20] + ".json")
            _atomic_json(result_path, value)
            completed.append(value)
            print(f"[{index}/{len(pending)}] {value['unit_key']} complete", flush=True)
    summary = _aggregate(completed, panels, horizons_us)
    rows = _rows(summary, horizons_us)
    _atomic_json(output / "summary.json", {**command, **summary, "completed_at": dt.datetime.now().astimezone().isoformat()})
    with (output / "class_counts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]) if rows else ())
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    print(f"Return-class audit complete: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
