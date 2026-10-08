"""Bounded producer-only native channels from pinned canonical arte bars."""
import polars as pl
import pyarrow as pa

from src.backend.backtest_market_data import _literal, verify_market_day_plan
from src.market_engine.native_causal_channel_contract import (
    NativeChannelRequest, NativeChannelProjection, SOURCE_SCHEMA, FEATURE_SCHEMA,
    COVERAGE_SCHEMA, FEATURE_TABLE, COVERAGE_TABLE, HASH_DOMAIN,
    producer_implementation_hash, projection_token, require_uuid, table_hash,
)
from pipelines.market_sip.events.completed_endpoint_return_producer import read_arrow
from research.causal_strategy_features.v4.native_bars import native_channels


def produce_native_channels(request, feature_attempt_id, client):
    """Reads full earlier-day context; publishes no tables and grants no admission.

    Source rows remain columnar through normalization. Small per-ticker/resolution
    loops seal typed partitions only, never process market decisions or fills.
    The immutable packet contains explicit zero-count coverage for expected sparse
    source resolutions. Missing source certification fails before any bar read.
    """
    if type(request) is not NativeChannelRequest:
        raise ValueError('Typed native channel request required')
    request.__post_init__()
    require_uuid(feature_attempt_id)
    producer_hash = producer_implementation_hash()
    verify_market_day_plan(request.market, client)
    pins = ','.join(f'({_literal(t)},toUUID({_literal(request.unit(t).attempt_id)}))'
                    for t in request.tickers)
    resolutions = ','.join(map(str, request.policy.resolutions_ms))
    sql = ('SELECT build_id,toString(session_date) AS session_date,toString(ticker) AS ticker,'
           'toString(attempt_id) AS attempt_id,resolution_ms,bucket_index,'
           'open_int,high_int,low_int,close_int,execution_volume,execution_notional,'
           'trade_count,price_valid,extremes_valid FROM (SELECT '
           'build_id,session_date,ticker,attempt_id,resolution_ms,bucket_index,'
           'open_int,high_int,low_int,close_int,execution_volume,execution_notional,'
           'trade_count,price_valid,extremes_valid FROM arte.bars_v1 '
           f'WHERE build_id={_literal(request.market.build_id)} '
           f'AND session_date=toDate({_literal(request.session_date)}) '
           f'AND (ticker,attempt_id) IN ({pins}) AND resolution_ms IN ({resolutions}) '
           f'AND (toUInt64(bucket_index)+1)*resolution_ms<={request.through_day_boundary_ms} '
           'ORDER BY ticker,resolution_ms,bucket_index '
           f'LIMIT {request.maximum_source_rows+1}) FORMAT ArrowStream')
    source = read_arrow(client, sql, SOURCE_SCHEMA, request.maximum_source_rows)
    raw = pl.from_arrow(source)
    expected = pl.DataFrame({'ticker': request.tickers,
                            'attempt_id': [request.unit(t).attempt_id for t in request.tickers]})
    if (any(source[n].null_count for n in SOURCE_SCHEMA.names) or
            raw.filter((pl.col('build_id') != request.market.build_id) |
                       (pl.col('session_date') != request.session_date)).height or
            raw.join(expected, on=['ticker', 'attempt_id'], how='anti').height or
            raw.filter((pl.col('bucket_index').cast(pl.Int64)+1)*pl.col('resolution_ms') >
                       request.through_day_boundary_ms).height):
        raise ValueError('Native producer received foreign or future source rows')
    result = native_channels(raw, resolutions_ms=request.policy.resolutions_ms,
        through_day_boundary_ms=request.through_day_boundary_ms,
        lookback_bars=request.policy.participation_lookback_bars,
        volatility_lookback_bars=request.policy.volatility_lookback_bars)
    rows = result.with_columns(pl.lit(feature_attempt_id).alias('feature_attempt_id'),
        pl.lit(request.policy.digest).alias('policy_digest')).sort('ticker', 'resolution_ms', 'bucket_index')
    rows = rows.select(FEATURE_SCHEMA.names).to_arrow().cast(FEATURE_SCHEMA, safe=True)
    coverage = []
    for ticker in request.tickers:
        unit = request.unit(ticker)
        for res in request.policy.resolutions_ms:
            child = rows.filter(pa.compute.and_(pa.compute.equal(rows['ticker'], ticker),
                                               pa.compute.equal(rows['resolution_ms'], res)))
            coverage.append(dict(build_id=request.market.build_id, session_date=request.session_date,
                ticker=ticker, attempt_id=unit.attempt_id, feature_attempt_id=feature_attempt_id,
                policy_digest=request.policy.digest, bars_source_hash=unit.source_hash,
                bars_output_hash=unit.output_hash, market_plan_token=request.market.token,
                producer_source_hash_domain=HASH_DOMAIN, producer_source_hash=producer_hash,
                content_hash=table_hash(child), resolution_ms=res,
                through_day_boundary_ms=request.through_day_boundary_ms, row_count=child.num_rows,
                candle_count=pa.compute.sum(child['candle_available']).as_py() or 0))
    coverage = pa.Table.from_pylist(coverage, schema=COVERAGE_SCHEMA)
    if producer_hash != producer_implementation_hash():
        raise ValueError('Native feature producer source changed during projection')
    return NativeChannelProjection(request, feature_attempt_id, rows, coverage,
        projection_token(request, feature_attempt_id, rows, coverage))


def publication_tables(projection):
    """Child-first, coverage-last packet; owner must separately verify SSD placement."""
    if type(projection) is not NativeChannelProjection:
        raise ValueError('Exact typed native channel projection required')
    projection.__post_init__()
    return ((FEATURE_TABLE, projection.rows), (COVERAGE_TABLE, projection.coverage))
