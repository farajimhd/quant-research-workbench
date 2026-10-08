"""Reader contract with explicitly controlled market verification and Arrow transport."""
import polars as pl
import pytest
from src.backend.backtest_market_data import CertifiedMarketDayPlan,ExecutionInterval,MarketDayUnit
from research.causal_strategy_features.v3 import source as reader
from tests.test_causal_native_bar_channels import source


ATTEMPT = '11111111-1111-4111-8111-111111111111'


def plan():
    unit=MarketDayUnit('build','2026-08-04','X','bars',ATTEMPT,'a'*64,10,'b'*64)
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(100),'build','c'*64,
        ('2026-08-04',),('X',),(unit,),(60000,),'d'*64)


class Transport:
    def __init__(self, frame): self.frame,self.queries=frame,[]
    def iter_arrow_record_batches(self,sql):
        self.queries.append(sql)
        yield from self.frame.select(reader.COLUMNS).to_arrow().cast(reader.SOURCE_SCHEMA).to_batches()


def args():
    return dict(session_date='2026-08-04',tickers=('X',),resolutions_ms=(60000,),
                through_day_boundary_ms=600000,lookback_bars=3)


def frame(): return source().with_columns(pl.lit(ATTEMPT).alias('attempt_id'))


def test_pinned_bounded_arrow_read_and_real_vector_projection(monkeypatch):
    verified=[]
    monkeypatch.setattr(reader,'verify_market_day_plan',lambda market,client:verified.append(market))
    market=plan();client=Transport(frame());packets=list(reader.certified_channel_packets(market,client,**args()))
    assert verified==[market] and len(packets)==1 and packets[0].height==10
    sql=client.queries[0]
    assert 'FROM arte.bars_v1' in sql and ATTEMPT in sql and "build_id='build'" in sql
    assert '*resolution_ms<=600000' in sql and 'FORMAT ArrowStream' in sql
    assert 'LIMIT 11' in sql and 'SETTINGS' not in sql
    assert 'extremes_valid FROM (SELECT build_id,session_date,ticker,attempt_id' in sql
    assert 'LIMIT 11) FORMAT ArrowStream' in sql
    assert packets[0].filter(pl.col('bucket_index')==5)['relative_execution_volume'][0]==1.


@pytest.mark.parametrize('change', ['future','foreign'])
def test_transport_cannot_return_future_or_foreign_source(monkeypatch,change):
    monkeypatch.setattr(reader,'verify_market_day_plan',lambda *a:None)
    bad=frame().with_columns(pl.lit(20).alias('bucket_index') if change=='future'
                             else pl.lit('foreign').alias('build_id'))
    with pytest.raises(ValueError,match='foreign identity or future'):
        list(reader.certified_channel_packets(plan(),Transport(bad),**args()))


def test_undeclared_resolution_fails_before_verification_or_read(monkeypatch):
    monkeypatch.setattr(reader,'verify_market_day_plan',lambda *a:pytest.fail('Premature verification'))
    values={**args(),'resolutions_ms':(300000,)}
    with pytest.raises(ValueError,match='explicitly certified'):
        list(reader.certified_channel_packets(plan(),Transport(frame()),**values))


def test_extra_sentinel_row_rejects_overflow_instead_of_truncating(monkeypatch):
    monkeypatch.setattr(reader,'verify_market_day_plan',lambda *a:None)
    too_many=pl.concat([frame(),frame().head(1)])
    with pytest.raises(ValueError,match='row bound'):
        list(reader.certified_channel_packets(plan(),Transport(too_many),**args()))
