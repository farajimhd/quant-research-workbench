"""One SEC source pass, normalized facts/provenance, verified publication."""
from __future__ import annotations

from uuid import uuid4
from . import schema,reference_sql,short_sql,fundamentals
from .contracts import identifier,literal,digest
from .reference_publication import ReferencePublisher
from .publication import evidence_sql
from .facts import FACT_KEY,UNITS

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
    if scope.entity_kind!=1 or scope.family!='fundamentals':raise ValueError('SEC scope must be issuer fundamentals')
    return _materialize(scope,query,claim,source_guard,database,
        lambda attempt:reference_sql.sec_projection(scope.dataset_id,attempt,scope.entity_key,cik,scope.start,scope.end,
                                                   metrics=metrics,wide=True))


def run_short(scope,query,claim,source_guard,*,database='arte',source_database='q_live'):
    identifier(database);claim.check();source_guard()
    if scope.entity_kind!=2 or scope.family not in ('short_volume','short_interest'):
        raise ValueError('Short scope must be security short_volume or short_interest')
    if source_database!='q_live' and database!=source_database:
        raise ValueError('Fixture source must be the output isolated namespace')
    schema.verify_storage(query,database,require_tables=True)
    schema.verify_auxiliary_storage(query,database,(STAGING,))
    schema.verify_auxiliary_storage(query,source_database,('market_'+scope.family+'_v1',))
    audit=query(short_sql.short_audit(database,scope.dataset_id,scope.entity_key,scope.start,scope.end,
                                    scope.family,source_database=source_database))[0]
    if (int(audit['n'])!=int(audit['unique_events']) or any(int(audit[k]) for k in
        ('unknown_public_time','invalid_public_time','missing_evidence'))):
        raise ValueError('Short source is not certifiable: '+str(audit))
    # The immutable scope is bound to the measured values and original clocks.
    if scope.parent_hash!=audit['source_hash']:
        raise ValueError('Short source differs from the frozen audited dependency')
    return _materialize(scope,query,claim,source_guard,database,
        lambda attempt:short_sql.short_projection(database,scope.dataset_id,attempt,scope.entity_key,
            scope.start,scope.end,scope.family,source_database=source_database,wide=True))


def derived_parent_hash(receipt):
    return digest(dict(fundamentals=receipt['output_hash'],formula_contract=fundamentals.contract_hash()))


def run_derived(scope,parent_scope,query,claim,source_guard,*,database='arte'):
    claim.check();source_guard();schema.verify_storage(query,database,require_tables=True)
    schema.verify_auxiliary_storage(query,database,(STAGING,))
    if (scope.entity_kind!=1 or scope.family!='derived_fundamentals' or parent_scope.family!='fundamentals'
            or (scope.dataset_id,scope.entity_key)!=(parent_scope.dataset_id,parent_scope.entity_key)
            or parent_scope.start>scope.start or parent_scope.end<scope.end):
        raise ValueError('Invalid derived-to-reported fundamental dependency')
    parent=ReferencePublisher(query,database,claim).receipt(parent_scope)
    if parent is None:raise ValueError('Reported fundamental predecessor is not certified')
    if scope.parent_hash!=derived_parent_hash(parent):raise ValueError('Derived fundamental dependency changed')
    return _materialize(scope,query,claim,source_guard,database,
        lambda attempt:fundamentals.projection(database,scope.dataset_id,attempt,scope.entity_key,
            parent['attempt_id'],scope.start,scope.end,wide=True))


def _materialize(scope,query,claim,source_guard,database,projection_factory):
    publisher=ReferencePublisher(query,database,claim)
    previous=publisher.receipt(scope)
    if previous:return previous
    attempt=str(uuid4())
    # Controller installs this DDL during storage preflight, not during a read.
    where=scope.rows_where(attempt)
    projection=projection_factory(attempt)
    query(f'INSERT INTO {database}.{STAGING} '+projection,read=False)
    fact_table=next(t for t in schema.TABLES if t.name=='research_facts_v1')
    names=','.join(c.name for c in fact_table.columns)
    normalized=f'SELECT {names} FROM {database}.{STAGING} WHERE {where}'
    units=','.join(str(v) for v in sorted(set(UNITS.values())))
    invalid=query(f'SELECT countIf(status NOT IN (1,2,3) OR precision NOT IN (1,2) OR confidence>100 '
        f'OR unit_key NOT IN ({units}) OR basis_unit_key NOT IN ({units}) OR taxonomy_key NOT IN (0,1,2,3) '
        "OR (status=1 AND ifNull(isFinite(value),0)=0) OR (status!=1 AND (value IS NOT NULL OR empty(reason))) "
        f"OR NOT match(evidence_hash,'^[0-9a-f]{{64}}$')) bad FROM ({normalized})")
    if int(invalid[0]['bad']):raise ValueError('Reference producer emitted invalid value/unit/availability states')
    expected={k:int(v) for k,v in query(evidence_sql(fact_table,normalized,
                FACT_KEY))[0].items()}
    if expected['n']!=expected['unique_keys']:raise ValueError('Reference normalization produced ambiguous fact keys')
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
    if int(mismatch[0]['bad']):raise ValueError('Reference provenance conflicts with existing evidence')
    duplicates=query(f'SELECT e.evidence_hash FROM {database}.research_provenance_v1 e '
                     f'WHERE evidence_hash IN (SELECT evidence_hash FROM ({proof})) '
                     'GROUP BY evidence_hash HAVING count()!=1 LIMIT 1')
    if duplicates:raise ValueError('Reference provenance is missing or duplicated')
    query(f'INSERT INTO {database}.research_facts_v1 ({names}) '+normalized,read=False)
    source_guard();claim.check()
    return publisher.publish(scope,attempt,expected)
