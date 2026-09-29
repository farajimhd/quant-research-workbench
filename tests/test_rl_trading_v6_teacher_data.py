from datetime import date
from hashlib import sha256
import json

import numpy as np
import polars as pl

from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.teacher_data import VERSION, load_teacher


def test_certified_sparse_teacher_loader_rejects_old_contract(tmp_path):
    root = tmp_path / 'teacher'
    root.mkdir()
    bank = SessionBank(tmp_path / 'bank',
        {'offsets': {'A': [0, 1]}}, np.asarray([1_000_000], dtype=np.int64),
        np.zeros((1, 37), dtype=np.float32),
        np.zeros((1, 2, 5, 11), dtype=np.float32))
    session = PackedSession(date(2026, 7, 31), 'train', tmp_path / 'bank',
                            'bank-hash', bank, None, ('A',))
    pl.DataFrame({'close_us': [1_000_000], 'order_index': [0],
        'token': [1], 'account_cash': [10000.],
        'account_equity': [10000.], 'account_realized': [0.],
        'account_exposure': [0.], 'seconds_since_action': [0.],
        'pending_reserved_cash': [0.], 'pending_entry_count': [0.],
        'held_listing_indices': [[]], 'held_features_flat': [[]],
        'enter_allowed_indices': [[0]], 'exit_allowed': [[]],
        'stop_allowed': [[]], 'target_allowed': [[]],
        'size_fraction': [.25], 'oracle_log_distance': [None],
    }).write_parquet(root / 'decisions.parquet')
    pl.DataFrame({'source_close_us': [1_000_000],
        'source_order_index': [0], 'bucket_end_us': [1_100_000], 'action': [1],
        'listing_index': [0], 'requested_fraction': [.25],
        'filled_fraction': [.2], 'realized_net_over_equity': [0.],
    }).write_parquet(root / 'outcomes.parquet')
    def digest(path):
        return sha256(path.read_bytes()).hexdigest()
    certificate = {'version': VERSION, 'status': 'audited_price_action_teacher',
        'label_evidence': ['certified_1s_candles', 'sparse_episode_scores'],
        'execution_evidence': 'none',
        'price_grid_source_type': 'canonical_bar_precision',
        'price_unit': .0001, 'executable_market_tick_claim': False,
        'profit_scope': 'hypothetical_close_path_not_executable',
        'day': '2026-07-31', 'bank_certificate_sha256': 'bank-hash',
        'decision_rows': 1, 'outcome_rows': 1,
        'decisions_sha256': digest(root / 'decisions.parquet'),
        'outcomes_sha256': digest(root / 'outcomes.parquet')}
    (root / 'complete.json').write_text(json.dumps(certificate))
    decisions, outcomes = load_teacher(root, session, runtime_root=tmp_path)
    assert len(decisions) == len(outcomes) == 1
    assert decisions[0].enter_allowed.tolist() == [True]
    certificate['execution_evidence'] = 'quotes'
    (root / 'complete.json').write_text(json.dumps(certificate))
    import pytest
    with pytest.raises(ValueError, match='unaudited'):
        load_teacher(root, session, runtime_root=tmp_path)
    certificate['version'] = VERSION
    certificate['execution_evidence'] = 'none'
    original = {name: pl.read_parquet(root / f'{name}.parquet')
                for name in ('decisions', 'outcomes')}
    for name, field in [('decisions', 'quote_spread'),
                        ('outcomes', 'quote_fill_price')]:
        for reset_name, frame in original.items():
            frame.write_parquet(root / f'{reset_name}.parquet')
            certificate[f'{reset_name}_sha256'] = digest(
                root / f'{reset_name}.parquet')
        path = root / f'{name}.parquet'
        frame = original[name].with_columns(pl.lit(0.01).alias(field))
        frame.write_parquet(path)
        certificate[f'{name}_sha256'] = digest(path)
        (root / 'complete.json').write_text(json.dumps(certificate))
        with pytest.raises(ValueError, match='price-action-only'):
            load_teacher(root, session, runtime_root=tmp_path)
    certificate['execution_evidence'] = 'none'
    certificate['version'] = 'old-v5-orders'
    (root / 'complete.json').write_text(json.dumps(certificate))
    with pytest.raises(ValueError, match='unaudited'):
        load_teacher(root, session, runtime_root=tmp_path)
