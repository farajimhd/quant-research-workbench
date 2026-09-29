from datetime import date
from hashlib import sha256
import json

import polars as pl
import pytest

from research.rl_trading.v6.build_price_action_geometry import (
    compile_geometry, source_positions)


def test_sparse_geometry_binds_bank_and_uses_only_observed_bars(tmp_path):
    root = tmp_path / 'day'
    root.mkdir()
    (root / 'plan.json').write_text(json.dumps({
        'day': '2026-08-04', 'split_role': 'train'}))
    pl.DataFrame({'ticker': ['A', 'B'], 'episode_uid': ['A:1', 'B:1'],
        'time_us': [4_000_000, 4_000_000], 'exit_hint_us': [8_000_000, 8_000_000],
        'decision_close': [10., 12.], 'direction': [1, -1]}).write_parquet(
            root / 'intended_allocations.parquet')
    checksum = sha256((root / 'intended_allocations.parquet').read_bytes()).hexdigest()
    (root / 'complete.json').write_text(json.dumps({'status': 'complete',
        'outputs': {'intended_allocations': {'rows': 2, 'sha256': checksum}}}))
    positions, _, allocation_hash = source_positions(root, date(2026, 8, 4))
    assert positions.height == 1
    assert allocation_hash == checksum
    bars = pl.DataFrame({'ticker': ['A', 'A', 'A'],
        'boundary_us': [3_000_000, 5_000_000, 8_000_000],
        'high': [9., 11., 15.], 'low': [8., 9., 10.],
        'extremes_valid': [1, 1, 1]})
    result = compile_geometry(positions, bars)
    assert result['swing_low_3s'][0] == 8.
    assert result['held_max_high'][0] == 15.
    assert result['held_min_low'][0] == 9.
    assert result['unobserved_held_seconds'][0] == 2
    assert result['clock_complete'][0] is False
    assert 'oracle_stop' not in result.columns
    assert 'oracle_target' not in result.columns
    (root / 'complete.json').write_text(json.dumps({'status': 'complete',
        'outputs': {'intended_allocations': {'rows': 2, 'sha256': 'bad'}}}))
    with pytest.raises(ValueError, match='differs'):
        source_positions(root, date(2026, 8, 4))


def test_external_legacy_allocation_needs_exact_source_binding(tmp_path):
    root = tmp_path / 'day'
    sidecar = tmp_path / 'alloc'
    root.mkdir()
    sidecar.mkdir()
    day = date(2026, 8, 5)
    (root / 'plan.json').write_text(json.dumps({
        'day': str(day), 'split_role': 'train'}))
    (root / 'complete.json').write_text(json.dumps({'status': 'complete',
        'outputs': {'candidates': {'sha256': 'candidate-hash'}}}))
    pl.DataFrame({'ticker': ['A'], 'episode_uid': ['A:1'],
        'time_us': [4_000_000], 'exit_hint_us': [8_000_000],
        'decision_close': [10.], 'direction': [1]}).write_parquet(
            sidecar / 'intended_allocations.parquet')
    checksum = sha256((sidecar / 'intended_allocations.parquet').read_bytes()).hexdigest()
    source_hash = sha256((root / 'complete.json').read_bytes()).hexdigest()
    certificate = {'version': 'rl-trading-sparse-allocation-v6',
        'status': 'planning_only_not_fills',
        'source_certificate_sha256': source_hash,
        'source_candidate_sha256': 'candidate-hash',
        'intended_allocations_sha256': checksum, 'rows': 1}
    (sidecar / 'complete.json').write_text(json.dumps(certificate))
    positions, _, actual = source_positions(root, day, allocation_root=sidecar)
    assert positions.height == 1 and actual == checksum
    certificate['source_candidate_sha256'] = 'other'
    (sidecar / 'complete.json').write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match='not bound'):
        source_positions(root, day, allocation_root=sidecar)
