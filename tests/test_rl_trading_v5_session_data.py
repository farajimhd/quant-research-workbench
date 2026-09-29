from pathlib import Path

import polars as pl
import pytest

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.dynamic_supervision import VERSION
from research.rl_trading.v1.v5_feature_binding import FeatureBinding
from research.rl_trading.v1.v5_session_data import load_session
from src.market_engine.level_book_store import write


def _session(tmp_path: Path, split: str):
    supervision, teacher, features = (tmp_path / name for name in
                                      ('supervision', 'teacher', 'features'))
    for root in (supervision, teacher, features):
        root.mkdir(parents=True)
    orders = pl.DataFrame({'time_us': [10], 'ticker': ['ABC']})
    trajectory = pl.DataFrame({'time_us': [10, 11]})
    positions = pl.DataFrame({'ticker': ['ABC']})
    orders.write_parquet(supervision / 'orders.parquet')
    trajectory.write_parquet(teacher / 'trajectory.parquet')
    positions.write_parquet(teacher / 'positions.parquet')
    teacher_plan = {'initial_cash': 10000., 'plan_hash': 'teacher'}
    write(teacher / 'plan.json', teacher_plan)
    write(teacher / 'complete.json', {'plan_hash': 'teacher', 'files': {
        name: file_hash(teacher / name) for name in
        ('trajectory.parquet', 'positions.parquet')}})
    supervision_plan = {'version': VERSION, 'scope': 'full_session',
        'split': split, 'phase3_root': str(teacher),
        'phase3_plan_hash': 'teacher',
        'phase3_complete_hash': file_hash(teacher / 'complete.json')}
    supervision_plan['plan_hash'] = digest(supervision_plan)
    write(supervision / 'plan.json', supervision_plan)
    write(supervision / 'complete.json', {
        'plan_hash': supervision_plan['plan_hash'],
        'orders_hash': file_hash(supervision / 'orders.parquet')})
    binding = FeatureBinding('2026-07-30', ('ABC',), 2, 1, features / 'features.npy',
                             'feature-hash', supervision / 'orders.parquet',
                             file_hash(supervision / 'orders.parquet'),
                             supervision_plan['plan_hash'])
    return supervision, features, binding


def test_v5_session_loader_verifies_rows_and_test_seal(tmp_path, monkeypatch):
    supervision, features, binding = _session(tmp_path, 'train')
    monkeypatch.setattr('research.rl_trading.v1.v5_session_data.bind_existing_features',
                        lambda *_: binding)
    loaded = load_session(supervision, features, tmp_path)
    assert loaded.split == 'train'
    assert loaded.initial_cash == 10000.
    assert loaded.trajectory.height == 2

    sealed, feature_root, binding = _session(tmp_path / 'other', 'test_sealed')
    with pytest.raises(ValueError, match='sealed'):
        load_session(sealed, feature_root, tmp_path)
    assert load_session(sealed, feature_root, tmp_path,
                        allow_sealed_test=True).split == 'test_sealed'
