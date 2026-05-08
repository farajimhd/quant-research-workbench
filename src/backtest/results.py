from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import polars as pl

from src.backtest.config import BacktestConfig


def json_default(value: Any):
    if isinstance(value, (datetime, Path)):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    return value


def create_run_dir(config: BacktestConfig) -> Path:
    config.output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    slug = config.run_slug
    run_dir = config.output_root / f"{timestamp}_{slug}"
    suffix = 1
    while run_dir.exists():
        run_dir = config.output_root / f"{timestamp}_{slug}_{suffix:02d}"
        suffix += 1
    run_dir.mkdir(parents=True)
    return run_dir


def base_metadata(config: BacktestConfig, run_dir: Path, status: str = "running") -> dict:
    return {
        "run_id": run_dir.name,
        "run_name": config.run_name,
        "strategy_name": config.strategy_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by_app": config.created_by_app,
        "status": status,
        "run_dir": str(run_dir),
        "config": config.to_dict(),
        "app_version": "phase1-local",
        "metadata_version": 1,
        "uuid": str(uuid4()),
    }


def read_run_metadata(run_dir: Path) -> dict | None:
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_run_metadata(run_dir: Path, metadata: dict) -> None:
    write_json(run_dir / "metadata.json", metadata)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=json_default), encoding="utf-8")


def write_table(path: Path, rows: list[dict]) -> None:
    if rows:
        pl.DataFrame([normalize_row(row) for row in rows], infer_schema_length=None).write_parquet(path)
    else:
        pl.DataFrame().write_parquet(path)


def normalize_row(row: dict) -> dict:
    return {key: normalize_value(value) for key, value in row.items()}


def normalize_value(value):
    if isinstance(value, (datetime, date, Path)):
        return str(value)
    if is_dataclass(value):
        return normalize_row(asdict(value))
    return value


def list_runs(output_root: Path, strategy_name: str | None = None) -> list[Path]:
    if not output_root.exists():
        return []
    runs = []
    for path in output_root.iterdir():
        if not path.is_dir():
            continue
        metadata = read_run_metadata(path)
        if not metadata or not metadata.get("created_by_app"):
            continue
        runs.append(path)
    if strategy_name:
        runs = [path for path in runs if (read_run_metadata(path) or {}).get("strategy_name") == strategy_name]
    return sorted(runs, key=lambda path: path.stat().st_mtime, reverse=True)
