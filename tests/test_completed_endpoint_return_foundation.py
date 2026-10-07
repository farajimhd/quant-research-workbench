"""Certified source → typed producer packet → native consumer → causal ranking."""
from dataclasses import replace
from datetime import date
import re

import numpy as np
import pyarrow as pa
import pytest

from pipelines.market_sip.events import completed_endpoint_return_producer as producer
from src.backend import backtest_completed_endpoint_returns as native
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.market_engine.completed_endpoint_return_contract import (
    COVERAGE_TABLE, FEATURE_TABLE, CompletedEndpointReturnPolicy,
    ReturnSourceRequest, ddl, issue_source_plan, CompletedReturnProjection,
    projection_token, table_hash, canonical_source_hash,
)
from src.trading_runtime.completed_endpoint_return_ranking import (
    CompletedReturnRankingPolicy, ReturnQualification, rank_completed_candidates,
)

ATTEMPT = '11111111-1111-4111-8111-111111111111'
FEATURE_ATTEMPT = '22222222-2222-4222-8222-222222222222'


def plan(tickers=('AAA', 'BBB', 'CCC')):
    units = tuple(MarketDayUnit('a'*64, '2026-08-04', t, 'bars', ATTEMPT, 'b'*64, 1920, 'c'*64)
                  for t in tickers)
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(100), 'a'*64, 'd'*64,
        ('2026-08-04',), tickers, units, (100, 30000), 'e'*64)


def bar(ticker, minute, close, *, half=1, valid=1, extremes=1):
    return dict(ticker=ticker, bucket_index=480 + 2*minute + half, close_int=close,
        high_int=close+10, low_int=max(0, close-10), price_valid=valid, extremes_valid=extremes)


class Client:
    def __init__(self, bars=(), packet=None):
        self.bars, self.packet, self.sql = bars, packet, []

    def iter_arrow_record_batches(self, sql):
        self.sql.append(sql)
        assert sql.startswith('SELECT ') and 'max_threads=1' in sql
        assert 'max_execution_time=45' in sql and 'max_memory_usage=2147483648' in sql
        assert "session_date=toDate('2026-08-04')" in sql
        if 'FROM arte.bars_v1 ' in sql:
            assert f"toUUID('{ATTEMPT}')" in sql
            keys = set((t, int(b)) for t, b in re.findall(r"\('([^']+)',(\d+)\)", sql))
            table = pa.Table.from_pylist([b for b in self.bars if (b['ticker'], b['bucket_index']) in keys],
                                        schema=producer.SOURCE_SCHEMA)
        elif f'FROM {COVERAGE_TABLE} ' in sql:
            table = self.packet.coverage
        elif f'FROM {FEATURE_TABLE} ' in sql:
            table = self.packet.rows
        else:
            raise AssertionError(sql)
        return iter(table.to_batches(max_chunksize=3))


@pytest.fixture(autouse=True)
def certified(monkeypatch):
    # No market reads in this test. Actual source verification is the official
    # production boundary, independently tested by the existing authority suite.
    calls = []
    def verify(m, client):
        calls.append(m.token)
    monkeypatch.setattr(producer, 'verify_market_day_plan', verify)
    monkeypatch.setattr(native, 'verify_market_day_plan', verify)
    return calls


def test_end_to_end_normalized_source_consumption_and_rank(certified):
    bars = [bar('AAA', 4, 100000), bar('AAA', 9, 110000),
            bar('BBB', 4, 100000), bar('BBB', 9, 90000)]
    keys = tuple((t, 600000) for t in plan().tickers)
    source = Client(bars)
    packet = producer.produce_completed_returns(plan(), keys, FEATURE_ATTEMPT, source)
    assert len(source.sql) == 1 and len(certified) == 1
    publications = producer.publication_tables(packet)
    assert [name for name, _ in publications] == [FEATURE_TABLE, COVERAGE_TABLE]
    consumer = Client(packet=packet)
    loaded = native.load_completed_return_projection(issue_source_plan(packet), consumer)
    assert loaded.token == packet.token and len(consumer.sql) == 2 and len(certified) == 2
    rows = loaded.rows.to_pylist()
    assert rows[0]['return5'] == pytest.approx(.1)
    assert rows[0]['price_scaled_momentum'] == pytest.approx(1.1)
    assert rows[0]['price_scaled_momentum'] != pytest.approx(1.0)  # not P1-P6
    assert rows[2]['return5'] is None and rows[2]['return_available'] == 0
    eligible = np.ones(3, dtype=bool)
    a = CompletedReturnRankingPolicy(ReturnQualification.POSITIVE_RETURN_REQUIRED)
    b = CompletedReturnRankingPolicy(ReturnQualification.OPTIONAL_RETURN_RANK)
    assert rank_completed_candidates(loaded, eligible, a).tolist() == [0]
    assert rank_completed_candidates(loaded, eligible, b).tolist() == [0, 1, 2]
    eligible[0] = False
    assert rank_completed_candidates(loaded, eligible, b).tolist() == [1, 2]


def test_exact_asof_age_and_completed_native_clock_no_future():
    p = plan(('AAA',))
    bars = [bar('AAA', 2, 100000), bar('AAA', 7, 110000),
            bar('AAA', 10, 500000)]  # forming target/decision minute never read
    keys = (('AAA', 600000), ('AAA', 600100), ('AAA', 660000))
    packet = producer.produce_completed_returns(p, keys, FEATURE_ATTEMPT, Client(bars))
    a, b, c = packet.rows.to_pylist()
    assert a['endpoint1_age_ms'] == a['endpoint6_age_ms'] == 120000
    assert a['endpoint1_boundary_ms'] == 480000 and a['feature_available_boundary_ms'] == 600000
    assert a['return5'] == b['return5'] == pytest.approx(.1)
    assert c['return_available'] == 0  # prior endpoint at lag6 is now three minutes old
    assert c['return5'] is None


def test_pm_never_invents_prior_day_and_ah_uses_rth_warmup():
    p = plan(('AAA',))
    bars = [bar('AAA', 714, 100000), bar('AAA', 719, 120000)]
    packet = producer.produce_completed_returns(p, (('AAA', 100), ('AAA', 43200000)),
        FEATURE_ATTEMPT, Client(bars))
    pm, ah = packet.rows.to_pylist()
    assert pm['return5'] is None and pm['endpoint6_boundary_ms'] is None
    assert ah['return5'] == pytest.approx(.2)
    assert ah['endpoint1_boundary_ms'] == 43200000  # 16:00 ET, completed 15:59 minute
    assert ah['endpoint6_boundary_ms'] == 42900000


def test_missing_certificate_fatal_before_source_read(monkeypatch):
    def fail(*args):
        raise ValueError('missing certified parent')
    monkeypatch.setattr(producer, 'verify_market_day_plan', fail)
    monkeypatch.setattr(native, 'verify_market_day_plan', fail)
    client = Client()
    with pytest.raises(ValueError, match='missing certified parent'):
        producer.produce_completed_returns(plan(), (('AAA', 600000),), FEATURE_ATTEMPT, client)
    assert client.sql == []
    with pytest.raises(ValueError, match='coverage'):
        ReturnSourceRequest(replace(plan(), units=()), (('AAA', 600000),))


def test_expected_empty_source_is_sealed_null_not_missing_certification():
    packet = producer.produce_completed_returns(plan(('AAA',)), (('AAA', 600000),),
        FEATURE_ATTEMPT, Client())
    assert packet.coverage['requested_count'].to_pylist() == [1]
    assert packet.coverage['available_count'].to_pylist() == [0]
    assert packet.rows['return5'].null_count == 1


@pytest.mark.parametrize('defect', ['duplicate', 'invalid_flags', 'foreign', 'schema'])
def test_canonical_stream_defects_fail_closed(defect):
    rows = [bar('AAA', 4, 100000)]
    if defect == 'duplicate':
        rows *= 2
    elif defect == 'invalid_flags':
        rows[0]['price_valid'] = 2
    elif defect == 'foreign':
        rows[0]['ticker'] = 'ZZZ'
    table = pa.Table.from_pylist(rows, schema=producer.SOURCE_SCHEMA)
    if defect == 'schema':
        table = table.drop(['high_int'])
    class BadClient:
        def iter_arrow_record_batches(self, sql):
            return iter(table.to_batches())
    with pytest.raises(ValueError):
        producer.produce_completed_returns(plan(('AAA',)), (('AAA', 600000),),
            FEATURE_ATTEMPT, BadClient())


def test_seal_key_coverage_and_feature_clock_tampering_rejected():
    packet = producer.produce_completed_returns(plan(('AAA',)), (('AAA', 600000),),
        FEATURE_ATTEMPT, Client([bar('AAA', 4, 100000), bar('AAA', 9, 110000)]))
    for column, value in [('price_scaled_momentum', 999.), ('endpoint1_boundary_ms', 660000)]:
        idx = packet.rows.schema.get_field_index(column)
        rows = packet.rows.set_column(idx, column, pa.array([value], type=packet.rows[column].type))
        with pytest.raises(ValueError):
            replace(packet, rows=rows)
    with pytest.raises(ValueError):
        replace(packet, coverage=packet.coverage.slice(0, 0))
    with pytest.raises(ValueError):
        native.load_completed_return_projection(replace(issue_source_plan(packet),
            request=ReturnSourceRequest(plan(('AAA',)), (('AAA', 600100),))), Client(packet=packet))


def test_typed_policy_and_explicit_mandatory_gate():
    with pytest.raises(ValueError):
        CompletedEndpointReturnPolicy(current_lag=True)
    with pytest.raises(ValueError):
        CompletedReturnRankingPolicy('optional_return_rank')
    packet = producer.produce_completed_returns(plan(('AAA',)), (('AAA', 600000),),
        FEATURE_ATTEMPT, Client())
    with pytest.raises(ValueError, match='Boolean'):
        rank_completed_candidates(packet, np.array([1]),
            CompletedReturnRankingPolicy(ReturnQualification.OPTIONAL_RETURN_RANK))
    assert all("storage_policy='live_market_ssd'" in sql for sql in ddl())


@pytest.mark.parametrize('column', ['return5', 'price_scaled_momentum'])
def test_wrong_finite_formula_rejected_even_when_resealed(column):
    packet = producer.produce_completed_returns(plan(('AAA',)), (('AAA', 600000),),
        FEATURE_ATTEMPT, Client([bar('AAA', 4, 100000), bar('AAA', 9, 110000)]))
    idx = packet.rows.schema.get_field_index(column)
    rows = packet.rows.set_column(idx, column, pa.array([999.], type=pa.float64()))
    idx = packet.coverage.schema.get_field_index('content_hash')
    coverage = packet.coverage.set_column(idx, 'content_hash', pa.array([table_hash(rows)]))
    with pytest.raises(ValueError, match='formula/units'):
        CompletedReturnProjection(packet.request, FEATURE_ATTEMPT, rows, coverage,
            projection_token(packet.request, FEATURE_ATTEMPT, rows, coverage))


def test_native_preflight_requires_exact_producer_seal_and_fatal_parent(monkeypatch):
    packet = producer.produce_completed_returns(plan(('AAA',)), (('AAA', 600000),),
        FEATURE_ATTEMPT, Client())
    source = issue_source_plan(packet)
    client = Client(packet=packet)
    with pytest.raises(ValueError, match='producer implementation'):
        native.load_completed_return_projection(replace(source, producer_source_hash='f'*64), client)
    def fail(*args):
        raise ValueError('missing certified parent')
    monkeypatch.setattr(native, 'verify_market_day_plan', fail)
    client.sql.clear()
    with pytest.raises(ValueError, match='missing certified parent'):
        native.load_completed_return_projection(source, client)
    assert not client.sql
    with pytest.raises(ValueError, match='source plan'):
        native.load_completed_return_projection(None, client)


def test_portable_producer_hash_normalizes_lf_crlf_and_bom_only():
    lf = b'"""source"""\nx = 1\n'
    crlf = b'\xef\xbb\xbf"""source"""\r\nx = 1\r\n'
    assert canonical_source_hash([('producer.py', lf)]) == canonical_source_hash([('producer.py', crlf)])
    assert canonical_source_hash([('producer.py', lf)]) != canonical_source_hash([('producer.py', lf.replace(b'1', b'2'))])
    assert canonical_source_hash([('producer.py', lf)]) != canonical_source_hash([('other.py', lf)])


def test_maximum_key_packet_is_columnar_and_bounded():
    tickers = tuple(f'T{i}' for i in range(8))
    keys = tuple((t, 600000 + i*100) for t in tickers for i in range(64))
    bars = [bar(t, m, price) for t in tickers for m, price in [(4, 100000), (9, 110000)]]
    client = Client(bars)
    packet = producer.produce_completed_returns(plan(tickers), keys, FEATURE_ATTEMPT, client)
    assert len(client.sql) == 1 and packet.rows.num_rows == 512
    indices = rank_completed_candidates(packet, np.ones(512, dtype=bool),
        CompletedReturnRankingPolicy(ReturnQualification.OPTIONAL_RETURN_RANK))
    assert indices[:8].tolist() == [i*64 for i in range(8)]
    with pytest.raises(ValueError, match='512'):
        ReturnSourceRequest(plan(tickers), keys + (('T7', 607000),))
