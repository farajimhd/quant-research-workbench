"""Restart-safe native feature publication, with durable dispatch and coverage last."""
import polars as pl
import pyarrow as pa

from src.backend.backtest_market_data import verify_market_day_plan
from src.backend.backtest_native_channel_store import storage_preflight, read_packet_tables
from src.market_engine.native_causal_channel_contract import (
    NativeChannelProjection, FEATURE_TABLE, COVERAGE_TABLE, ddl, table_hash,
    producer_implementation_hash,
)
from src.market_engine.native_channel_insert_authority import NativeChannelInsertAuthority


def install(client):
    """Explicit separate producer operation; never called by Backtest or publish."""
    storage_preflight(client, allow_missing=True)
    for sql in ddl():
        client.execute(sql)
    storage_preflight(client)


def _remaining(existing, expected, keys):
    actual, wanted = pl.from_arrow(existing), pl.from_arrow(expected)
    if actual.select(keys).is_duplicated().any() or actual.join(wanted.select(keys), on=keys, how='anti').height:
        raise ValueError('Native feature partial write has duplicate/foreign keys')
    matched = wanted.join(actual.select(keys), on=keys, how='semi').sort(keys).to_arrow().cast(expected.schema)
    if table_hash(matched) != table_hash(actual.sort(keys).to_arrow().cast(expected.schema)):
        raise ValueError('Native feature persisted partial rows differ; immutable attempt held')
    return wanted.join(actual.select(keys), on=keys, how='anti').sort(keys).to_arrow().cast(expected.schema)


def _insert(client, table, rows, lease, verify):
    if rows.num_rows == 0:
        return
    storage_preflight(client)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, rows.schema) as writer:
        writer.write_table(rows)
    header = (f"INSERT INTO {table} ({','.join(rows.schema.names)}) "
        "SETTINGS max_threads=1,max_execution_time=45,max_memory_usage=2147483648,async_insert=0 FORMAT ArrowStream\n").encode()
    lease.execute(client, table, header + sink.getvalue().to_pybytes(), verify)


def publish_packet(client, projection, *, authority):
    """No repairs, deletes or INSERT retries; unknown outcomes retain a closed gate."""
    if type(projection) is not NativeChannelProjection or type(authority) is not NativeChannelInsertAuthority:
        raise ValueError('Exact native producer packet and product ownership required')
    projection.__post_init__()
    verify_market_day_plan(projection.request.market, client)
    if projection.coverage['producer_source_hash'].unique().to_pylist() != [producer_implementation_hash()]:
        raise ValueError('Native channel producer implementation identity differs')
    storage_preflight(client)
    attempt, request = projection.feature_attempt_id, projection.request
    with authority.ownership(attempt) as lease:
        features, coverage = read_packet_tables(client, request, attempt)
        lease.admit_existing(bool(features.num_rows or coverage.num_rows))
        feature_keys = ['ticker', 'resolution_ms', 'bucket_index']
        coverage_keys = ['ticker', 'resolution_ms']
        remaining_features = _remaining(features, projection.rows, feature_keys)
        remaining_coverage = _remaining(coverage, projection.coverage, coverage_keys)
        if coverage.num_rows and remaining_features.num_rows:
            raise ValueError('Native feature coverage preceded complete child rows')

        def verify_features():
            storage_preflight(client)
            actual, _ = read_packet_tables(client, request, attempt)
            if table_hash(actual) != table_hash(projection.rows):
                raise ValueError('Native feature readback incomplete; dispatch unverified')

        def verify_coverage():
            storage_preflight(client)
            actual, cover = read_packet_tables(client, request, attempt)
            if table_hash(actual) != table_hash(projection.rows) or table_hash(cover) != table_hash(projection.coverage):
                raise ValueError('Native feature coverage readback incomplete; dispatch unverified')

        _insert(client, FEATURE_TABLE, remaining_features, lease, verify_features)
        verify_features()
        _insert(client, COVERAGE_TABLE, remaining_coverage, lease, verify_coverage)
        verify_coverage()
        lease.complete(projection.token)
        return dict(status='published' if remaining_features.num_rows or remaining_coverage.num_rows else 'skipped',
            inserted_feature_rows=remaining_features.num_rows, inserted_coverage_rows=remaining_coverage.num_rows)
