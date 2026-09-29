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
