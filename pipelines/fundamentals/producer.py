"""Incremental server-side publisher. Python transports only bounded metadata.

A batch is a source partition and a bounded entity cohort, not a ticker-day.
Publication headers are the commit boundary. Stage -> exact readback -> headers
-> receipt. Failure leaves no false completion; content IDs make retry idempotent.
"""
from dataclasses import dataclass, asdict
from datetime import date
import json
import time
from . import schema
from .adapters import ADAPTERS, projection, recipe_hash
from .eligibility import require_eligible
from pipelines.certified_research.contracts import digest, literal, identifier


def names(columns):
    return ','.join(n for n,_ in columns)


@dataclass(frozen=True)
class Batch:
    family: str
    partition_id: str
    entities: tuple[str, ...]
    start: date
    end: date
    population_hash: str
    scan_keys: tuple[str, ...] = ()

    @property
    def key(self):
        return digest(asdict(self))


def source_snapshot(query, family, partition_id=None):
    """Cheap part metadata; merges may invalidate a batch, never hide a change."""
    db, table = ADAPTERS[family].table.split('.')
    table_info = query(f'SELECT engine,uuid,create_table_query FROM system.tables '
        f'WHERE database={literal(db)} AND name={literal(table)}')
    if len(table_info)!=1 or 'MergeTree' not in table_info[0]['engine']:
        raise ValueError(f'{family}: source absent or not a versioned MergeTree')
    mutations = query(f'SELECT mutation_id FROM system.mutations WHERE database={literal(db)} '
        f'AND table={literal(table)} AND NOT is_done LIMIT 1')
    if mutations:
        raise ValueError(f'{family}: source mutation is active; retry after it drains')
    scope = '' if partition_id is None else f' AND partition_id={literal(partition_id)}'
    rows = query('SELECT partition_id,name,rows,bytes_on_disk,data_version,hash_of_all_files '
        f'FROM system.parts WHERE active AND database={literal(db)} AND table={literal(table)}{scope} '
        'ORDER BY partition_id,name')
    return rows, digest({'table':table_info,'parts':rows})


def plan(query, population, start, end, cohort_size=128, *, cache_dir=None, progress=lambda family:None):
    if not 1<=cohort_size<=256 or end<start:
        raise ValueError('Invalid range or cohort size')
    if not population or set(population)-set(ADAPTERS):
        raise ValueError('Explicit known source families required')
    pop_hash = digest(population)
    batches=[]
    for family, entities in sorted(population.items()):
        require_eligible(ADAPTERS[family].kind,entities)
        if not entities or len(entities)>100000 or any(not isinstance(x,str) or not x for x in entities):
            raise ValueError(f'{family}: nonempty reviewed entity IDs required')
        progress(family)
        _,signature = source_snapshot(query,family)
        cache_key=digest([family,sorted(set(entities)),start,end,recipe_hash(family),ADAPTERS[family].scan_key])
        cache=None if cache_dir is None else cache_dir/('admission-'+cache_key+'.json')
        saved=json.loads(cache.read_text()) if cache is not None and cache.exists() else None
        if saved and saved['signature']==signature and saved['hash']==digest(saved['rows']):
            occupied=saved['rows']
        else:
            a=ADAPTERS[family];ids=','.join(map(literal,sorted(set(entities))))
            # One narrow discovery pass, not a payload scan for every empty
            # Cartesian product. Older physical versions only over-admit; FINAL
            # in the writer remains the authoritative row selection.
            occupied=query(f'SELECT _partition_id partition_id,arraySort(groupUniqArray('
                f'tuple(toString({a.entity}),toString({a.scan_key})))) members FROM {a.table} '
                f'WHERE {a.entity} IN ({ids}) AND coalesce({a.day},toDate({a.observed})) '
                f'BETWEEN toDate({literal(start)}) AND toDate({literal(end)}) GROUP BY _partition_id ORDER BY _partition_id')
            if source_snapshot(query,family)[1]!=signature:
                raise ValueError(f'{family}: source changed during admission discovery; rerun plan')
            if cache is not None:
                tmp=cache.with_suffix('.tmp')
                tmp.write_text(json.dumps({'signature':signature,'rows':occupied,'hash':digest(occupied)}))
                tmp.replace(cache)
        for partition in occupied:
            by_entity={}
            for entity,key in partition['members']:
                by_entity.setdefault(entity,set()).add(key)
            ids=sorted(by_entity)
            for i in range(0,len(ids),cohort_size):
                chosen=tuple(ids[i:i+cohort_size])
                keys=tuple(sorted(set().union(*(by_entity[e] for e in chosen))))
                if len(keys)>4096:raise ValueError('Index-key fanout exceeds bounded batch size')
                batches.append(Batch(family,partition['partition_id'],chosen,start,end,pop_hash,keys))
    return batches


def _different(query, expected, actual):
    # Exact bidirectional comparison; hashes are receipt summaries, not equality.
    for left,right in ((expected,actual),(actual,expected)):
        if int(query(f'SELECT count() n FROM (({left}) EXCEPT DISTINCT ({right}))')[0]['n']):
            raise ValueError('Normalized output readback differs from staged source')


def publish(query, batch, database='arte', *, audit=False, progress=lambda stage:None):
    identifier(database)
    require_eligible(ADAPTERS[batch.family].kind,batch.entities)
    recipe=recipe_hash(batch.family)
    prefix=f'{database}.reference_'
    entity_filter=(f'entity_kind={literal(ADAPTERS[batch.family].kind)} AND '
        f'entity_id IN ({",".join(map(literal,batch.entities))})')
    stage=f'{prefix}stage_v1 FINAL WHERE batch_key={literal(batch.key)}'
    ids=f'SELECT DISTINCT source_id FROM {stage}'
    timings={}
    last=time.monotonic()
    current='source metadata'
    def step(label):
        nonlocal last,current
        now=time.monotonic()
        timings[current]=now-last
        last=now
        current=label
        progress(label)
    progress('source metadata')
    _,signature=source_snapshot(query,batch.family,batch.partition_id)
    source_signature=signature
    destinations=query(f'SELECT name,uuid FROM system.tables WHERE database={literal(database)} '
        "AND name IN ('reference_source_v1','reference_context_v1','reference_fact_v1') ORDER BY name")
    if len(destinations)!=3:raise ValueError('Destination tables are missing')
    signature=digest([signature,destinations])
    receipt_where=(f'batch_key={literal(batch.key)} AND source_signature={literal(signature)} '
        f'AND recipe_hash={literal(recipe)} AND population_hash={literal(batch.population_hash)}')
    receipts=query(f'SELECT source_rows,fact_rows,output_hash FROM {prefix}batch_receipt_v1 FINAL WHERE {receipt_where}')
    if receipts and not audit:
        # A crash after receipt insertion but before cleanup must not leak its
        # staging partition forever. This bounded key lookup reads no source.
        if int(query(f'SELECT count() n FROM {stage}')[0]['n']):
            query(f'ALTER TABLE {prefix}stage_v1 DROP PARTITION {literal(batch.key)}',read=False)
        return {'state':'unchanged','seconds':time.monotonic()-last,'rows':int(receipts[0]['fact_rows']),
                'qualification':'not certified','timings':{'metadata':time.monotonic()-last}}
    step('stage source partition')
    incompatible=query(f'SELECT count() n FROM {prefix}source_v1 FINAL WHERE '
        f'family={literal(batch.family)} AND {entity_filter} AND recipe_hash!={literal(recipe)}')
    if int(incompatible[0]['n']):
        raise ValueError('Stored observations use a different recipe; explicit migration required')
    query(f'ALTER TABLE {prefix}stage_v1 DROP PARTITION {literal(batch.key)}',read=False)
    projected=projection(batch.family,batch.entities,batch.start,batch.end,
        partition_id=batch.partition_id,scan_keys=batch.scan_keys)
    # SHA source identity includes the complete observation. Keep recipe identity
    # separate; semantic changes require an explicit versioned migration.
    committed='' if audit else (f' WHERE source_id NOT IN (SELECT source_id FROM {prefix}source_v1 FINAL '
        f'WHERE family={literal(batch.family)} AND entity_id IN ({",".join(map(literal,batch.entities))}))')
    expanded=f'''SELECT *, tupleElement(m,1) metric,tupleElement(m,2) value,
        tupleElement(m,3) text_value,tupleElement(m,4) unit,
        lower(hex(SHA256(toJSONString(tuple(period_start,period_end,taxonomy,dimensions,unit))))) context_id,
        {literal(recipe)} recipe_hash FROM ({projected}) ARRAY JOIN metrics AS m{committed}'''
    query(f'INSERT INTO {prefix}stage_v1 ({names(schema.STAGE)}) SELECT {literal(batch.key)},'
        f'{names(schema.SOURCE)},{names(schema.CONTEXT)},metric,value,text_value FROM ({expanded})',read=False)
    stats=query(f'''SELECT uniqExact(source_id) source_rows,count() fact_rows,
        countIf(empty(entity_id) OR empty(event_key) OR empty(metric) OR
          (isNotNull(value) AND NOT isFinite(value))) bad,
        toString(sumWithOverflow(cityHash64(source_id,metric,context_id,toJSONString(tuple(value,text_value))))) h
        FROM {stage}''')[0]
    if int(stats['bad']):
        raise ValueError(f"{batch.family}: {stats['bad']} invalid identities or nonfinite facts; stage retained")
    # A changed source is not stamped complete. No headers have been published.
    if source_snapshot(query,batch.family,batch.partition_id)[1]!=source_signature:
        raise ValueError('Source changed during staging; repeat this batch from retained receipts')
    step('publish facts and contexts')
    context_expected=f'SELECT DISTINCT {names(schema.CONTEXT)} FROM {stage}'
    fact_columns=names(schema.FACT)
    fact_expected=f'SELECT DISTINCT entity_kind,entity_id,metric,observed_at ordering_time,source_id,context_id,value,text_value FROM {stage}'
    if not audit:
        query(f'INSERT INTO {prefix}context_v1 ({names(schema.CONTEXT)}) {context_expected} '
            f'AND context_id NOT IN (SELECT context_id FROM {prefix}context_v1 '
            f'WHERE context_id IN (SELECT context_id FROM {stage}))',read=False)
        # Retry after an ambiguous insertion never removes completed records.
        query(f'INSERT INTO {prefix}fact_v1 ({fact_columns}) {fact_expected}',read=False)
    step('verify exact output')
    _different(query,context_expected,f'SELECT {names(schema.CONTEXT)} FROM {prefix}context_v1 FINAL '
        f'WHERE context_id IN (SELECT context_id FROM {stage})')
    _different(query,fact_expected,f'SELECT {fact_columns} FROM {prefix}fact_v1 FINAL WHERE {entity_filter} AND source_id IN ({ids})')
    source_expected=f'SELECT DISTINCT {names(schema.SOURCE)} FROM {stage}'
    if not audit:
        query(f'INSERT INTO {prefix}source_v1 ({names(schema.SOURCE)}) {source_expected}',read=False)
    _different(query,source_expected,f'SELECT {names(schema.SOURCE)} FROM {prefix}source_v1 FINAL WHERE '
        f'family={literal(batch.family)} AND {entity_filter} AND source_id IN ({ids})')
    step('commit receipt')
    output_hash=digest(stats)
    query(f'INSERT INTO {prefix}batch_receipt_v1 VALUES (' + ','.join((literal(batch.key),literal(signature),
        literal(recipe),literal(batch.population_hash),str(stats['source_rows']),str(stats['fact_rows']),
        literal(output_hash),"now64(6,'UTC')")) + ')',read=False)
    # Staging is disposable only after a verified receipt exists.
    query(f'ALTER TABLE {prefix}stage_v1 DROP PARTITION {literal(batch.key)}',read=False)
    step('done')
    return {'state':'audited' if audit else 'published','rows':int(stats['fact_rows']),
            'source_rows':int(stats['source_rows']),'timings':timings,'output_hash':output_hash,
            'seconds':sum(timings.values()),'qualification':'not certified'}
