"""Bounded producer projection from exact certified bars; never a strategy indicator."""
from datetime import date

import polars as pl
import pyarrow as pa

from src.backend.backtest_market_data import _literal, assert_select_only, verify_market_day_plan
from src.market_engine.completed_endpoint_return_contract import (
    COVERAGE_SCHEMA, COVERAGE_TABLE, FEATURE_SCHEMA, FEATURE_TABLE, MAX_SOURCE_ROWS,
    MAX_TICKERS_PER_READ, CompletedReturnProjection, ReturnSourceRequest,
    projection_token, require_uuid, table_hash, producer_implementation_hash, PRODUCER_HASH_DOMAIN,
)

SOURCE_SCHEMA = pa.schema([
    ('ticker', pa.string()), ('bucket_index', pa.uint32()),
    ('close_int', pa.uint64()), ('high_int', pa.uint64()), ('low_int', pa.uint64()),
    ('price_valid', pa.uint8()), ('extremes_valid', pa.uint8()),
])


def read_arrow(client, sql, schema, maximum_rows):
    """Bounded typed stream, with no coercion, partial success or truncation."""
    assert_select_only(sql)
    stream = client.iter_arrow_record_batches(sql)
    batches, count = [], 0
    try:
        for batch in stream:
            if batch.schema.remove_metadata() != schema or batch.nbytes > 16 * 1024 * 1024:
                raise ValueError('Foreign/lossy schema or oversized Arrow batch')
            count += batch.num_rows
            if count > maximum_rows:
                raise ValueError('Source exceeded declared row bound')
            batches.append(batch.replace_schema_metadata(None))
    finally:
        close = getattr(stream, 'close', None)
        if close is not None:
            close()
    return pa.Table.from_batches(batches, schema=schema)


def produce_completed_returns(market, keys, feature_attempt_id, client):
    """Verify parent authority, project <=8 tickers per read, seal every requested key.

    All reads use pinned attempts and completed 30s source buckets. The producer
    emits explicit null rows for expected absent endpoints, including early PM.
    It neither installs tables nor publishes execution approval.
    """
    request = ReturnSourceRequest(market, keys)
    require_uuid(feature_attempt_id)
    producer_hash = producer_implementation_hash()
    verify_market_day_plan(market, client)
    source_tables = []
    for offset in range(0, len(request.tickers), MAX_TICKERS_PER_READ):
        tickers = request.tickers[offset:offset + MAX_TICKERS_PER_READ]
        buckets = sorted({(t, 480 + minute * 2 + half)
            for t, boundary in keys if t in tickers
            for lag in (1, 6) for age in range(3)
            for minute in (boundary // 60000 - lag - age,)
            if 0 <= minute < 960 for half in (0, 1)})
        if not buckets:
            continue
        pins = ','.join(f"({_literal(t)},toUUID({_literal(request.unit(t).attempt_id)}))" for t in tickers)
        wanted = ','.join(f'({_literal(t)},{b})' for t, b in buckets)
        sql = ('SELECT ticker,toUInt32(bucket_index) AS bucket_index,close_int,high_int,low_int,'
            'price_valid,extremes_valid FROM arte.bars_v1 '
            f'WHERE build_id={_literal(market.build_id)} AND session_date=toDate({_literal(market.sessions[0])}) '
            f'AND resolution_ms=30000 AND (ticker,attempt_id) IN ({pins}) '
            f'AND (ticker,bucket_index) IN ({wanted}) ORDER BY ticker,bucket_index '
            f'SETTINGS max_threads=1,max_execution_time=45,max_memory_usage=2147483648,'
            f'max_result_rows={MAX_SOURCE_ROWS},result_overflow_mode=\'throw\' FORMAT ArrowStream')
        source = read_arrow(client, sql, SOURCE_SCHEMA, MAX_SOURCE_ROWS)
        if any(source[name].null_count for name in SOURCE_SCHEMA.names):
            raise ValueError('Canonical bars have null required fields')
        frame = pl.from_arrow(source)
        wanted_keys = pl.DataFrame(buckets, schema={'ticker': pl.String, 'bucket_index': pl.UInt32}, orient='row')
        if (frame.select('ticker', 'bucket_index').is_duplicated().any()
                or frame.join(wanted_keys, on=['ticker', 'bucket_index'], how='anti').height
                or frame.filter((pl.col('price_valid') > 1) | (pl.col('extremes_valid') > 1)).height):
            raise ValueError('Canonical bars returned duplicate/foreign keys or invalid flags')
        source_tables.append(source)
    source = pa.concat_tables(source_tables) if source_tables else pa.Table.from_batches([], schema=SOURCE_SCHEMA)
    s = pl.from_arrow(source).with_columns(
        (pl.col('bucket_index').cast(pl.Int32) // 2 - 240).alias('source_minute'),
        pl.when((pl.col('price_valid') == 1) & (pl.col('close_int') > 0)).then(pl.col('close_int')).alias('valid_close'),
        ((pl.col('price_valid') == 1) & (pl.col('extremes_valid') == 1)
         & (pl.col('low_int') > 0) & (pl.col('high_int') >= pl.col('low_int'))
         & pl.col('close_int').is_between(pl.col('low_int'), pl.col('high_int'))).alias('valid_geometry'),
    ).sort('ticker', 'bucket_index')
    minutes = s.group_by('ticker', 'source_minute').agg(
        pl.col('valid_close').drop_nulls().last().alias('source_close'),
        pl.col('valid_geometry').sum().alias('valid_rows'),
    ).filter((pl.col('valid_rows') > 0) & (pl.col('source_close') > 0)).sort('ticker', 'source_minute')
    rows = pl.DataFrame(keys, schema={'ticker': pl.String, 'decision_boundary_ms': pl.UInt32}, orient='row')
    rows = rows.with_columns((pl.col('decision_boundary_ms') // 60000).cast(pl.UInt16).alias('anchor_minute'))
    for lag in (1, 6):
        target = f'target{lag}_minute'
        rows = rows.with_columns((pl.col('anchor_minute').cast(pl.Int32) - lag).alias(target))
        lookup = minutes.select('ticker', pl.col('source_minute').alias(f'source{lag}_minute'),
            pl.col('source_close').alias(f'endpoint{lag}_close_int')).sort('ticker', f'source{lag}_minute')
        rows = rows.sort('ticker', target).join_asof(lookup, left_on=target,
            right_on=f'source{lag}_minute', by='ticker', strategy='backward', tolerance=2,
            check_sortedness=False).with_columns(
                ((pl.col(f'source{lag}_minute') + 1) * 60000).cast(pl.UInt32).alias(f'endpoint{lag}_boundary_ms'),
                ((pl.col(target) - pl.col(f'source{lag}_minute')) * 60000).cast(pl.UInt32).alias(f'endpoint{lag}_age_ms'))
    pins = pl.DataFrame({'ticker': request.tickers,
                        'bars_attempt_id': [request.unit(t).attempt_id for t in request.tickers]})
    rows = rows.join(pins, on='ticker', how='left').with_columns(
        pl.lit(market.build_id).alias('build_id'), pl.lit(date.fromisoformat(market.sessions[0])).alias('session_date'),
        pl.lit(feature_attempt_id).alias('feature_attempt_id'), pl.lit(request.policy.digest).alias('policy_digest'),
        pl.col('decision_boundary_ms').alias('feature_available_boundary_ms'),
        (pl.col('endpoint1_close_int').is_not_null() & pl.col('endpoint6_close_int').is_not_null()).cast(pl.UInt8).alias('return_available'),
        (pl.col('endpoint1_close_int').cast(pl.Float64) / pl.col('endpoint6_close_int').cast(pl.Float64) - 1).alias('return5'),
    ).with_columns((pl.col('endpoint1_close_int').cast(pl.Float64) / 10000 * pl.col('return5')).alias('price_scaled_momentum'))
    rows = rows.sort('ticker', 'decision_boundary_ms').select(FEATURE_SCHEMA.names).to_arrow().cast(FEATURE_SCHEMA)
    coverage = []
    if producer_hash != producer_implementation_hash():
        raise ValueError('Producer implementation changed during bounded projection')
    for ticker in request.tickers:
        child = rows.filter(pa.compute.equal(rows['ticker'], ticker))
        unit = request.unit(ticker)
        coverage.append(dict(build_id=market.build_id, session_date=date.fromisoformat(market.sessions[0]),
            ticker=ticker, feature_attempt_id=feature_attempt_id, bars_attempt_id=unit.attempt_id,
            bars_source_hash=unit.source_hash, bars_output_hash=unit.output_hash,
            policy_digest=request.policy.digest, producer_source_hash_domain=PRODUCER_HASH_DOMAIN,
            producer_source_hash=producer_hash, market_plan_token=market.token,
            requested_keys_hash=request.keys_hash(ticker), requested_count=child.num_rows,
            available_count=sum(child['return_available'].to_pylist()), content_hash=table_hash(child)))
    coverage = pa.Table.from_pylist(coverage, schema=COVERAGE_SCHEMA)
    return CompletedReturnProjection(request, feature_attempt_id, rows, coverage,
        projection_token(request, feature_attempt_id, rows, coverage))


def publication_tables(projection):
    """Immutable typed producer packet: publish child rows before coverage-last.

    Installation/publication must validate explicit SSD policy/part placement and
    use the established producer campaign/seal owner. This function performs no
    market writes or DDL and is not a substitute for that approval.
    """
    projection.__post_init__()
    return ((FEATURE_TABLE, projection.rows), (COVERAGE_TABLE, projection.coverage))
