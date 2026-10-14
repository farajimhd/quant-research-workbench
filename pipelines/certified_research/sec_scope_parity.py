"""Read-only original-document authority for one frozen fundamental scope."""
from datetime import date,datetime,timezone
import json
import re

from . import facts,reference_sql,sec_coverage,sec_document_lineage,sec_filing_census,schema
from . import sec_submission_coverage,sec_fundamental_readback,sec_reported_readback,sec_fundamental_reconciliation
from .contracts import digest,literal


def _utc(value):
    value=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def verify(query,scope,specification,claim,source_guard):
    """Require complete retained/source coverage, exact originals and safe clocks.

    A specification names existing immutable bindings; it cannot assert readiness.
    Candidate flags are never accepted as certification evidence.
    """
    spec=dict(specification);sealed=spec.pop('specification_hash',None)
    if sealed!=digest(spec) or spec.get('version')!='sec-original-scope-specification-v1':
        raise ValueError('Frozen original SEC scope specification fingerprint required')
    if (scope.family!='fundamentals' or scope.entity_kind!=1 or spec['entity_key']!=scope.entity_key
            or spec['start']!=str(scope.start) or spec['end']!=str(scope.end) or spec['source_hash']!=scope.parent_hash
            or scope.catalog_hash!=facts.catalog_hash()):
        raise ValueError('Original SEC scope specification differs from frozen publication')
    cik=str(int(spec['cik']));context_start=date.fromisoformat(spec['context_start'])
    if context_start>scope.start:raise ValueError('Original SEC context excludes the target start')
    bindings=spec['bindings']
    if not isinstance(bindings,(tuple,list)) or not 1<=len(bindings)<=1000:
        raise ValueError('Original SEC scope requires 1..1000 document bindings')
    names=spec['metrics'];selected=tuple(m for m in facts.catalog() if m.tags and m.name in names)
    if not selected or len(set(names))!=len(names) or set(names)!={m.name for m in selected}:
        raise ValueError('Original SEC scope metric selection differs from catalog')
    def guard():claim.check();source_guard();claim.check()
    guard()
    schema.verify_auxiliary_storage(query,'q_live',('sec_filing_v3','sec_xbrl_company_fact_v3'))
    schema.verify_auxiliary_storage(query,'sec_core',('sec_bulk_mirror_xbrl_fact_v3',))
    acquisition=sec_coverage.require_ready(query,context_start,scope.end)
    inventory=sec_submission_coverage.inspect(query,(cik,),context_start,scope.end,forms=tuple(spec['forms']))
    if inventory['status']!='retained_evidence_ready':raise ValueError('Original SEC scope has incomplete filing acquisition/submissions')
    census=sec_filing_census.inventory(query,(cik,),context_start,scope.end,forms=tuple(spec['forms']),maximum_documents=1000)
    expected={(r['accession'],r['primary_document']) for r in census['documents']}
    actual={(r['accession'],r['document']) for r in bindings}
    if len(actual)!=len(bindings) or actual!=expected:raise ValueError('Original SEC bindings do not cover the complete filing census')
    before=reference_sql.sec_source_evidence(query,scope.entity_key,cik,scope.start,scope.end,metrics=selected)
    if before['source_hash']!=scope.parent_hash:raise ValueError('Current SEC projection differs from frozen source')
    tags=tuple(sorted({tag for m in facts.catalog() for tag in m.tags}));verified=[];times={}
    for item in sorted(bindings,key=lambda r:(r['accession'],r['document'])):
        guard()
        if any(not re.fullmatch('[0-9a-f]{64}',item[key]) for key in ('reconciliation_hash','reported_binding_hash')):
            raise ValueError('Original SEC binding locators must be immutable hashes')
        lineage=sec_document_lineage.inspect(query,cik,item['accession'],item['document'],claim,source_guard)
        original=sec_fundamental_readback.verify(query,'q_live',item['reconciliation_hash'],claim,source_guard,include_extraction=True)
        extraction=original.pop('extraction')
        if (not original['parity_inputs_ready'] or not original['all_bulk_matched']
                or extraction['catalog_hash']!=facts.catalog_hash()
                or extraction['source_text_hash']!=lineage['document']['content_sha256']):
            raise ValueError('Original SEC document parity, catalog or current lineage is unqualified')
        current=sec_fundamental_reconciliation.compare_original(extraction,
            sec_fundamental_reconciliation.load_bulk(query,cik,item['accession'],tags))
        if not current['all_bulk_matched'] or current['unmatched_original_positions']:
            raise ValueError('Current bulk facts no longer have complete original parity')
        public=sec_reported_readback.verify(query,'q_live',item['reported_binding_hash'],claim,source_guard)
        if (public['source_reconciliation_hash']!=item['reconciliation_hash'] or not public['public_time_qualified']
                or public['extraction_hash']!=original['extraction_hash']):
            raise ValueError('Original SEC public-time binding differs from document parity')
        times[item['accession']]=_utc(public['available_at'])
        verified.append((item['accession'],item['document'],original['reconciliation_hash'],public['evidence_hash'],lineage['candidate_hash']))
    namespace='00000000-0000-0000-0000-000000000001'
    projection=reference_sql.sec_projection(namespace,namespace,scope.entity_key,cik,scope.start,scope.end,metrics=selected,provenance=True)
    size=query('SELECT count() n,sum(length(source_record)+length(proof)) bytes FROM ('+projection+')')
    if len(size)!=1 or not 0<int(size[0]['n'])<=10000 or int(size[0]['bytes'])>16*1024**2:
        raise ValueError('Original SEC projected provenance exceeds row/byte bounds')
    rows=query('SELECT source_record,proof FROM ('+projection+') LIMIT 10001')
    if len(rows)!=int(size[0]['n']):raise ValueError('Original SEC projected provenance changed during read')
    for row in rows:
        proof=json.loads(row['proof'])
        if (not isinstance(proof,list) or len(proof)!=18 or proof[3] not in times
                or not row['source_record'].startswith('sec_core.sec_bulk_mirror_xbrl_fact_v3/')
                or _utc(proof[16])<times[proof[3]]):
            raise ValueError('Projected SEC fact lacks original parity or precedes proven public availability')
    guard()
    if reference_sql.sec_source_evidence(query,scope.entity_key,cik,scope.start,scope.end,metrics=selected)!=before:
        raise ValueError('SEC source changed during original-scope verification')
    if sec_submission_coverage.inspect(query,(cik,),context_start,scope.end,forms=tuple(spec['forms']))!=inventory:
        raise ValueError('SEC filing authority changed during original-scope verification')
    if sec_coverage.require_ready(query,context_start,scope.end)!=acquisition:
        raise ValueError('SEC acquisition authority changed during original-scope verification')
    guard()
    result=dict(version='sec-original-scope-parity-v1',certified=True,all_bulk_matched=True,original_vintage_qualified=True,
        source_hash=scope.parent_hash,specification_hash=sealed,documents=tuple(verified),
        acquisition_hash=acquisition['coverage_hash'],inspection_hash=inventory['inspection_hash'],allocation_certified=False)
    return dict(result,evidence_hash=digest(result))


class Guard:
    """Application verifier bound to immutable metadata and current CH evidence."""
    def __init__(self,query,specifications,claim,source_guard):
        if not isinstance(specifications,(tuple,list)) or not 1<=len(specifications)<=256:
            raise ValueError('Original SEC verifier requires 1..256 frozen scope specifications')
        self.specifications={}
        for original in specifications:
            spec=json.loads(json.dumps(original))
            body=dict(spec);sealed=body.pop('specification_hash',None)
            if sealed!=digest(body):raise ValueError('Original SEC verifier specification fingerprint differs')
            key=(spec['entity_key'],spec['start'],spec['end'],spec['source_hash'])
            if key in self.specifications:raise ValueError('Competing original SEC scope specifications')
            self.specifications[key]=spec
        self.query=query;self.claim=claim;self.source_guard=source_guard

    def __call__(self,scope):
        key=(scope.entity_key,str(scope.start),str(scope.end),scope.parent_hash)
        if key not in self.specifications:raise ValueError('Original SEC scope has no frozen document specification')
        return verify(self.query,scope,self.specifications[key],self.claim,self.source_guard)
