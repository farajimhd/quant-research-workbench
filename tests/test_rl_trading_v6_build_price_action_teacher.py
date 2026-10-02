from datetime import date
from hashlib import sha256
import json

import numpy as np
import polars as pl

from research.rl_trading.v6 import build_price_action_teacher as writer
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.build_price_action_brackets import VERSION as BRACKETS_VERSION
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.teacher_data import _load_legacy_teacher_for_historical_audit as load_teacher


def test_teacher_writer_certifies_only_bar_sidecar_and_reloads(tmp_path,
                                                               monkeypatch):
    monkeypatch.setenv('QW_RUNTIME_ROOT', str(tmp_path))
    day = date(2026, 7, 31)
    source, previous, bracket_root, geometry_root, output = (
        tmp_path / name for name in
        ('source', 'previous', 'brackets', 'geometry', 'teacher'))
    for path in (source, previous, bracket_root, geometry_root):
        path.mkdir()
    (source / 'plan.json').write_text(json.dumps({
        'day': str(day), 'split_role': 'train'}))
    allocation = pl.DataFrame({'ticker': ['A'], 'listing_id': ['A'],
        'episode_uid': ['A:1'],
        'time_us': [1_000_000], 'exit_hint_us': [3_000_000],
        'decision_close': [10.], 'exit_hint_close': [11.],
        'desired_budget': [1000.], 'future_reservation': [0.],
        'score': [.1], 'direction': [1]})
    allocation.write_parquet(source / 'intended_allocations.parquet')
    checksum = sha256((source / 'intended_allocations.parquet').read_bytes()).hexdigest()
    (source / 'complete.json').write_text(json.dumps({'status': 'complete',
        'outputs': {'intended_allocations': {'rows': 1, 'sha256': checksum}}}))
    source_hash = sha256((source / 'complete.json').read_bytes()).hexdigest()
    (geometry_root / 'complete.json').write_text(json.dumps({
        'version': writer.GEOMETRY_VERSION,
        'status': 'geometry_only_not_supervision', 'day': str(day),
        'source_certificate_sha256': source_hash,
        'allocation_sha256': checksum, 'execution_evidence': 'none'}))
    geometry_hash = sha256((geometry_root / 'complete.json').read_bytes()).hexdigest()
    brackets = pl.DataFrame({'ticker': ['A'], 'episode_uid': ['A:1'],
        'entry_us': [1_000_000], 'exit_us': [3_000_000],
        'oracle_stop': [9.], 'oracle_target': [11.1],
        'held_last_max_high_us': [3_000_000],
        'label_available': [True]})
    brackets.write_parquet(bracket_root / 'oracle_brackets.parquet')
    bracket_hash = sha256((bracket_root / 'oracle_brackets.parquet').read_bytes()).hexdigest()
    (bracket_root / 'complete.json').write_text(json.dumps({
        'version': BRACKETS_VERSION, 'status': 'labels_only_not_teacher',
        'day': str(day), 'execution_evidence': 'none',
        'geometry_certificate_sha256': geometry_hash,
        'price_grid_source_type': 'canonical_bar_precision',
        'price_unit': .0001,
        'executable_market_tick_claim': False,
        'brackets_sha256': bracket_hash}))
    clocks = np.asarray([1_000_000, 2_000_000, 3_000_000, 4_000_000],
                        dtype=np.int64)
    scalar = np.zeros((4, len(SCALAR_NAMES)), dtype=np.float32)
    scalar[:, SCALAR_NAMES.index('log_close')] = np.log(
        np.asarray([10., 10.2, 11., 11.], dtype=np.float32))
    scalar[:, SCALAR_NAMES.index('bar_price_valid')] = 1.
    bank = SessionBank(source / 'bank', {'offsets': {'A': [0, 4]}},
        clocks, scalar, np.zeros((4, 2, 5, 11), dtype=np.float32))
    session = PackedSession(day, 'train', source, source_hash, bank,
                            None, ('A',))
    monkeypatch.setattr(writer, 'open_session', lambda *args, **kwargs: session)
    assert writer.main(['--source-root', str(source),
        '--previous-root', str(previous), '--brackets-root', str(bracket_root),
        '--geometry-root', str(geometry_root),
        '--date', str(day), '--output', str(output)]) == 0
    saved = json.loads((output / 'complete.json').read_text())
    assert saved['execution_evidence'] == 'none'
    assert saved['completed_positions'] == 1
    decisions, outcomes = load_teacher(output, session, runtime_root=tmp_path)
    assert len(decisions) == saved['decision_rows']
    assert len(outcomes) == saved['outcome_rows']
