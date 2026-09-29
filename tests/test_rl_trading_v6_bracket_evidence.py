"""Sparse certified price-level projection for target touches."""
from datetime import date

import polars as pl

from research.rl_trading.v1 import arte_sql
from research.rl_trading.v1.common import bounds
from research.rl_trading.v6 import bracket_evidence
from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit


def test_only_unambiguous_target_bucket_is_read(monkeypatch):
    day = date(2026, 8, 3)
    first_close = bounds(day)[0] + 100_000
    source_id = '11111111-1111-1111-1111-111111111111'
    derived_id = '22222222-2222-2222-2222-222222222222'
    source = {'build_id': 'build', 'units': {str(day): {'TEST': {}}}}
    plan = PriceLevelPlan('build', (PriceLevelUnit(str(day), 'TEST',
        source_id, derived_id, 1, 1, 100., '0'*64),), 'token')
    touched = pl.DataFrame({
        'ticker': ['TEST', 'TEST'],
        'boundary_us': [first_close, first_close + 100_000],
        'target_touched': [True, True], 'stop_touched': [False, True],
    })
    monkeypatch.setattr(bracket_evidence, 'broker_attempts',
                        lambda *args: {'TEST': source_id})
    statements = []

    def fake_frame(reader, statement, schema):
        arte_sql._approved(statement)
        statements.append(statement)
        return pl.DataFrame({'ticker': ['TEST'], 'boundary_us': [first_close],
                             'price_int': [10000], 'execution_volume': [100.]})

    monkeypatch.setattr(bracket_evidence, 'frame', fake_frame)
    result = bracket_evidence.read_touched_price_levels(
        object(), source, 'ledger', plan, day, touched)
    assert result.height == 1
    assert len(statements) == 1
    assert 'AND (ticker,source_attempt_id,derivation_attempt_id,bucket_index)' in statements[0]
    assert ',144000)' in statements[0]
    assert ',144001)' not in statements[0]


def test_held_extrema_read_only_exact_post_fill_buckets(monkeypatch):
    day = date(2026, 8, 3)
    origin = bounds(day)[0]
    attempt = '11111111-1111-1111-1111-111111111111'
    source = {'build_id': 'build', 'units': {str(day): {
        'TEST': {'bars': {'attempt_id': attempt}}}}}
    positions = pl.DataFrame({'ticker': ['TEST'],
        'entry_us': [origin + 1_100_000],
        'exit_us': [origin + 1_300_000]})
    statements = []

    def fake_frame(reader, statement, schema):
        arte_sql._approved(statement)
        statements.append(statement)
        return pl.DataFrame({'ticker': ['TEST', 'TEST'],
            'boundary_us': [origin + 1_200_000, origin + 1_300_000],
            'high': [1.25, 1.3], 'low': [1.1, 1.2],
            'extremes_valid': [1, 1]})

    monkeypatch.setattr(bracket_evidence, 'frame', fake_frame)
    result = bracket_evidence.read_held_bucket_extrema(
        object(), source, day, positions)
    assert result.height == 2
    assert len(statements) == 1
    assert 'bucket_index BETWEEN 144011 AND 144012' in statements[0]
    assert 'resolution_ms=100' in statements[0]


def test_oracle_extrema_scope_is_three_second_prefill_and_held_path(monkeypatch):
    day = date(2026, 8, 3)
    origin = bounds(day)[0]
    source = {'build_id': 'build', 'units': {str(day): {'TEST': {
        'bars': {'attempt_id': '11111111-1111-1111-1111-111111111111'}}}}}
    positions = pl.DataFrame({'ticker': ['TEST'],
        'entry_us': [origin + 4_100_000],
        'exit_us': [origin + 8_100_000]})
    statements = []

    def fake_frame(reader, statement, schema):
        arte_sql._approved(statement)
        statements.append(statement)
        return pl.DataFrame(schema=bracket_evidence.BUCKET_SCHEMA)

    monkeypatch.setattr(bracket_evidence, 'frame', fake_frame)
    result = bracket_evidence.read_oracle_one_second_extrema(
        object(), source, day, positions)
    assert result.is_empty()
    assert len(statements) == 1
    assert 'resolution_ms=1000' in statements[0]
    assert 'bucket_index BETWEEN 14401 AND 14407' in statements[0]
