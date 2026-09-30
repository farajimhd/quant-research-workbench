from datetime import date
from hashlib import sha256
from types import SimpleNamespace
import polars as pl
from research.rl_trading.v6.environment_source import ArteExecutionSource


def test_target_source_pins_full_market_day_unit(monkeypatch):
    def certify(market,reader):
        unit=market.units[0]
        assert (unit.build_id,unit.session_date,unit.ticker,unit.stage,unit.attempt_id)==(
            'build','2026-07-31','A','broker_100ms','attempt')
        return SimpleNamespace(token='certified')
    monkeypatch.setattr('src.backend.backtest_liquidity_price.certify_price_level_plan',certify)
    monkeypatch.setattr('research.rl_trading.v6.bracket_evidence.read_touched_price_levels',
        lambda *args:pl.DataFrame({'price_int':[100000,110000],'execution_volume':[2.,3.]}))
    source=ArteExecutionSource.__new__(ArteExecutionSource)
    source.reader=None
    source.source={'build_id':'build','definition_hash':'definition'}
    source.day=date(2026,7,31)
    source.ledger='unused'
    source.attempts={'A':'attempt'}
    source.price_plans={}
    source.read_hash=sha256()
    source.query_count=source.rows_read=0
    assert source.target_capacity('A',1_000_000,10.5)==3.
    assert source.rows_read==2
