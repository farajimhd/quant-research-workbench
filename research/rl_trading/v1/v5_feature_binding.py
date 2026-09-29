"""Fail-closed reuse of certified causal market features for V5 labels.

The old shard's market feature bank is independent of its fixed-lot teacher.
Only that bank may be reused; its actions, account and rewards are excluded.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.dynamic_supervision import VERSION as SUPERVISION_VERSION
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
from research.rl_trading.v1.phase2_close_values import VERSION as PHASE2_VERSION
from research.rl_trading.v1.phase3_dynamic_teacher import VERSION as PHASE3_VERSION
from src.market_engine.level_book_store import read


@dataclass(frozen=True)
class FeatureBinding:
    date: str
    tickers: tuple[str, ...]
    features: Path
    features_hash: str
    orders: Path
    orders_hash: str
    supervision_plan_hash: str


def _certified(root: Path) -> tuple[dict, dict]:
    plan, complete = read(root / 'plan.json'), read(root / 'complete.json')
    if (plan.get('plan_hash') != digest({key: value for key, value in plan.items()
                                          if key != 'plan_hash'}) or
            complete.get('plan_hash') != plan['plan_hash']):
        raise ValueError(f'Invalid artifact certificate: {root}')
    return plan, complete


def bind_existing_features(supervision_root: Path, shard_root: Path,
                           runtime_root: Path) -> FeatureBinding:
    """Bind one V2 order ledger to a V4-era feature bank by exact identities.

    Full hashes are checked before publication or training. A caller can cache
    the resulting binding under a new hash-bound runtime manifest; it must not
    copy the old actions or substitute hindsight Phase 2 columns as features.
    """
    runtime = runtime_root.resolve()
    supervision_root, shard_root = supervision_root.resolve(), shard_root.resolve()
    if not runtime.is_dir() or any(not path.is_relative_to(runtime)
                                    for path in (supervision_root, shard_root)):
        raise ValueError('Certified sources must stay under the runtime root')
    supervision, supervised = _certified(supervision_root)
    shard, shard_done = _certified(shard_root)
    if (supervision.get('version') != SUPERVISION_VERSION or
            supervision.get('scope') != 'full_session' or
            supervision.get('split') not in ('train', 'development', 'test_sealed') or
            shard.get('segment') or shard.get('base_shard_root') or
            tuple(shard.get('feature_names', ())) != FEATURE_NAMES or
            supervision['date'] != shard.get('date')):
        raise ValueError('Supervision and feature shard contracts differ')
    teacher_root = Path(supervision['phase3_root']).resolve()
    if not teacher_root.is_relative_to(runtime):
        raise ValueError('Teacher root outside runtime')
    teacher, teacher_done = _certified(teacher_root)
    new_phase2_root = Path(supervision['phase2_root']).resolve()
    old_phase2_root = Path(shard['phase2_root']).resolve()
    prior_root = Path(teacher['v7_population_source']).resolve()
    if any(not path.is_relative_to(runtime) for path in
           (new_phase2_root, old_phase2_root, prior_root)):
        raise ValueError('Feature authority outside runtime')
    new_phase2, _ = _certified(new_phase2_root)
    old_phase2, _ = _certified(old_phase2_root)
    prior, prior_done = _certified(prior_root)
    included = prior['v7_population']['included']
    selected = new_phase2['selected']
    included_set = set(included)
    expected_tickers = [row['ticker'] for row in selected
                        if row['ticker'] in included_set]
    if (teacher.get('version') != PHASE3_VERSION or
            new_phase2.get('version') != PHASE2_VERSION or
            teacher['phase2_plan_hash'] != new_phase2['plan_hash'] or
            supervision['phase3_plan_hash'] != teacher['plan_hash'] or
            supervision['phase2_plan_hash'] != new_phase2['plan_hash'] or
            supervision['phase3_complete_hash'] != file_hash(teacher_root / 'complete.json') or
            supervision['phase2_complete_hash'] != file_hash(new_phase2_root / 'complete.json') or
            teacher['v7_population_certificate_hash'] != file_hash(prior_root / 'complete.json') or
            prior_done['plan_hash'] != prior['plan_hash'] or
            old_phase2['selected'] != selected or
            list(shard['tickers']) != expected_tickers or
            len(set(shard['tickers'])) != len(shard['tickers']) or
            teacher['date'] != shard['date'] or new_phase2['date'] != shard['date'] or
            shard_done.get('rows') != SECONDS or
            teacher_done.get('trajectory_rows') != SECONDS):
        raise ValueError('Causal feature bank does not match the dynamic teacher population')
    features = shard_root / 'features.npy'
    orders = supervision_root / 'orders.parquet'
    features_hash = shard_done['files'].get('features.npy')
    orders_hash = supervised.get('orders_hash')
    if (not features_hash or not orders_hash or
            file_hash(features) != features_hash or file_hash(orders) != orders_hash):
        raise ValueError('Feature or order bytes changed after certification')
    bank = np.load(features, mmap_mode='r', allow_pickle=False)
    if bank.shape != (len(expected_tickers), SECONDS, len(FEATURE_NAMES)) or bank.dtype != np.float32:
        raise ValueError('Causal feature bank shape or dtype changed')
    del bank
    label_tickers = pl.scan_parquet(orders).select('ticker').unique().collect()['ticker'].to_list()
    missing = set(label_tickers) - set(expected_tickers)
    if missing:
        raise ValueError(f'{len(missing)} teacher listings are absent from causal features')
    return FeatureBinding(shard['date'], tuple(expected_tickers), features,
                          features_hash, orders, orders_hash, supervision['plan_hash'])
