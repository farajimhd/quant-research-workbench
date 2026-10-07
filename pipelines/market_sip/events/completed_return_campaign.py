"""Serialized, bounded, coverage-last publication and exact restart readback.

A required product-specific Keeper owner and durable dispatch fence serialize
each attempt. Unknown original INSERT outcomes block all subset resumption.
An uncertain insert stops immediately. A later explicit restart reads persisted
rows and inserts only proven missing keys, never retries the original operation.
"""
from datetime import date

import polars as pl
import pyarrow as pa

from src.backend.backtest_completed_endpoint_returns import _read
from src.market_engine.completed_endpoint_return_contract import (
    COVERAGE_SCHEMA, COVERAGE_TABLE, FEATURE_SCHEMA, FEATURE_TABLE, STORAGE_POLICY,
    ddl, issue_source_plan, producer_implementation_hash, table_hash,
)
from src.market_engine.completed_return_campaign_contract import (
    CERTIFICATE_SCHEMA, CERTIFICATE_TABLE, campaign_source_hash, certify_native_population,
)
from pipelines.market_sip.events.completed_endpoint_return_producer import (
    produce_completed_returns,
)

from src.backend.backtest_completed_return_campaign_store import (
    _types, storage_preflight, read_certificate,
)


def certificate_ddl():
    columns = ','.join(f'{name} {kind}' for name, kind in _types(CERTIFICATE_SCHEMA).items())
    return (f'CREATE TABLE IF NOT EXISTS {CERTIFICATE_TABLE} ({columns}) ENGINE=MergeTree '
        "PARTITION BY toYYYYMM(session_date) ORDER BY (build_id,session_date,feature_attempt_id) "
        f"SETTINGS storage_policy='{STORAGE_POLICY}'")


def install(client):
    """Explicit producer-owned installation API; not called by Backtest/publish."""
    storage_preflight(client, allow_missing=True)
    for statement in (*ddl(), certificate_ddl()):
        client.execute(statement)
    storage_preflight(client)


def certificate_for(population, index, projection):
    source = issue_source_plan(projection)
    return pa.Table.from_pylist([dict(build_id=population.market.build_id,
        session_date=date.fromisoformat(population.market.sessions[0]),
        feature_attempt_id=source.feature_attempt_id, population_token=population.token,
        decision_source_kind=population.source_kind.value,
        decision_source_token=population.candidate_token, population_count=len(population.keys),
        population_keys_hash=population.keys_hash, packet_index=index,
        packet_count=population.packet_count, requested_count=projection.rows.num_rows,
        producer_source_hash=source.producer_source_hash, campaign_source_hash=population.campaign_source_hash,
        policy_digest=projection.request.policy.digest, feature_hash=source.feature_hash,
        coverage_hash=source.coverage_hash, projection_token=source.token)], schema=CERTIFICATE_SCHEMA)


def _remaining(existing, expected, keys):
    """Only exact persisted subsets may resume; duplicates/mutations never repaired."""
    e, wanted = pl.from_arrow(existing), pl.from_arrow(expected)
    if e.select(keys).is_duplicated().any() or e.join(wanted.select(keys), on=keys, how='anti').height:
        raise ValueError('Return campaign partial write has duplicate/foreign keys')
    matched = wanted.join(e.select(keys), on=keys, how='semi').sort(keys).to_arrow().cast(expected.schema)
    actual = e.sort(keys).to_arrow().cast(expected.schema)
    if table_hash(matched) != table_hash(actual):
        raise ValueError('Return campaign persisted partial rows differ; immutable attempt held')
    return wanted.join(e.select(keys), on=keys, how='anti').sort(keys).to_arrow().cast(expected.schema)


def _insert(client, table, rows, lease, verify_readback):
    if rows.num_rows == 0:
        return
    storage_preflight(client)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, rows.schema) as writer:
        writer.write_table(rows)
    columns = ','.join(rows.schema.names)
    header = (f'INSERT INTO {table} ({columns}) SETTINGS max_threads=1,max_execution_time=45,'
              'max_memory_usage=2147483648,async_insert=0 FORMAT ArrowStream\n').encode()
    lease.execute(client, table, header + sink.getvalue().to_pybytes(), verify_readback)


def _publish_owned(client, population, index, projection, lease):
    """Read/resume exact unit  ->  complete child  ->  complete coverage  ->  certificate.

    No deletes, replacement, retries, mutation or silent deduplication. Every
    restart must re-certify the whole native decision population independently.
    """
    verified = certify_native_population(population.market, client, source_kind=population.source_kind)
    if verified != population:
        raise ValueError('Return campaign population differs from current certified native source')
    keys, attempt = population.packet(index)
    projection.__post_init__()
    if (projection.request.market != population.market or projection.request.keys != keys
            or projection.feature_attempt_id != attempt
            or projection.coverage['producer_source_hash'].unique().to_pylist() != [population.producer_source_hash]
            or producer_implementation_hash() != population.producer_source_hash
            or campaign_source_hash() != population.campaign_source_hash):
        raise ValueError('Return campaign packet/source identity differs')
    storage_preflight(client)
    certificate = certificate_for(population, index, projection)
    old_certificate = read_certificate(client, projection.request, attempt)
    existing_features = _read(client, FEATURE_TABLE, FEATURE_SCHEMA, projection.request, attempt)
    existing_coverage = _read(client, COVERAGE_TABLE, COVERAGE_SCHEMA, projection.request, attempt)
    lease.admit_existing(bool(existing_features.num_rows or existing_coverage.num_rows or old_certificate.num_rows))
    remaining_features = _remaining(existing_features, projection.rows, ['ticker', 'decision_boundary_ms'])
    remaining_coverage = _remaining(existing_coverage, projection.coverage, ['ticker'])
    if (old_certificate.num_rows or existing_coverage.num_rows) and remaining_features.num_rows:
        raise ValueError('Coverage/certification preceded complete features; immutable attempt held')
    if old_certificate.num_rows:
        if remaining_coverage.num_rows or table_hash(old_certificate) != table_hash(certificate):
            raise ValueError('Published certification differs from complete installed output')
        lease.complete(projection.token)
        return dict(status='skipped', inserted_feature_rows=0, inserted_coverage_rows=0)
    def verify_features():
        actual = _read(client, FEATURE_TABLE, FEATURE_SCHEMA, projection.request, attempt)
        if table_hash(actual) != table_hash(projection.rows):
            raise ValueError('Feature readback incomplete; dispatch remains unverified')
    def verify_coverage():
        actual = _read(client, COVERAGE_TABLE, COVERAGE_SCHEMA, projection.request, attempt)
        if table_hash(actual) != table_hash(projection.coverage):
            raise ValueError('Coverage readback incomplete; dispatch remains unverified')
    def verify_certificate():
        actual = read_certificate(client, projection.request, attempt)
        if table_hash(actual) != table_hash(certificate):
            raise ValueError('Certificate readback incomplete; dispatch remains unverified')
    _insert(client, FEATURE_TABLE, remaining_features, lease, verify_features)
    actual = _read(client, FEATURE_TABLE, FEATURE_SCHEMA, projection.request, attempt)
    if table_hash(actual) != table_hash(projection.rows):
        raise ValueError('Feature insertion/readback incomplete; coverage not published')
    _insert(client, COVERAGE_TABLE, remaining_coverage, lease, verify_coverage)
    actual = _read(client, COVERAGE_TABLE, COVERAGE_SCHEMA, projection.request, attempt)
    if table_hash(actual) != table_hash(projection.coverage):
        raise ValueError('Coverage insertion/readback incomplete; certification not published')
    _insert(client, CERTIFICATE_TABLE, certificate, lease, verify_certificate)
    actual = read_certificate(client, projection.request, attempt)
    if table_hash(actual) != table_hash(certificate):
        raise ValueError('Packet certification readback differs; no success receipt')
    storage_preflight(client)
    lease.complete(projection.token)
    return dict(status='published', inserted_feature_rows=remaining_features.num_rows,
                inserted_coverage_rows=remaining_coverage.num_rows)


def publish_packet(client, population, index, projection, *, authority):
    from src.market_engine.completed_return_insert_authority import KeeperProductInsertAuthority
    if type(authority) is not KeeperProductInsertAuthority:
        raise ValueError('Publisher requires typed Keeper producer ownership/dispatch authority')
    _, attempt = population.packet(index)
    with authority.ownership(attempt) as lease:
        return _publish_owned(client, population, index, projection, lease)


def produce_and_publish_packet(client, population, index, *, authority):
    """Smallest explicit producer -> persist/readback -> certificate callable unit."""
    from src.market_engine.completed_return_insert_authority import KeeperProductInsertAuthority
    if type(authority) is not KeeperProductInsertAuthority:
        raise ValueError('Producer requires typed Keeper ownership/dispatch authority')
    keys, attempt = population.packet(index)
    with authority.ownership(attempt) as lease:
        projection = produce_completed_returns(population.market, keys, attempt, client)
        return _publish_owned(client, population, index, projection, lease)


def run_campaign(client, market, *, source_kind, progress, authority):
    """Serialized explicit restart: compact phase counts; stop on first failure.

    The controlling launcher must persist progress/receipts outside source,
    own serialization, freeze implementation bytes, and call this anew after
    an explicit restart decision. No automatic failed-operation retry occurs.
    """
    if not callable(progress):
        raise ValueError('Campaign requires an explicit progress/receipt sink')
    population = certify_native_population(market, client, source_kind=source_kind)
    completed = skipped = 0
    for index in range(population.packet_count):
        state = dict(population_token=population.token, packet_index=index,
            total=population.packet_count, completed=completed, skipped=skipped,
            queued=population.packet_count-index-1, active=1, failed=0, retried=0)
        progress(dict(state, phase='active'))
        try:
            result = produce_and_publish_packet(client, population, index, authority=authority)
        except Exception as exc:
            progress(dict(state, phase='failed', active=0, failed=1,
                error_type=type(exc).__name__, partial_attempt_held=True))
            raise
        completed += 1
        skipped += int(result['status'] == 'skipped')
        progress(dict(state, phase='completed', active=0, completed=completed,
            skipped=skipped, **result))
    progress(dict(phase='terminal', population_token=population.token,
        total=population.packet_count, completed=completed, skipped=skipped,
        queued=0, active=0, failed=0, retried=0))
    return population
