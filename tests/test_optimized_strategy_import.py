from datetime import datetime, timezone
from dataclasses import replace

import pytest

from src.backend.optimized_strategy_import import prepare_optimized_release, verify_imported_release
from src.trading_runtime.optimized_candidate import restore_candidate, content_hash
from src.trading_runtime.optimized_program_specification import STAGES


def candidate():
    node = dict(op=0, a=-1, b=-1, feature=0, window=1, value=0., unit='ratio')
    literal = dict(node, op=1, feature=-1, value=1., unit='log_price')
    comparison = dict(node, op=6, a=0, b=1, feature=-1)
    rule = dict(nodes=[node, literal, comparison], output=2)
    policy = dict(stop_fraction=.03, target_fraction=.05, reduce_fraction=.25,
        trail_fraction=.03, add_minimum_profit=.01, reduce_minimum_profit=.02,
        maximum_adds=2, cooldown=30, swing_left=0, swing_right=0)
    program = dict(rules={s: rule for s in STAGES}, policy=policy,
        open_rules=None, minimum_age={})
    return restore_candidate(dict(version='v19', generation=50, serial=43361, candidate=1,
        program=program, execution=dict(entry_dollars=1000., add_dollars=200., cost_bps=0.,
        fee_per_share=.005, minimum_order_fee=1., risk_protection=True,
        maximum_stop_distance=.2, maximum_position_risk=300.),
        universe=dict(version='v6-volume-market-blocks-v1', window=30, top_n=10,
        minimum_close=.8, maximum_close=50.), program_hash=content_hash(program), evidence_hash='a'*64))


def prepared():
    return prepare_optimized_release(candidate(), history_code_hash='b'*64, code_hash='c'*64,
        universe_source_hash='d'*64, strategy_number=100, strategy_id='audited-program',
        release_id='00000000-0000-0000-0000-000000000001',
        published_at=datetime(2026, 10, 15, tzinfo=timezone.utc))


def test_import_uses_shared_qmd_and_normalized_release_without_io():
    result = prepared()
    assert result.required_fields == ('log_open',)
    assert result.release.fields['log_open'].product == 'q_live.qmd_causal_history_value_v1'
    assert result.release.header['entry_cash'] == 1000
    assert result.release.universe.top_count == 10
    verify_imported_release(result.candidate, result.release)


def test_release_with_changed_cash_is_not_frozen_candidate():
    result = prepared()
    release = replace(result.release, header=dict(result.release.header, entry_cash=999))
    with pytest.raises(ValueError, match='differs'):
        verify_imported_release(result.candidate, release)
