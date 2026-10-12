"""One SEC source pass, normalized facts/provenance, verified publication."""
from __future__ import annotations

from uuid import uuid4
from . import schema,reference_sql
from .contracts import identifier,literal
from .reference_publication import ReferencePublisher
from .publication import evidence_sql

STAGING='research_stage_reference_v1'


def staging_table():
    facts=next(t for t in schema.TABLES if t.name=='research_facts_v1')
    proof=next(t for t in schema.TABLES if t.name=='research_provenance_v1')
    return schema.Table(STAGING,facts.columns+tuple(c for c in proof.columns if c.name!='evidence_hash'),
                        facts.order,facts.partition)


def run_sec(scope,cik,metrics,query,claim,source_guard,*,database='arte'):
    identifier(database);claim.check();source_guard()
    schema.verify_storage(query,database,require_tables=True)
    schema.verify_auxiliary_storage(query,database,(STAGING,))
    schema.verify_auxiliary_storage(query,'q_live',('sec_filing_v3','sec_xbrl_company_fact_v3'))
    schema.verify_auxiliary_storage(query,'sec_core',('sec_bulk_mirror_xbrl_fact_v3',))
    publisher=ReferencePublisher(query,database,claim)
    previous=publisher.receipt(scope)
    if previous:return previous
    if scope.entity_kind!=1 or scope.family!='fundamentals':raise ValueError('SEC scope must be issuer fundamentals')
    attempt=str(uuid4());stage=staging_table()
    # Controller installs this DDL during storage preflight, not during a read.
    where=scope.rows_where(attempt)
    projection=reference_sql.sec_projection(scope.dataset_id,attempt,scope.entity_key,cik,scope.start,scope.end,
                                           metrics=metrics,wide=True)
    query(f'INSERT INTO {database}.{STAGING} '+projection,read=False)
    fact_table=next(t for t in schema.TABLES if t.name=='research_facts_v1')
    names=','.join(c.name for c in fact_table.columns)
    normalized=f'SELECT {names} FROM {database}.{STAGING} WHERE {where}'
    expected={k:int(v) for k,v in query(evidence_sql(fact_table,normalized,
                'tuple(metric_key,unit_key,period_start,period_end,available_at,revision)'))[0].items()}
    if expected['n']!=expected['unique_keys']:raise ValueError('SEC normalization produced ambiguous fact keys')
    proof_columns='evidence_hash,source_system,source_record,source_hash,proof'
    proof=(f'SELECT DISTINCT {proof_columns} FROM {database}.{STAGING} WHERE {where}')
    # Deduplicate only identical evidence; conflicting contents fail on readback.
    claim.check();source_guard()
    query(f'INSERT INTO {database}.research_provenance_v1 ({proof_columns}) '
          f'SELECT p.* FROM ({proof}) p LEFT ANTI JOIN {database}.research_provenance_v1 existing '
          'ON existing.evidence_hash=p.evidence_hash',read=False)
    mismatch=query(f'SELECT count() bad FROM ({proof}) p LEFT JOIN {database}.research_provenance_v1 e '
                   'ON p.evidence_hash=e.evidence_hash WHERE e.source_system!=p.source_system OR '
                   'e.source_record!=p.source_record OR e.source_hash!=p.source_hash OR e.proof!=p.proof')
    if int(mismatch[0]['bad']):raise ValueError('SEC provenance conflicts with existing evidence')
    duplicates=query(f'SELECT e.evidence_hash FROM {database}.research_provenance_v1 e '
                     f'WHERE evidence_hash IN (SELECT evidence_hash FROM ({proof})) '
                     'GROUP BY evidence_hash HAVING count()!=1 LIMIT 1')
    if duplicates:raise ValueError('SEC provenance is missing or duplicated')
    query(f'INSERT INTO {database}.research_facts_v1 ({names}) '+normalized,read=False)
    source_guard();claim.check()
    return publisher.publish(scope,attempt,expected)
