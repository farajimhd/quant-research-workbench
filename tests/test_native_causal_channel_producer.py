"""Producer runnable path with controlled canonical transport, no installation."""
from dataclasses import replace

import polars as pl
import pyarrow as pa
import pytest
from polars.testing import assert_frame_equal

from pipelines.market_sip.events import native_causal_channel_producer as producer
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.market_engine.native_causal_channel_contract import (
    NativeChannelPolicy, NativeChannelRequest, FEATURE_SCHEMA, SOURCE_SCHEMA, COVERAGE_SCHEMA,
    projection_token, producer_implementation_hash, issue_source_plan, ddl, require_bars_output_hash, table_hash,
)
from research.causal_strategy_features.v5.decisions import multi_resolution_decisions
from tests.test_causal_native_volatility_channels import source

ATTEMPT = '11111111-1111-4111-8111-111111111111'
FEATURE_ATTEMPT = '22222222-2222-4222-8222-222222222222'


def request(boundary=600000):
    unit = MarketDayUnit('a'*64, '2026-08-04', 'X', 'bars', ATTEMPT, 'b'*64, 20, '12345678901234567890')
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), 'a'*64, 'd'*64,
        ('2026-08-04',), ('X',), (unit,), (100, 60000, 300000), 'e'*64)
    policy = NativeChannelPolicy((60000, 300000), 100, ((60000, 59999), (300000, 299999)), 3, 3)
    return NativeChannelRequest(market, '2026-08-04', ('X',), boundary, policy)


def raw():
    frame = source().with_columns(pl.lit('a'*64).alias('build_id'),
        pl.lit(ATTEMPT).alias('attempt_id'))
    frame = pl.concat([frame, frame.with_columns(
        pl.lit(300000, dtype=frame.schema['resolution_ms']).alias('resolution_ms'))])
    return frame.filter((pl.col('bucket_index')+1)*pl.col('resolution_ms') <= 600000).to_arrow().cast(SOURCE_SCHEMA)


class Client:
    def __init__(self, table):
        self.table, self.sql = table, []

    def iter_arrow_record_batches(self, sql):
        self.sql.append(sql)
        assert sql.startswith('SELECT ') and 'FROM arte.bars_v1 ' in sql
        assert 'SETTINGS' not in sql and 'file(' not in sql
        assert f"toUUID('{ATTEMPT}')" in sql and 'LIMIT 13' in sql
        return iter(self.table.to_batches(max_chunksize=4))


@pytest.fixture
def verified(monkeypatch):
    calls = []
    monkeypatch.setattr(producer, 'verify_market_day_plan', lambda m, c: calls.append(m.token))
    return calls


def produce(verified, table=None):
    client = Client(raw() if table is None else table)
    return producer.produce_native_channels(request(), FEATURE_ATTEMPT, client), client


def test_source_to_typed_packet_to_multi_resolution_decisions(verified):
    packet, client = produce(verified)
    assert verified == ['e'*64] and len(client.sql) == 1
    assert packet.rows.schema == FEATURE_SCHEMA
    assert packet.coverage.schema == COVERAGE_SCHEMA
    assert packet.rows.num_rows == 12
    assert packet.coverage['row_count'].to_pylist() == [10, 2]
    assert packet.coverage['producer_source_hash'].unique().to_pylist() == [producer_implementation_hash()]
    sealed = issue_source_plan(packet)
    assert sealed.token == packet.token
    with pytest.raises(ValueError, match='seal'):
        replace(sealed, feature_hash='f'*64)
    assert [name for name, _ in producer.publication_tables(packet)] == [
        'arte.native_causal_channels_v1', 'arte.native_causal_channel_coverage_v1']
    features = pl.from_arrow(packet.rows)
    decisions = features.select('build_id', 'session_date', 'ticker', 'attempt_id').unique().with_columns(
        pl.lit(359900).alias('decision_day_ms'))
    aligned = multi_resolution_decisions(features, decisions, decision_interval_ms=100,
        freshness_by_resolution=request().policy.freshness_by_resolution)
    assert aligned['channels_60000ms'].struct.field('available_day_boundary_ms').to_list() == [300000]
    assert aligned['channels_300000ms'].struct.field('available_day_boundary_ms').to_list() == [300000]


def test_future_mutation_cannot_change_completed_prefix(verified):
    before, _ = produce(verified)
    frame = pl.from_arrow(raw())
    changed = frame.with_columns(pl.when((pl.col('bucket_index')+1)*pl.col('resolution_ms') > 300000)
        .then(pl.col('close_int')+1).otherwise(pl.col('close_int')).alias('close_int'))
    after, _ = produce(verified, changed.to_arrow().cast(SOURCE_SCHEMA))
    left = pl.from_arrow(before.rows).filter(pl.col('available_day_boundary_ms') <= 300000)
    right = pl.from_arrow(after.rows).filter(pl.col('available_day_boundary_ms') <= 300000)
    assert_frame_equal(left, right)


def test_expected_sparse_resolution_and_empty_source_are_covered(verified):
    sparse = pl.from_arrow(raw()).filter(pl.col('resolution_ms') == 60000).to_arrow().cast(SOURCE_SCHEMA)
    packet, _ = produce(verified, sparse)
    assert packet.coverage['row_count'].to_pylist() == [10, 0]
    empty, _ = produce(verified, raw().slice(0, 0))
    assert empty.rows.num_rows == 0 and empty.coverage['row_count'].to_pylist() == [0, 0]


@pytest.mark.parametrize('field,value', [('build_id', 'f'*64), ('attempt_id', FEATURE_ATTEMPT),
    ('session_date', '2026-08-05')])
def test_foreign_source_identity_rejected(verified, field, value):
    table = pl.from_arrow(raw()).with_columns(pl.lit(value).alias(field)).to_arrow().cast(SOURCE_SCHEMA)
    with pytest.raises(ValueError, match='foreign'):
        produce(verified, table)


def test_future_and_duplicate_source_rejected(verified):
    frame = pl.from_arrow(raw())
    future = frame.with_columns(pl.when(pl.col('resolution_ms') == 300000).then(2)
        .otherwise(pl.col('bucket_index')).cast(pl.UInt32).alias('bucket_index'))
    with pytest.raises(ValueError, match='future'):
        produce(verified, future.to_arrow().cast(SOURCE_SCHEMA))
    with pytest.raises(ValueError, match='row bound|Duplicate'):
        produce(verified, pa.concat_tables([raw(), raw().slice(0, 1)]))


def test_market_failure_prevents_source_read(monkeypatch):
    def fail(m, c):
        raise ValueError('Uncertified market')
    monkeypatch.setattr(producer, 'verify_market_day_plan', fail)
    client = Client(raw())
    with pytest.raises(ValueError, match='Uncertified'):
        producer.produce_native_channels(request(), FEATURE_ATTEMPT, client)
    assert client.sql == []


def test_changed_coverage_and_future_clock_rejected_even_when_resealed(verified):
    packet, _ = produce(verified)
    coverage = pl.from_arrow(packet.coverage).with_columns(pl.lit('f'*64).alias('bars_output_hash')).to_arrow().cast(COVERAGE_SCHEMA)
    with pytest.raises(ValueError, match='coverage/source'):
        replace(packet, coverage=coverage,
            token=projection_token(packet.request, FEATURE_ATTEMPT, packet.rows, coverage))
    rows = pl.from_arrow(packet.rows).with_columns((pl.col('available_day_boundary_ms')+100).alias('available_day_boundary_ms')).to_arrow().cast(FEATURE_SCHEMA)
    with pytest.raises(ValueError, match='availability'):
        replace(packet, rows=rows, token=projection_token(packet.request, FEATURE_ATTEMPT, rows, packet.coverage))


def test_policy_parameters_change_identity_and_invalid_declarations_fail():
    p = request().policy
    assert replace(p, decision_interval_ms=1000).digest != p.digest
    assert replace(p, participation_lookback_bars=4).digest != p.digest
    assert replace(p, volatility_lookback_bars=4).digest != p.digest
    assert replace(p, freshness_by_resolution=((60000, 0), (300000, 0))).digest != p.digest
    with pytest.raises(ValueError):
        replace(p, decision_interval_ms=True)
    with pytest.raises(ValueError):
        replace(p, freshness_by_resolution=((60000, 0),))
    with pytest.raises(ValueError, match='row bound'):
        replace(request(), through_day_boundary_ms=86400000,
            policy=NativeChannelPolicy((100,), 100, ((100, 99),)),
            market=replace(request().market, tickers=('A','B','C'),
                           required_resolutions_ms=(100,)), tickers=('A','B','C'))


def test_installation_contract_is_explicit_ssd_and_normalized():
    statements = ddl()
    assert len(statements) == 2
    assert all("storage_policy='live_market_ssd'" in sql for sql in statements)
    assert 'Nullable(Float64)' in statements[0]
    assert 'attempt_id UUID' in statements[0]
    assert all(' JSON' not in sql and ' default' not in sql for sql in statements)


@pytest.mark.parametrize('value', ['01', '-1', '1.0', ' 1', 'a'*64, str(2**64), 1, True])
def test_output_checksum_requires_actual_canonical_uint64_domain(value):
    with pytest.raises(ValueError, match='UInt64'):
        require_bars_output_hash(value)


def test_certified_output_checksum_extremes_are_exact():
    for value in ('0', '10502403561365938998', str(2**64-1)):
        require_bars_output_hash(value)


def test_content_hash_ignores_hidden_null_bytes_and_bitmap_padding_but_preserves_values():
    import struct
    first = pa.Array.from_buffers(pa.float64(), 3,
        [pa.py_buffer(bytes([0b101])), pa.py_buffer(struct.pack('<ddd', 1.0, 99.0, -0.0))])
    other = pa.Array.from_buffers(pa.float64(), 3,
        [pa.py_buffer(bytes([0b11111101])), pa.py_buffer(struct.pack('<ddd', 1.0, -777.0, -0.0))])
    before, after = pa.table({'x': first}), pa.table({'x': other})
    assert table_hash(before) == table_hash(after)
    assert table_hash(before) == table_hash(pl.from_arrow(before).to_arrow())
    assert table_hash(before) != table_hash(pa.table({'x': pa.array([1.0, None, 0.0])}))
    assert table_hash(before) != table_hash(pa.table({'x': pa.array([1.0, 0.0, -0.0])}))
    assert table_hash(pa.table({'x': pa.array([True, None, False])})) == table_hash(
        pl.from_arrow(pa.table({'x': pa.array([True, None, False])})).to_arrow())
