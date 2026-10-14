"""Shared coverage-last publication; no schema installation or automatic retry."""
from src.market_engine.feature_source_plan import FeatureSourcePlan, TABLES, UNIT, SEAL, wire_records
from src.market_engine.typed_product_insert import insert_product_rows
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.backend.qmd_columnar_history import _rows, permission_preflight
from src.backend.qmd_feature_source_plan import read_feature_source_plan


def publish_feature_source_plan(reader, writer, plan, *, ownership, verify_sources):
    if type(plan) is not FeatureSourcePlan or not callable(verify_sources):
        raise TypeError('Feature publication requires its typed native producer plan')
    if reader is writer:
        raise ValueError('Feature source publication requires isolated principals')
    permission_preflight(reader, feature_sources=True)
    permission_preflight(writer, writable=True, feature_sources=True)
    storage_preflight(reader,tables=TABLES)
    ownership.assert_owner(); verify_sources(reader)
    condition=f"plan_hash='{plan.plan_hash}'"
    if _rows(reader,f'SELECT plan_hash FROM q_live.{SEAL.name} WHERE {condition} LIMIT 1',1):
        if read_feature_source_plan(reader,plan.plan_hash)!=plan:
            raise ValueError('Existing feature source plan differs')
        ownership.assert_owner()
        return 'skipped_verified'
    if _rows(reader,f'SELECT plan_hash FROM q_live.{UNIT.name} WHERE {condition} LIMIT 1',1):
        raise ValueError('Unsealed feature source plan requires operator reconciliation')
    records=plan.unit_records()
    for first in range(0,len(records),1024):
        ownership.assert_owner()
        insert_product_rows(writer,UNIT,wire_records(records[first:first+1024]))
    # Exact typed child readback precedes the coverage row.
    for first in range(0,len(records),1024):
        rows=_rows(reader,f'SELECT * FROM q_live.{UNIT.name} WHERE {condition} '
            f'AND ordinal>={first} AND ordinal<{min(first+1024,len(records))} ORDER BY ordinal LIMIT 1025',1025)
        expected=[dict(r,session_date=r['session_date'].isoformat(),row_count=str(r['row_count'])) for r in records[first:first+1024]]
        # UInt64 may be a wire string; only that transport representation varies.
        actual=[dict(r,row_count=str(r['row_count'])) for r in rows]
        if actual!=expected:
            raise ValueError('Feature source dependency readback differs')
    verify_sources(reader);ownership.assert_owner()
    insert_product_rows(writer,SEAL,wire_records([plan.seal()]))
    if read_feature_source_plan(reader,plan.plan_hash)!=plan:
        raise ValueError('Feature source plan acknowledgement differs')
    ownership.assert_owner()
    return 'published'
