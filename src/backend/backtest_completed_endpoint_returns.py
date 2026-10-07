"""SELECT-only certified return reader; no strategy/private indicator derivation."""
from src.backend.backtest_market_data import _literal, verify_market_day_plan
from src.market_engine.completed_endpoint_return_contract import (
    COVERAGE_SCHEMA, COVERAGE_TABLE, FEATURE_SCHEMA, FEATURE_TABLE, MAX_KEYS,
    CompletedReturnProjection, CompletedReturnSourcePlan, table_hash,
)


def _read(client, table, schema, request, attempt):
    # Keep the native reader independent of producer calculation code.
    import pyarrow as pa
    from src.backend.backtest_market_data import assert_select_only
    columns = ','.join(f'toString({name}) AS {name}' if name.endswith('attempt_id') else name
                       for name in schema.names)
    sql = (f'SELECT {columns} FROM {table} WHERE build_id={_literal(request.market.build_id)} '
        f'AND session_date=toDate({_literal(request.market.sessions[0])}) '
        f'AND feature_attempt_id=toUUID({_literal(attempt)}) ORDER BY ticker'
        + (',decision_boundary_ms' if table == FEATURE_TABLE else '')
        + f" SETTINGS max_threads=1,max_execution_time=45,max_memory_usage=2147483648,max_result_rows={MAX_KEYS},result_overflow_mode='throw' FORMAT ArrowStream")
    assert_select_only(sql)
    stream, batches, count = client.iter_arrow_record_batches(sql), [], 0
    try:
        for batch in stream:
            if batch.schema.remove_metadata() != schema or batch.nbytes > 16 * 1024 * 1024:
                raise ValueError('Return native reader received foreign Arrow types/batch')
            count += batch.num_rows
            if count > MAX_KEYS:
                raise ValueError('Return native reader exceeded packet bound')
            batches.append(batch.replace_schema_metadata(None))
    finally:
        close = getattr(stream, 'close', None)
        if close is not None:
            close()
    return pa.Table.from_batches(batches, schema=schema)


def load_completed_return_projection(source, client):
    """Require complete parent certification and immutable normalized child seals.

    Missing coverage is fatal. A certified explicit null endpoint is an expected
    unavailable feature, not a zero return or source-certification exemption.
    """
    if type(source) is not CompletedReturnSourcePlan:
        raise ValueError('Native return reader requires pinned producer-issued source plan')
    source.__post_init__()
    request, feature_attempt_id = source.request, source.feature_attempt_id
    verify_market_day_plan(request.market, client)
    coverage = _read(client, COVERAGE_TABLE, COVERAGE_SCHEMA, request, feature_attempt_id)
    rows = _read(client, FEATURE_TABLE, FEATURE_SCHEMA, request, feature_attempt_id)
    if table_hash(rows) != source.feature_hash or table_hash(coverage) != source.coverage_hash:
        raise ValueError('Native return product differs from pinned producer seal')
    if coverage['producer_source_hash'].unique().to_pylist() != [source.producer_source_hash]:
        raise ValueError('Native return product differs from pinned producer implementation')
    return CompletedReturnProjection(request, feature_attempt_id, rows, coverage,
        source.token)
