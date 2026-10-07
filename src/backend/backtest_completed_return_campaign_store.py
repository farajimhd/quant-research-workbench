"""SELECT-only installed return product storage and certificate proof."""
import json
import pyarrow as pa
from src.backend.backtest_market_data import _literal, assert_select_only
from src.market_engine.completed_endpoint_return_contract import FEATURE_SCHEMA, FEATURE_TABLE, COVERAGE_SCHEMA, COVERAGE_TABLE, STORAGE_POLICY
from src.market_engine.completed_return_campaign_contract import CERTIFICATE_SCHEMA, CERTIFICATE_TABLE
TABLE_SCHEMAS = {FEATURE_TABLE: FEATURE_SCHEMA, COVERAGE_TABLE: COVERAGE_SCHEMA, CERTIFICATE_TABLE: CERTIFICATE_SCHEMA}

def _metadata(client, sql):
    # The general source guard rejects the word SYSTEM even in catalog table
    # identifiers. Keep it unchanged. Catalog proof uses only these four exact
    # internal SELECT statements, following existing candidate-store preflight.
    names = ','.join(_literal(name.split('.')[1]) for name in TABLE_SCHEMAS)
    allowed = {
        f"SELECT disks FROM system.storage_policies WHERE policy_name='{STORAGE_POLICY}'",
        f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({names})",
        f"SELECT table,disk_name FROM system.parts WHERE active AND database='arte' AND table IN ({names}) AND disk_name!='{STORAGE_POLICY}' LIMIT 1",
        f"SELECT table,name,type FROM system.columns WHERE database='arte' AND table IN ({names})",
    }
    if sql not in allowed:
        raise ValueError('Catalog proof accepts only exact internal storage SELECTs')
    return [json.loads(line) for line in client.execute(sql +
        " SETTINGS max_threads=1,max_execution_time=45,max_memory_usage=2147483648,max_result_rows=128,result_overflow_mode='throw' FORMAT JSONEachRow").splitlines() if line.strip()]


def _types(schema):
    types = {pa.string(): 'String', pa.date32(): 'Date', pa.uint32(): 'UInt32',
             pa.uint16(): 'UInt16', pa.int32(): 'Int32', pa.uint64(): 'UInt64',
             pa.uint8(): 'UInt8', pa.float64(): 'Float64'}
    result = {}
    for field in schema:
        kind = 'UUID' if field.name.endswith('attempt_id') else types[field.type]
        if field.name.startswith('endpoint') or field.name in ('return5', 'price_scaled_momentum'):
            kind = f'Nullable({kind})'
        result[field.name] = kind
    return result


def storage_preflight(client, *, allow_missing=False):
    """Validate SSD policy, table types and actual active parts before every writer."""
    policies = _metadata(client, f"SELECT disks FROM system.storage_policies WHERE policy_name='{STORAGE_POLICY}'")
    if not policies or any(row.get('disks') != [STORAGE_POLICY] for row in policies):
        raise ValueError('Return campaign requires SSD-only policy; no fallback')
    names = tuple(table.split('.')[1] for table in TABLE_SCHEMAS)
    quoted = ','.join(_literal(name) for name in names)
    catalog = _metadata(client, f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({quoted})")
    existing = {row['name'] for row in catalog}
    if (len(existing) != len(catalog) or any(row['storage_policy'] != STORAGE_POLICY for row in catalog)
            or (not allow_missing and existing != set(names))):
        raise ValueError('Return campaign tables have missing/foreign SSD policy')
    parts = _metadata(client, "SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
        f"AND table IN ({quoted}) AND disk_name!='{STORAGE_POLICY}' LIMIT 1")
    if parts:
        raise ValueError('Return campaign active parts misplaced; explicit migration required')
    columns = _metadata(client, f"SELECT table,name,type FROM system.columns WHERE database='arte' AND table IN ({quoted})")
    for table, schema in TABLE_SCHEMAS.items():
        name = table.split('.')[1]
        if name in existing:
            observed = {row['name']: row['type'] for row in columns if row['table'] == name}
            if observed != _types(schema) or len(observed) != sum(row['table'] == name for row in columns):
                raise ValueError('Return campaign installed column contract differs')


def _arrow(client, sql):
    assert_select_only(sql)
    stream = client.iter_arrow_record_batches(sql)
    batches, count = [], 0
    try:
        for batch in stream:
            if batch.schema.remove_metadata() != CERTIFICATE_SCHEMA or batch.nbytes > 16*1024*1024:
                raise ValueError('Foreign installed certificate Arrow contract')
            count += batch.num_rows
            if count > 2:
                raise ValueError('Installed packet certificate row bound exceeded')
            batches.append(batch.replace_schema_metadata(None))
    finally:
        close = getattr(stream, 'close', None)
        if close is not None:
            close()
    return pa.Table.from_batches(batches, schema=CERTIFICATE_SCHEMA)


def read_certificate(client, request, attempt):
    fields = ','.join(f'toString({name}) AS {name}' if name.endswith('attempt_id') else name
                      for name in CERTIFICATE_SCHEMA.names)
    sql = (f'SELECT {fields} FROM {CERTIFICATE_TABLE} WHERE build_id={_literal(request.market.build_id)} '
        f'AND session_date=toDate({_literal(request.market.sessions[0])}) '
        f'AND feature_attempt_id=toUUID({_literal(attempt)}) '
        "SETTINGS max_threads=1,max_execution_time=45,max_memory_usage=2147483648,max_result_rows=2,result_overflow_mode='throw' FORMAT ArrowStream")
    table = _arrow(client, sql)
    if table.num_rows > 1:
        raise ValueError('Return campaign has duplicate packet certification')
    return table


