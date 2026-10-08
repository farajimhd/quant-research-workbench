"""SELECT-only native feature storage proof and exact installed packet readback."""
import json

import pyarrow as pa

from src.backend.backtest_market_data import _literal, verify_market_day_plan
from src.market_engine.native_causal_channel_contract import (
    FEATURE_TABLE, COVERAGE_TABLE, FEATURE_SCHEMA, COVERAGE_SCHEMA, STORAGE_POLICY,
    NativeChannelSourcePlan, NativeChannelProjection, issue_source_plan,
)
from src.market_engine.native_channel_insert_authority import NativeChannelInsertAuthority
from pipelines.market_sip.events.completed_endpoint_return_producer import read_arrow

TABLE_SCHEMAS = {FEATURE_TABLE: FEATURE_SCHEMA, COVERAGE_TABLE: COVERAGE_SCHEMA}


def column_types(schema):
    types = {pa.string(): 'String', pa.uint32(): 'UInt32', pa.uint64(): 'UInt64',
             pa.uint8(): 'UInt8', pa.int64(): 'Int64', pa.float64(): 'Float64', pa.bool_(): 'Bool'}
    return {f.name: ('Nullable(' if f.nullable else '') +
        ('UUID' if f.name.endswith('attempt_id') else types[f.type]) +
        (')' if f.nullable else '') for f in schema}


def _catalog(client, sql):
    names = ','.join(_literal(t.split('.')[1]) for t in TABLE_SCHEMAS)
    allowed = {
        f"SELECT disks FROM system.storage_policies WHERE policy_name='{STORAGE_POLICY}'",
        f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({names})",
        f"SELECT table,disk_name FROM system.parts WHERE active AND database='arte' AND table IN ({names}) AND disk_name!='{STORAGE_POLICY}' LIMIT 1",
        f"SELECT table,name,type FROM system.columns WHERE database='arte' AND table IN ({names})",
    }
    if sql not in allowed:
        raise ValueError('Native feature catalog proof accepts exact internal SELECTs only')
    # Read-only principals retain their configured resource policy (no SETTINGS).
    rows = [json.loads(line) for line in client.execute(sql + ' FORMAT JSONEachRow').splitlines() if line.strip()]
    if len(rows) > 128:
        raise ValueError('Native feature catalog proof exceeded declared row bound')
    return rows


def storage_preflight(client, *, allow_missing=False):
    policies = _catalog(client, f"SELECT disks FROM system.storage_policies WHERE policy_name='{STORAGE_POLICY}'")
    if not policies or any(row.get('disks') != [STORAGE_POLICY] for row in policies):
        raise ValueError('Native features require SSD-only policy; no fallback')
    names = {t.split('.')[1] for t in TABLE_SCHEMAS}
    quoted = ','.join(_literal(t.split('.')[1]) for t in TABLE_SCHEMAS)
    tables = _catalog(client, f"SELECT name,storage_policy FROM system.tables WHERE database='arte' AND name IN ({quoted})")
    existing = {r['name'] for r in tables}
    if (len(existing) != len(tables) or not existing.issubset(names) or
            any(r['storage_policy'] != STORAGE_POLICY for r in tables) or
            (not allow_missing and existing != names)):
        raise ValueError('Native feature tables lack exact SSD policy or coverage')
    if _catalog(client, "SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
                f"AND table IN ({quoted}) AND disk_name!='{STORAGE_POLICY}' LIMIT 1"):
        raise ValueError('Native feature parts misplaced; explicit migration required')
    columns = _catalog(client, f"SELECT table,name,type FROM system.columns WHERE database='arte' AND table IN ({quoted})")
    for table, schema in TABLE_SCHEMAS.items():
        name = table.split('.')[1]
        observed = [r for r in columns if r['table'] == name]
        if name in existing and (len(observed) != len(schema) or
                {r['name']: r['type'] for r in observed} != column_types(schema)):
            raise ValueError('Native feature installed column contract differs')


def read_packet_tables(client, request, attempt):
    """Internal exact attempt inventory, without filtering away foreign rows.

    Publication owns one globally unique attempt per bounded request. Extra
    identities under that attempt remain visible and fail typed validation.
    """
    from src.market_engine.native_causal_channel_contract import require_uuid
    request.__post_init__()
    require_uuid(attempt)
    output = []
    for table, schema in TABLE_SCHEMAS.items():
        fields = ','.join(f'toString({f.name}) AS {f.name}' if f.name.endswith('attempt_id')
                          else f.name for f in schema)
        bound = request.maximum_source_rows if table == FEATURE_TABLE else len(request.tickers) * len(request.policy.resolutions_ms)
        # Filter native UUID columns before output aliases can shadow them.
        order = 'ticker,resolution_ms' + (',bucket_index' if table == FEATURE_TABLE else '')
        sql = (f'SELECT {fields} FROM (SELECT {",".join(schema.names)} FROM {table} '
               f'WHERE feature_attempt_id=toUUID({_literal(attempt)}) ORDER BY {order} '
               f'LIMIT {bound+1}) FORMAT ArrowStream')
        output.append(read_arrow(client, sql, schema, bound))
    return tuple(output)


def read_installed_native_channels(client, source_plan, *, authority):
    """Fail closed on missing source, typed content, SSD placement or completion."""
    if type(source_plan) is not NativeChannelSourcePlan or type(authority) is not NativeChannelInsertAuthority:
        raise ValueError('Typed native feature source plan and producer fence required')
    source_plan.__post_init__()
    authority.assert_complete(source_plan.feature_attempt_id, source_plan.token)
    verify_market_day_plan(source_plan.request.market, client)
    storage_preflight(client)
    rows, coverage = read_packet_tables(client, source_plan.request, source_plan.feature_attempt_id)
    packet = NativeChannelProjection(source_plan.request, source_plan.feature_attempt_id,
                                     rows, coverage, source_plan.token)
    if issue_source_plan(packet) != source_plan:
        raise ValueError('Installed native feature content/source plan differs')
    authority.assert_complete(source_plan.feature_attempt_id, source_plan.token)
    return packet
