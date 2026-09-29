"""Certified V5 teacher/session inputs, with the test split sealed by default."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.dynamic_supervision import VERSION as SUPERVISION_VERSION
from research.rl_trading.v1.v5_feature_binding import FeatureBinding, bind_existing_features
from src.market_engine.level_book_store import read


@dataclass(frozen=True)
class V5Session:
    split: str
    binding: FeatureBinding
    supervision_root: Path
    feature_root: Path
    teacher_root: Path
    orders: pl.DataFrame
    trajectory: pl.DataFrame
    positions: pl.DataFrame
    initial_cash: float


def load_session(supervision_root: Path, feature_root: Path, runtime: Path, *,
                 allow_sealed_test: bool = False) -> V5Session:
    """Read labels only after their exact supervision, teacher and feature proof.

    This loader is for training/development. The sealed test must be opened
    explicitly once a checkpoint is selected; its teacher profits are never
    needed for policy inference or checkpoint selection.
    """
    runtime = Path(runtime).resolve()
    supervision_root = Path(supervision_root).resolve()
    feature_root = Path(feature_root).resolve()
    if not runtime.is_dir() or any(not path.is_relative_to(runtime)
                                   for path in (supervision_root, feature_root)):
        raise ValueError('V5 session inputs must be inside the runtime root')
    supervision = read(supervision_root / 'plan.json')
    complete = read(supervision_root / 'complete.json')
    split = supervision.get('split')
    if (supervision.get('version') != SUPERVISION_VERSION or
            supervision.get('scope') != 'full_session' or
            split not in ('train', 'development', 'test_sealed') or
            (split == 'test_sealed' and not allow_sealed_test) or
            supervision.get('plan_hash') != digest({key: value for key, value
                in supervision.items() if key != 'plan_hash'}) or
            complete.get('plan_hash') != supervision['plan_hash'] or
            complete.get('orders_hash') != file_hash(supervision_root / 'orders.parquet')):
        raise ValueError('V5 supervision is uncertified or its split is sealed')
    binding = bind_existing_features(supervision_root, feature_root, runtime)
    teacher_root = Path(supervision['phase3_root']).resolve()
    if not teacher_root.is_relative_to(runtime):
        raise ValueError('Teacher root is outside the runtime root')
    teacher = read(teacher_root / 'plan.json')
    teacher_complete = read(teacher_root / 'complete.json')
    if (teacher.get('plan_hash') != supervision['phase3_plan_hash'] or
            teacher_complete.get('plan_hash') != teacher['plan_hash'] or
            any(file_hash(teacher_root / name) != teacher_complete['files'].get(name)
                for name in ('trajectory.parquet', 'positions.parquet')) or
            file_hash(teacher_root / 'complete.json') != supervision['phase3_complete_hash']):
        raise ValueError('Teacher trajectories or positions changed after certification')
    orders = pl.read_parquet(binding.orders)
    trajectory = pl.read_parquet(teacher_root / 'trajectory.parquet')
    positions = pl.read_parquet(teacher_root / 'positions.parquet')
    if (trajectory.height != binding.decision_seconds or
            orders.filter(~pl.col('ticker').is_in(binding.tickers)).height or
            positions.filter(~pl.col('ticker').is_in(binding.tickers)).height or
            not trajectory['time_us'].is_sorted() or
            not orders['time_us'].is_sorted()):
        raise ValueError('V5 teacher rows do not align with the causal feature bank')
    return V5Session(split, binding, supervision_root, feature_root, teacher_root,
                     orders, trajectory, positions, float(teacher['initial_cash']))
