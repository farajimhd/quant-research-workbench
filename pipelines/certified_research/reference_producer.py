"""One SEC source pass, normalized facts/provenance, verified publication."""
from __future__ import annotations

from uuid import uuid4
from . import schema,reference_sql,short_sql,fundamentals,float_data,price_inputs
from .contracts import identifier,literal,digest
from .reference_publication import ReferencePublisher
from .publication import evidence_sql
from .publication import Publisher
from .facts import FACT_KEY,UNITS

STAGING='research_stage_reference_v1'


def staging_table():
    facts=next(t for t in schema.TABLES if t.name=='research_facts_v1')
    proof=next(t for t in schema.TABLES if t.name=='research_provenance_v1')
    return schema.Table(STAGING,facts.columns+tuple(c for c in proof.columns if c.name!='evidence_hash'),
                        facts.order,facts.partition)


def run_sec(scope,cik,metrics,query,claim,source_guard,*,database='arte',expected_source_hash=None,original_parity_guard=None):
    # Isolated fixtures exercise SQL contracts; production requires independent
    # original-document coverage/value authority before any operational writes.
    if not database.startswith('certified_research_test_'):
        if not callable(original_parity_guard):
            raise ValueError('Production SEC fundamentals require certified original-document parity coverage')
        base_guard=source_guard
        def source_guard():
            base_guard();evidence=original_parity_guard(scope)
            if (not isinstance(evidence,dict) or evidence.get('certified') is not True
                    or evidence.get('all_bulk_matched') is not True
                    or evidence.get('original_vintage_qualified') is not True
                    or evidence.get('source_hash')!=scope.parent_hash):
                raise ValueError('SEC original-document parity coverage differs from frozen source authority')
    identifier(database);claim.check();source_guard()
    schema.verify_storage(query,database,require_tables=True)
    schema.verify_auxiliary_storage(query,database,(STAGING,))
    schema.verify_auxiliary_storage(query,'q_live',('sec_filing_v3','sec_xbrl_company_fact_v3'))
    schema.verify_auxiliary_storage(query,'sec_core',('sec_bulk_mirror_xbrl_fact_v3',))
    from . import sec_coverage
    sec_coverage.require_ready(query,scope.start,scope.end)
    if scope.entity_kind!=1 or scope.family!='fundamentals':raise ValueError('SEC scope must be issuer fundamentals')
    if expected_source_hash is not None and expected_source_hash!=scope.parent_hash:
        raise ValueError('SEC input fingerprint differs from publication lineage')
    def check_stage(attempt):
        namespace='00000000-0000-0000-0000-000000000001'
        projection=(f'SELECT * REPLACE(toUUID({literal(namespace)}) AS dataset_id,'
            f'toUUID({literal(namespace)}) AS attempt_id) FROM {database}.{STAGING} AS s '
            f'WHERE s.dataset_id=toUUID({literal(scope.dataset_id)}) AND s.attempt_id=toUUID({literal(attempt)}) '
            f'AND s.entity_kind={scope.entity_kind} AND s.entity_key={scope.entity_key}')
        rows=query(evidence_sql(staging_table(),projection,FACT_KEY))
        if len(rows)!=1:raise ValueError('Materialized SEC input evidence is ambiguous')
        binding=reference_sql.sec_source_binding(rows[0],scope.entity_key,cik,scope.start,scope.end,metrics)
        if binding['source_hash']!=expected_source_hash:raise ValueError('SEC input changed before materialized publication')
    return _materialize(scope,query,claim,source_guard,database,
        lambda attempt:reference_sql.sec_projection(scope.dataset_id,attempt,scope.entity_key,cik,scope.start,scope.end,
                                                   metrics=metrics,wide=True),stage_check=check_stage if expected_source_hash is not None else None)


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
    def check_stage(attempt):
        rows=query(short_sql.staged_input_audit(database,scope.rows_where(attempt),scope.family))
        if len(rows)!=1:raise ValueError('Materialized short source evidence is ambiguous')
        staged=rows[0];metrics=5 if scope.family=='short_volume' else 3
        if (staged['source_hash']!=scope.parent_hash or int(staged['n'])!=int(audit['n'])
                or int(staged['unique_events'])!=int(staged['n']) or int(staged['fact_rows'])!=int(staged['n'])*metrics):
            raise ValueError('Short input changed before materialized publication')
    return _materialize(scope,query,claim,source_guard,database,
        lambda attempt:short_sql.short_projection(database,scope.dataset_id,attempt,scope.entity_key,
            scope.start,scope.end,scope.family,source_database=source_database,wide=True,preserve_measurements=True),
        stage_check=check_stage,publication_filter=f"toDate(available_at,'UTC') BETWEEN toDate({literal(scope.start)}) AND toDate({literal(scope.end)})")


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


def run_float(scope,issuer_key,parent_scopes,query,claim,source_guard,*,database='arte'):
    claim.check();source_guard();schema.verify_storage(query,database,require_tables=True)
    schema.verify_auxiliary_storage(query,database,(STAGING,float_data.STAGING))
    if scope.entity_kind!=2 or scope.family!='float_supply':raise ValueError('Float scope must be security supply')
    publisher=ReferencePublisher(query,database,claim);parents={}
    for family,parent_scope in parent_scopes.items():
        if (family!=parent_scope.family or parent_scope.dataset_id!=scope.dataset_id
                or parent_scope.start>scope.start or parent_scope.end<scope.end
                or (parent_scope.entity_kind,parent_scope.entity_key)!=(
                    (1,issuer_key) if family=='fundamentals' else (2,scope.entity_key))):
            raise ValueError('Float parent identity/range does not match the requested security')
        receipt=publisher.receipt(parent_scope)
        if receipt is None:raise ValueError('Required float parent is not certified: '+family)
        parents[family]=receipt
    if scope.parent_hash!=float_data.parent_hash(parents):raise ValueError('Float parent dependencies changed')
    corporate=parents['corporate_actions']['attempt_id']
    bad=query(f'SELECT countIf(period_end IS NULL OR unit_key!=3 OR basis_unit_key!=3) bad '
        f'FROM {database}.research_facts_v1 WHERE dataset_id=toUUID({literal(scope.dataset_id)}) '
        f'AND attempt_id=toUUID({literal(corporate)}) AND metric_key IN '
        f'({float_data.key("corporate.executed_split_ratio")},{float_data.key("corporate.announced_split_ratio")})')
    if int(bad[0]['bad']):raise ValueError('Corporate parent lacks complete execution-date/ratio evidence')
    def projection(attempt):
        claim.check();source_guard()
        query(f'INSERT INTO {database}.{float_data.STAGING} '+float_data.projection(database,scope.dataset_id,attempt,
            scope.entity_key,issuer_key,parents,scope.start,scope.end,prepare_context=True),read=False)
        claim.check();source_guard()
        return float_data.projection(database,scope.dataset_id,attempt,scope.entity_key,issuer_key,
            parents,scope.start,scope.end,wide=True,context_attempt=attempt)
    return _materialize(scope,query,claim,source_guard,database,projection)


def run_regular_prices(scope,bar_scopes,query,claim,source_guard,*,database='arte'):
    claim.check();source_guard();schema.verify_storage(query,database,require_tables=True)
    schema.verify_auxiliary_storage(query,database,(STAGING,))
    if scope.entity_kind!=2 or scope.family!='regular_prices':raise ValueError('Price facts require a security scope')
    parents=regular_price_parents(scope,bar_scopes,query,claim,database=database)
    return _materialize(scope,query,claim,source_guard,database,
        lambda attempt:price_inputs.projection(database,scope.dataset_id,attempt,scope.entity_key,parents,wide=True))


def regular_price_parents(scope,bar_scopes,query,claim,*,database='arte'):
    """Reverify market authority and dated membership on production AND resume."""
    if (scope.entity_kind!=2 or scope.family!='regular_prices' or not 1<=len(bar_scopes)<=1000
            or len({p.session_date for p in bar_scopes})!=len(bar_scopes)):
        raise ValueError('Regular prices require one bounded bar parent per session')
    publisher=Publisher(query,database,claim);parents=[]
    for parent_scope in sorted(bar_scopes,key=lambda p:p.session_date):
        if (parent_scope.dataset_id!=scope.dataset_id or parent_scope.product!='bars'
                or not scope.start<=parent_scope.session_date<=scope.end):
            raise ValueError('Regular-price bar parent range/identity differs')
        receipt=publisher.receipt(parent_scope)
        if receipt is None:raise ValueError('Regular-price bars are not certified')
        start=price_inputs.bounds(parent_scope.session_date,'09:30:00')
        end=int(price_inputs.calendar_close(parent_scope.session_date).timestamp()*1_000_000)
        binding=query(f'SELECT count() n FROM {database}.research_membership_v1 WHERE dataset_id=toUUID({literal(scope.dataset_id)}) '
            f'AND security_key={scope.entity_key} AND listing_key={parent_scope.listing_key} AND eligible=1 '
            f'AND toUnixTimestamp64Micro(valid_from)<={start} AND toUnixTimestamp64Micro(valid_to)>={end}')
        if int(binding[0]['n'])!=1:raise ValueError('Regular-price listing/security membership is absent or ambiguous')
        parents.append((parent_scope,receipt))
    if scope.parent_hash!=price_inputs.parent_hash(parents):raise ValueError('Regular-price source/calendar dependency changed')
    return parents


def _materialize(scope,query,claim,source_guard,database,projection_factory,*,stage_check=None,publication_filter=None):
    def guard():
        claim.check();source_guard();claim.check()
    raw_query=query
    def query(sql,*,read=True):
        # Includes the receipt INSERT performed by ReferencePublisher: an
        # audit query or source check may outlive ownership of this attempt.
        if not read:guard()
        return raw_query(sql,read=read)
    guard()
    publisher=ReferencePublisher(query,database,claim)
    previous=publisher.receipt(scope)
    if previous:
        guard();return previous
    attempt=str(uuid4())
    # Controller installs this DDL during storage preflight, not during a read.
    where=scope.rows_where(attempt)
    projection=projection_factory(attempt)
    query(f'INSERT INTO {database}.{STAGING} '+projection,read=False)
    if stage_check is not None:stage_check(attempt)
    if publication_filter is not None:where+=' AND '+publication_filter
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
    guard()
    return publisher.publish(scope,attempt,expected)
