"""Versioned original-filing authority; bulk equivalence is diagnostic only.

This does not change the legacy bulk publisher or certify complete issuer
history. Every document still needs current exact lineage, normalized readback,
qualified public time and unambiguous original numeric contexts.
"""
from . import facts,sec_document_lineage,sec_reported_readback,sec_precision
from . import sec_share_source,sec_share_context
from .contracts import digest
from pipelines.fundamentals.eligibility import require_eligible

VERSION='qualified-original-filing-authority-v1'


def verify_document(query,database,cik,accession,document,binding_hash,claim,source_guard):
    require_eligible('issuer_cik',[str(cik).zfill(10)])
    def guard():
        claim.check();source_guard();claim.check()
    guard()
    before=sec_document_lineage.inspect(query,cik,accession,document,claim,source_guard)
    checked=sec_reported_readback.verify(query,database,binding_hash,claim,source_guard,include_source=True)
    extraction=checked['extraction']
    key=digest(dict(version=sec_share_source.VERSION,candidate_hash=before['candidate_hash'],parser=sec_share_context.VERSION))
    witness=checked.get('source_witness',{})
    archive_original=witness.get('version')=='sec-archive-original-document-witness-v3'
    retained_hash=witness.get('retained_document_hash') if archive_original else checked['source_document_hash']
    if archive_original and (witness.get('candidate_hash')!=before['candidate_hash']
            or witness.get('archive_version_key')!=before['archive_version_key']
            or str(int(witness['cik']))!=str(int(cik)) or witness.get('accession')!=accession
            or witness.get('document_name')!=document):
        raise ValueError('Archive-original authority differs from current issuer/transport lineage')
    if (checked['readback_verified'] is not True or checked['public_time_qualified'] is not True
            or checked['binding_hash']!=binding_hash or checked['document_key']!=key
            or retained_hash!=before['document']['content_sha256']
            or extraction['source_text_hash']!=checked['source_document_hash']
            or extraction['catalog_hash']!=facts.catalog_hash()):
        raise ValueError('Original filing authority differs from current document/time/catalog binding')
    nils=tuple(r for r in extraction['rejected'] if r['reason']=='nil_share_fact'
        and extraction.get('version')=='sec-inline-fundamental-context-v4')
    if len(extraction['rejected'])!=len(nils) or extraction['security_cover']['rejected']:
        raise ValueError('Original filing contains unresolved numeric or identity-cover observations')
    if extraction.get('version')=='sec-reviewed-html-context-v1':
        from .sec_html_context import verify_registered
        verify_registered(extraction,cik)
    projection=sec_precision.select_reported(extraction,mode=checked['numeric_mode'])
    if projection['projection_hash']!=checked['projection_hash'] or len(projection['groups'])!=checked['groups']:
        raise ValueError('Original filing authority differs from verified reported projection')
    after=sec_document_lineage.inspect(query,cik,accession,document,claim,source_guard)
    guard()
    if before!=after:raise ValueError('Original filing lineage changed during authority verification')
    result=dict(version=VERSION,cik=str(int(cik)).zfill(10),accession=accession,document=document,
        binding_hash=binding_hash,document_key=key,document_hash=checked['source_document_hash'],
        lineage_hash=before['candidate_hash'],extraction_hash=extraction['extraction_hash'],
        projection_hash=projection['projection_hash'],source_reconciliation_hash=checked['source_reconciliation_hash'],
        readback_evidence_hash=checked['evidence_hash'],available_at=checked['available_at'],
        precision=checked['precision'],numeric_mode=checked['numeric_mode'],catalog_hash=extraction['catalog_hash'],
        groups=projection['groups'],reconciliation=checked['reconciliation'],
        document_qualified=True,whole_scope_certified=False,aggregation='none')
    if nils:result['declared_missing_observations']=nils
    if archive_original:result.update(version='qualified-archive-original-filing-authority-v2',retained_document_hash=retained_hash)
    if extraction.get('version')=='sec-reviewed-html-context-v1':
        result.update(document_qualified=False,observations_qualified=True,
            qualification_scope='reviewed_html_observations',whole_document_coverage=False,
            html_plan_hash=extraction['html_plan']['plan_hash'])
    return dict(result,authority_hash=digest(result))
