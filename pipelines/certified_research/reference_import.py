"""Restart-safe server-side import of an existing qualified reference scope."""
from datetime import date
from uuid import UUID,uuid5,NAMESPACE_URL

from . import facts,schema
from .contracts import digest,literal
from .publication import evidence_sql
from .reference_publication import ReferenceScope,ReferencePublisher

FAMILIES=('corporate_actions','float_allocation','reported_supply')
VERSION='qualified-reference-import-v1'


def source_scope(row):
    if row['family'] not in FAMILIES or int(row['entity_kind'])!=2:
        raise ValueError('Only qualified security reference authorities can be imported')
    return ReferenceScope(str(row['dataset_id']),2,int(row['entity_key']),row['family'],
        date.fromisoformat(str(row['range_start'])),date.fromisoformat(str(row['range_end'])),row['parent_hash'],facts.catalog_hash())


def target_scope(dataset_id,row):
    UUID(dataset_id);source=source_scope(row)
    if source.dataset_id==dataset_id:raise ValueError('Reference import requires a different source dataset')
    parent=digest(dict(version=VERSION,source_dataset=source.dataset_id,entity_key=source.entity_key,
        family=source.family,start=source.start,end=source.end,source_parent=source.parent_hash,
        source_attempt=str(row['attempt_id']),source_output=row['output_hash'],catalog=source.catalog_hash))
    return ReferenceScope(dataset_id,2,source.entity_key,source.family,source.start,source.end,parent,source.catalog_hash)


def run(query,database,dataset_id,row,claim,source_guard,*,write=True):
    source=source_scope(row);target=target_scope(dataset_id,row)
    table=next(t for t in schema.TABLES if t.name=='research_facts_v1')
    attempt=str(uuid5(NAMESPACE_URL,digest(dict(dataset=dataset_id,parent=target.parent_hash))))
    def guard():claim.check();source_guard()
    guard();schema.verify_storage(query,database,require_tables=True)
    publisher=ReferencePublisher(query,database,claim)
    if publisher.receipt(source)!=row:raise ValueError('Reference import source differs from its qualified receipt')
    source_where=source.rows_where(str(row['attempt_id']))
    invalid=query('SELECT countIf(status NOT IN (1,2,3) OR precision NOT IN (1,2) OR confidence>100 '
        'OR (status=1 AND ifNull(isFinite(value),0)=0) OR (status!=1 AND (value IS NOT NULL OR empty(reason)))) bad '
        f'FROM {database}.research_facts_v1 WHERE {source_where}')[0]['bad']
    if int(invalid):raise ValueError('Reference import source contains invalid normalized fact states')
    # A certified fact fingerprint is insufficient if its original proof is
    # missing, duplicated or changed. Provenance is shared in this database.
    bad=query(f'SELECT f.evidence_hash FROM (SELECT DISTINCT evidence_hash FROM {database}.research_facts_v1 WHERE {source_where}) f '
        f'LEFT JOIN {database}.research_provenance_v1 p ON p.evidence_hash=f.evidence_hash '
        'GROUP BY f.evidence_hash HAVING count()!=1 OR countIf(p.evidence_hash!=f.evidence_hash '
        "OR lower(hex(SHA256(p.proof)))!=f.evidence_hash)>0 LIMIT 1")
    if bad:raise ValueError('Reference import source provenance is missing, duplicated or invalid')
    projected=(f'SELECT * REPLACE(toUUID({literal(dataset_id)}) AS dataset_id,toUUID({literal(attempt)}) AS attempt_id) '
        f'FROM (SELECT * FROM {database}.research_facts_v1 WHERE {source_where}) source_rows')
    expected={k:int(v) for k,v in query(evidence_sql(table,projected,facts.FACT_KEY))[0].items()}
    old=publisher.receipt(target)
    if old:
        if old['attempt_id']!=attempt or publisher.evidence(target,attempt)!=expected:
            raise ValueError('Reference import target differs from its source projection')
        guard();return old
    if not write:return None
    existing=f'SELECT * FROM {database}.research_facts_v1 WHERE '+target.rows_where(attempt)
    extra=query(f'SELECT count() n FROM ({existing} EXCEPT ALL {projected})')[0]['n']
    if int(extra):raise ValueError('Partial reference import contains changed or duplicate rows')
    columns=','.join(c.name for c in table.columns)
    row_hash='SHA256(toString(tuple('+columns+')))'
    guard()
    query(f'INSERT INTO {database}.research_facts_v1 ({columns}) SELECT '+','.join('s.'+c.name for c in table.columns)+
        f' FROM (SELECT *,{row_hash} row_hash FROM ({projected})) s '
        f'LEFT ANTI JOIN (SELECT {row_hash} row_hash FROM ({existing})) e USING(row_hash)',read=False)
    guard()
    if publisher.receipt(source)!=row:raise ValueError('Reference import source changed before certification')
    actual=publisher.evidence(target,attempt)
    if actual!=expected:raise ValueError('Reference import differs from the expected normalized source')
    return publisher.publish(target,attempt,expected)
