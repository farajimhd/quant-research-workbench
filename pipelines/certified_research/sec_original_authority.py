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
    if (checked['readback_verified'] is not True or checked['public_time_qualified'] is not True
            or checked['binding_hash']!=binding_hash or checked['document_key']!=key
            or checked['source_document_hash']!=before['document']['content_sha256']
            or extraction['source_text_hash']!=before['document']['content_sha256']
            or extraction['catalog_hash']!=facts.catalog_hash()):
        raise ValueError('Original filing authority differs from current document/time/catalog binding')
    if extraction['rejected'] or extraction['security_cover']['rejected']:
        raise ValueError('Original filing contains unresolved numeric or identity-cover observations')
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
    return dict(result,authority_hash=digest(result))
