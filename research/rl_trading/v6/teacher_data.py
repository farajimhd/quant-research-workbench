"""Certified sparse V6 bracket teacher adapter for chronological training.

The teacher writer must derive every pre-action snapshot from the causal OMS
ledger. Future oracle values are loaded only as conditional loss targets.
Old V5 trajectory and fixed-lot labels do not satisfy this versioned schema.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import polars as pl

from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.training import (ExecutionOutcome,
                                             TeacherDecision, _validate)


VERSION = 'rl-trading-confirmed-bracket-teacher-v6'
DECISION_COLUMNS = {
    'close_us', 'order_index', 'token', 'account_cash', 'account_equity',
    'account_realized', 'account_exposure', 'seconds_since_action',
    'held_listing_indices', 'held_features_flat',
    'enter_allowed_indices', 'exit_allowed', 'stop_allowed',
    'target_allowed', 'size_fraction', 'oracle_log_distance',
}
OUTCOME_COLUMNS = {
    'source_close_us', 'source_order_index', 'bucket_end_us', 'action',
    'listing_index', 'requested_fraction',
    'filled_fraction', 'realized_net_over_equity',
}


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_teacher(root: Path, session: PackedSession, *,
                 runtime_root: Path) -> tuple[tuple[TeacherDecision, ...],
                                              tuple[ExecutionOutcome, ...]]:
    """Bind sparse actions and later outcomes to one certified train bank."""
    root, runtime = Path(root).resolve(), Path(runtime_root).resolve()
    if (session.role != 'train' or not runtime.is_dir() or
            not root.is_relative_to(runtime) or root == session.root):
        raise ValueError('V6 training teacher needs a distinct runtime root')
    certificate = json.loads((root / 'complete.json').read_text())
    if (certificate.get('status') != 'audited_quote_bracket_teacher' or
            certificate.get('version') != VERSION or
            certificate.get('day') != str(session.day) or
            certificate.get('bank_certificate_sha256') !=
            session.source_certificate_sha256):
        raise ValueError('Teacher is unaudited or bound to another bank')
    decision_path = root / 'decisions.parquet'
    outcome_path = root / 'outcomes.parquet'
    if (_hash(decision_path) != certificate.get('decisions_sha256') or
            _hash(outcome_path) != certificate.get('outcomes_sha256')):
        raise ValueError('Teacher action or outcome hash differs from audit')
    rows = pl.read_parquet(decision_path)
    fills = pl.read_parquet(outcome_path)
    if (not DECISION_COLUMNS <= set(rows.columns) or
            not OUTCOME_COLUMNS <= set(fills.columns)):
        raise ValueError('Certified teacher lacks causal bracket fields')
    listings = len(session.listings)
    decisions = []
    for row in rows.iter_rows(named=True):
        held = np.asarray(row['held_listing_indices'], dtype=np.int64)
        features = np.asarray(row['held_features_flat'], dtype=np.float32)
        if features.size != held.size * 4:
            raise ValueError('Teacher holding snapshot shape changed')
        enter = np.zeros(listings, dtype=np.bool_)
        candidates = np.asarray(row['enter_allowed_indices'], dtype=np.int64)
        if (np.any(candidates < 0) or np.any(candidates >= listings) or
                len(np.unique(candidates)) != len(candidates)):
            raise ValueError('Teacher causal enter mask has invalid identity')
        enter[candidates] = True
        decisions.append(TeacherDecision(
            int(row['close_us']), int(row['order_index']), int(row['token']),
            np.asarray([row['account_cash'], row['account_equity'],
                        row['account_realized'], row['account_exposure'],
                        row['seconds_since_action']], dtype=np.float32),
            held, features.reshape(-1, 4), enter,
            np.asarray(row['exit_allowed'], dtype=np.bool_),
            np.asarray(row['stop_allowed'], dtype=np.bool_),
            np.asarray(row['target_allowed'], dtype=np.bool_),
            row['size_fraction'], row['oracle_log_distance']))
    outcomes = tuple(ExecutionOutcome(
        int(row['source_close_us']), int(row['source_order_index']),
        int(row['bucket_end_us']), int(row['action']),
        int(row['listing_index']), float(row['requested_fraction']),
        float(row['filled_fraction']), float(row['realized_net_over_equity']))
        for row in fills.iter_rows(named=True))
    _validate(tuple(decisions), outcomes, listings)
    if (len(decisions) != certificate.get('decision_rows') or
            len(outcomes) != certificate.get('outcome_rows')):
        raise ValueError('Teacher sparse row counts differ from audit')
    return tuple(decisions), outcomes
