"""Discover retained reference sources from certified dated identity metadata."""
from datetime import date
from threading import Event
from uuid import UUID
import re

from . import facts,planning,reference_sql,schema,sec_coverage,short_sql,universe
from .campaign import PlannedUnit
from .contracts import literal
from .execution import Unit,execute
from .identity import identity_key,ISSUER,SECURITY
from .reference_publication import ReferencePublisher,ReferenceScope


def discover(query,config,dataset_id,claim,client_factory,source_guard,snapshot,days,*,
             fundamental_start,short_start,universe_parent='universe',stop=None,maximum_entities=8192,progress=None):
    """No provider requests, data publication or invented missing authorities.

    Short source fingerprints use the qualified pre-publication intervals.
    Existing corporate/allocation receipts must belong to this dataset and
    independently verify. Missing receipts are explicit preparation requirements.
    """
    UUID(dataset_id);stop=Event() if stop is None else stop;days=tuple(days)
    if type(maximum_entities) is not int or not 1<=maximum_entities<=65536:
        raise ValueError('Invalid reference discovery entity bound')
    if any(type(d) is not date or d>config.start for d in (fundamental_start,short_start)):
        raise ValueError('Reference discovery context omits target start')
    def guard():
        if stop.is_set():raise InterruptedError('Reference discovery stopped')
        claim.check();source_guard()
    guard()
    if (type(snapshot.get('context_start')) is not date or type(snapshot.get('end')) is not date
            or snapshot['context_start']>short_start or snapshot['end']<config.end):
        raise ValueError('Reference discovery snapshot omits its historical context range')
    schema.verify_storage(query,config.database,require_tables=True)
    membership=universe.execution_membership(snapshot,days)
    if not membership['ready']:raise ValueError('Reference discovery requires admitted dated membership')
    target_days={d for d in days if config.start<=d<=config.end}
    owners={listing:security for security,listings in membership['security_listings'].items() for listing in listings}
    securities={owners[s.listing_key] for s in membership['sessions'] if s.day in target_days}
    security_issuers={k:membership['security_issuers'][k] for k in securities}
    issuers=set(security_issuers.values())
    if not securities or max(len(securities),len(issuers))>maximum_entities:
        raise ValueError('Reference discovery entity census is empty or exceeds its bound')
    end=planning.reference_coverage_end(config.end)
    sec_coverage.require_ready(query,fundamental_start,end)
    schema.verify_auxiliary_storage(query,'q_live',('id_issuer_identifier_v1','sec_filing_v3',
        'sec_xbrl_company_fact_v3','market_short_volume_v1','market_short_interest_v1',
        'market_reference_publication_coverage_v1'))
    schema.verify_auxiliary_storage(query,'sec_core',('sec_bulk_mirror_xbrl_fact_v3',))
    identities={identity_key(ISSUER,i.issuer_id):i.issuer_id for i in snapshot['intervals'] if identity_key(ISSUER,i.issuer_id) in issuers}
    ids=','.join(literal(v) for v in sorted(identities.values()))
    rows=query('SELECT issuer_id,arraySort(groupUniqArray(identifier_value_normalized)) ciks '
        f'FROM q_live.id_issuer_identifier_v1 FINAL WHERE lower(identifier_kind)=\'cik\' AND issuer_id IN ({ids}) GROUP BY issuer_id')
    by_issuer={}
    for row in rows:
        key=identity_key(ISSUER,row['issuer_id']);values=row['ciks']
        if key in by_issuer or key not in issuers or any(not re.fullmatch('0*[1-9][0-9]{0,9}',v) for v in values):
            raise ValueError('Reference issuer CIK evidence is invalid or duplicated')
        normalized={str(int(v)) for v in values}
        if len(normalized)!=1:raise ValueError('Reference issuer has competing CIKs')
        by_issuer[key]=normalized.pop()
    if set(by_issuer)!=issuers or len(set(by_issuer.values()))!=len(issuers):
        raise ValueError('Reference issuer CIK evidence is missing or shared by competing identities')
    coverage={family:short_sql.publication_coverage(query,short_start,end,family) for family in ('short_volume','short_interest')}
    if any(not c['ready'] for c in coverage.values()):raise ValueError('Short acquisition coverage omits required historical sessions')
    keys=','.join(map(str,sorted(securities)))
    candidates=query(f'SELECT * FROM {config.database}.research_reference_receipt_v1 WHERE dataset_id=toUUID({literal(dataset_id)}) '
        f"AND entity_kind=2 AND entity_key IN ({keys}) AND family IN ('corporate_actions','float_allocation','reported_supply') "
        f'AND range_start<=toDate({literal(fundamental_start)}) AND range_end>=toDate({literal(end)}) LIMIT {maximum_entities*6+1}')
    if len(candidates)>maximum_entities*6:raise ValueError('Reference authority metadata exceeds its bound')
    receipts={}
    for row in candidates:
        key=(int(row['entity_key']),row['family'])
        if key in receipts:raise ValueError('Reference authority candidates are ambiguous; explicit scope selection is required')
        if key[0] not in securities or key[1] not in ('corporate_actions','float_allocation','reported_supply'):
            raise ValueError('Reference authority discovery returned an unrelated scope')
        receipts[key]=row
    missing=tuple(dict(security_key=key,family=family,start=str(fundamental_start),end=str(end))
        for key in sorted(securities) for family in ('corporate_actions','float_allocation') if (key,family) not in receipts)
    if missing:
        guard()
        return dict(ready=False,issuers=(),securities=(),prerequisites=(),corporate_parents={},
            missing_authorities=missing,membership=membership)
    memberships=short_sql.snapshot_memberships(snapshot,securities)
    jobs=[Unit(f'issuer:{key}','sec',memory_bytes=config.query_memory_bytes) for key in sorted(issuers)]
    jobs.extend(Unit(f'{family}:{key}',family,memory_bytes=config.query_memory_bytes)
        for key in sorted(securities) for family in coverage)
    def run(unit,parents,signal):
        guard();client=client_factory();family,key=unit.key.split(':');key=int(key)
        try:
            if family=='issuer':
                result=reference_sql.sec_source_evidence(client.query,key,by_issuer[key],fundamental_start,end)
            else:
                rows=client.query(short_sql.short_audit(config.database,dataset_id,key,short_start,end,family,
                    membership_sql=memberships[key]))
                if len(rows)!=1:raise ValueError('Short discovery audit is ambiguous')
                result=rows[0]
                if int(result['n'])!=int(result['unique_events']) or any(int(result[k]) for k in
                    ('unknown_public_time','invalid_public_time','missing_evidence')):
                    raise ValueError('Short observations lack original publication evidence')
            guard();return result
        finally:client.close()
    def report(change):
        if progress is None:return
        value=dict(change,phase='reference-source-discovery',counts=dict(change['counts']))
        value['counts']['prepared']=value['counts'].pop('certified',0)+value['counts'].pop('reused',0)
        if value['status'] in ('certified','reused'):value['status']='prepared'
        progress(value)
    result=execute(tuple(jobs),{p:run for p in ('sec','short_volume','short_interest')},
        workers=config.workers,memory_bytes=config.global_memory_bytes,stop=stop,progress=report)
    if result['status']!='complete':
        if stop.is_set():raise InterruptedError('Reference discovery stopped')
        raise ValueError('Reference source discovery failed: '+str({k:str(result['errors'][k])[:256] for k in sorted(result['errors'])[:8]}))
    plans=[];issuer_inputs=[];security_inputs=[];corporate={}
    for key in sorted(issuers):
        evidence=result['results'][f'issuer:{key}'];unit=f'sec:{key}'
        plans.append(PlannedUnit(Unit(unit,'sec_acquisition',(universe_parent,),config.query_memory_bytes),
            dict(entity_key=key,cik=by_issuer[key],start=str(fundamental_start),end=str(end),source_hash=evidence['source_hash'])))
        issuer_inputs.append(planning.IssuerInput(key,by_issuer[key],evidence['source_hash'],(unit,)))
    for key in sorted(securities):
        short_parents={}
        for family,cov in coverage.items():
            unit=f'{family}:{key}:source';evidence=result['results'][f'{family}:{key}']
            plans.append(PlannedUnit(Unit(unit,family+'_acquisition',(universe_parent,),config.query_memory_bytes),
                dict(entity_key=key,start=str(short_start),end=str(end),source_hash=evidence['source_hash'],coverage_hash=cov['coverage_hash'])))
            short_parents[family]=(unit,)
        authorities={}
        for family in ('corporate_actions','float_allocation','reported_supply'):
            if (key,family) not in receipts:continue
            row=receipts[(key,family)];scope=ReferenceScope(dataset_id,2,key,family,date.fromisoformat(str(row['range_start'])),
                date.fromisoformat(str(row['range_end'])),row['parent_hash'],facts.catalog_hash())
            if ReferencePublisher(query,config.database,claim).receipt(scope)!=row:
                raise ValueError('Discovered reference authority differs from its persisted output')
            unit=f'{family}:{key}:authority';authorities[family]=unit
            plans.append(PlannedUnit(Unit(unit,family,(),config.query_memory_bytes),
                dict(entity_key=key,start=str(scope.start),end=str(scope.end),receipt=row)))
        if all(f in authorities for f in ('corporate_actions','float_allocation')):
            security_inputs.append(planning.SecurityInput(key,security_issuers[key],(),authorities['corporate_actions'],
                authorities['float_allocation'],result['results'][f'short_volume:{key}']['source_hash'],
                result['results'][f'short_interest:{key}']['source_hash'],short_parents['short_volume'],short_parents['short_interest'],
                reported_supply_parent=authorities.get('reported_supply')))
            for listing in membership['security_listings'][key]:corporate[listing]=authorities['corporate_actions']
    guard()
    return dict(ready=not missing,issuers=tuple(issuer_inputs),securities=tuple(security_inputs),prerequisites=tuple(plans),
        corporate_parents=corporate,missing_authorities=tuple(missing),membership=membership)
