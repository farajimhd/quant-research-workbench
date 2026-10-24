"""Same-submission XBRL resources with independently qualified source witnesses."""
from datetime import datetime, timezone
from .contracts import digest
from . import sec_document_lineage, sec_witness_publication


def validate(extraction, primary, available_at):
    hashes=extraction.get('resource_document_hashes',())
    proofs=extraction.get('resource_document_proofs',())
    if not hashes and not proofs:return ()
    if not 1<=len(hashes)<=4 or len(proofs)!=len(hashes) or len(set(hashes))!=len(hashes):
        raise ValueError('Resource document evidence is incomplete')
    def utc(value):
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        # ClickHouse readback timestamps are explicitly UTC by schema.
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    for fingerprint,proof in zip(hashes,proofs):
        witness=proof['witness'];qualification=proof['qualification']
        sec_witness_publication.records((witness,),(qualification,),proof['receipt'])
        if (witness['document_hash']!=fingerprint or witness['document_key']==primary['document_key']
                or any(witness[k]!=primary[k] for k in ('submission_hash','container_sha256','source_archive_date'))
                or utc(qualification['available_at'])>utc(available_at)):
            raise ValueError('Resource document crosses submission identity or public-time boundary')
    return proofs


def verify(query,database,extraction,primary,available_at,claim,source_guard):
    proofs=validate(extraction,primary,available_at)
    for proof in proofs:
        witness=proof['witness']
        identity=proof['identity']
        args=(identity['cik'],identity['accession'],identity['document'])
        before=sec_document_lineage.inspect(query,*args,claim,source_guard)
        if (before['candidate_hash']!=witness['candidate_hash']
                or before['archive_version_key']!=witness['archive_version_key']
                or before['document']['content_sha256']!=witness.get('retained_document_hash',witness['document_hash'])):
            raise ValueError('Supplemental resource lineage changed')
        sec_witness_publication.Publisher(query,database,claim,source_guard,lambda:None).publish(
            (witness,),(proof['qualification'],),proof['receipt'],write=False)
        if sec_document_lineage.inspect(query,*args,claim,source_guard)!=before:
            raise ValueError('Supplemental resource lineage changed during readback')
    return digest(proofs) if proofs else None
