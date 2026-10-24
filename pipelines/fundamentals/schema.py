"""Normalized retained observations; table presence never implies certification."""
from pipelines.certified_research.contracts import identifier, literal, digest
POLICY = 'live_market_ssd'
VERSION = 'retained-reference-v1'
TIME = "DateTime64(9,'UTC')"
SOURCE = (('source_id','FixedString(64)'), ('family','LowCardinality(String)'),
    ('entity_kind','LowCardinality(String)'), ('entity_id','String'), ('event_key','String'),
    ('source_table','String'), ('source_hash','String'), ('evidence_ref','String'),
    ('available_at',f'Nullable({TIME})'), ('observed_at',TIME),
    ('quality','LowCardinality(String)'), ('reason','LowCardinality(String)'), ('recipe_hash','FixedString(64)'))
CONTEXT = (('context_id','FixedString(64)'), ('period_start','Nullable(Date)'),
    ('period_end','Nullable(Date)'), ('taxonomy','LowCardinality(String)'),
    ('dimensions','String'), ('unit','LowCardinality(String)'))
FACT = (('entity_kind','LowCardinality(String)'), ('entity_id','String'),
    ('metric','LowCardinality(String)'), ('ordering_time',TIME),
    ('source_id','FixedString(64)'), ('context_id','FixedString(64)'),
    ('value','Nullable(Float64)'), ('text_value','Nullable(String)'))
STAGE = (('batch_key','FixedString(64)'),) + SOURCE + CONTEXT + (
    ('metric','String'), ('value','Nullable(Float64)'), ('text_value','Nullable(String)'))
RECEIPT = (('batch_key','FixedString(64)'), ('source_signature','FixedString(64)'),
    ('recipe_hash','FixedString(64)'), ('population_hash','FixedString(64)'),
    ('source_rows','UInt64'), ('fact_rows','UInt64'), ('output_hash','FixedString(64)'), ('completed_at',TIME))
METRIC = (('metric','String'),('issuer_cik','String'),('label','String'),
    ('statement_scope','String'),('definition_hash','FixedString(64)'))
PERIOD = (('company_fact_id','String'),('source_hash','String'),('period_start','Date'),
    ('frame','String'),('method','LowCardinality(String)'),('completed_at',"DateTime64(6,'UTC')"))
DEFINITIONS = {
    'reference_period_evidence_v1': (PERIOD, 'company_fact_id,source_hash', 'tuple()'),
    'reference_metric_definition_v1': (METRIC, 'metric', 'tuple()'),
    'reference_context_v1': (CONTEXT, 'context_id', 'tuple()'),
    'reference_source_v1': (SOURCE, 'family,entity_kind,entity_id,source_id', 'tuple()'),
    'reference_fact_v1': (FACT, 'entity_kind,entity_id,metric,ordering_time,source_id,context_id', 'toYYYYMM(ordering_time)'),
    'reference_batch_receipt_v1': (RECEIPT, 'batch_key,source_signature,recipe_hash,population_hash', 'tuple()'),
    'reference_stage_v1': (STAGE, 'batch_key,source_id,metric,context_id', 'batch_key'),
}


def ddl(database='arte'):
    identifier(database)
    return tuple(f'CREATE TABLE IF NOT EXISTS {database}.{name} ('
        + ','.join(f'{n} {t}' for n,t in columns) + ') ENGINE=ReplacingMergeTree '
        + f'ORDER BY ({order}) PARTITION BY {partition} '
        + f"SETTINGS storage_policy='{POLICY}',index_granularity=1024"
        for name,(columns,order,partition) in DEFINITIONS.items())


def verify(query, database='arte', *, required=True):
    identifier(database)
    routes = query(f"SELECT arrayJoin(disks) disk FROM system.storage_policies WHERE policy_name='{POLICY}'")
    if not routes or any(x['disk'] != POLICY for x in routes):
        raise ValueError('Required SSD policy is absent or routes elsewhere')
    names = ','.join(map(literal, DEFINITIONS))
    tables = query(f'SELECT name,engine,sorting_key,partition_key,storage_policy FROM system.tables '
        f'WHERE database={literal(database)} AND name IN ({names})')
    if required and {x['name'] for x in tables} != set(DEFINITIONS):
        raise ValueError('Normalized reference tables are missing')
    compact = lambda x: x.replace(' ', '').replace('`', '')
    columns = query(f'SELECT table,name,type FROM system.columns WHERE database={literal(database)} '
        f'AND table IN ({names}) ORDER BY table,position')
    for row in tables:
        expected, order, partition = DEFINITIONS[row['name']]
        actual = [(x['name'],compact(x['type'])) for x in columns if x['table']==row['name']]
        if (row['engine'] != 'ReplacingMergeTree' or row['storage_policy'] != POLICY
                or compact(row['sorting_key']) != compact(order)
                or compact(row['partition_key']) != compact(partition)
                or actual != [(n,compact(t)) for n,t in expected]):
            raise ValueError(f"Existing {row['name']} layout differs; explicit migration required")
    parts = query(f'SELECT DISTINCT disk_name FROM system.parts WHERE active '
        f'AND database={literal(database)} AND table IN ({names})')
    if any(x['disk_name'] != POLICY for x in parts):
        raise ValueError('Reference parts are outside the required SSD')
    return digest(DEFINITIONS)


def install(query, database='arte'):
    verify(query, database, required=False)
    for sql in ddl(database):
        query(sql, read=False)
    verify(query, database)
    from .joint_issuers import install as install_joint_issuers
    install_joint_issuers(query,database)
