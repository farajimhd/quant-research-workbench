from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
    slug = config.strategy_name.replace(" ", "_").lower()
    run_dir = config.output_root / f"{timestamp}_{slug}"
    suffix = 1
    while run_dir.exists():
        run_dir = config.output_root / f"{timestamp}_{slug}_{suffix:02d}"
        suffix += 1
    run_dir.mkdir(parents=True)
    return run_dir


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
    runs = [path for path in output_root.iterdir() if path.is_dir()]
    if strategy_name:
        slug = strategy_name.replace(" ", "_").lower()
        runs = [path for path in runs if path.name.endswith(slug) or f"_{slug}_" in path.name]
    return sorted(runs, key=lambda path: path.stat().st_mtime, reverse=True)
