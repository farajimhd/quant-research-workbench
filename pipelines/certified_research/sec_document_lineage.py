"""Read-only candidate bridges; never manufacture document-vintage authority."""
from datetime import date,datetime,timezone
import re

from pipelines.sec.edgar.sec_pipeline.revision import source_revision,REVISION_TIE_RANGE
from .contracts import digest,literal


def bridge(text,document,archive):
    """Require exact retained document identity and reproducible archive metadata."""
    identity=('document_id','accession_number','cik','sequence_number','document_name','document_type',
        'source_archive_date','source_archive_member','source_archive_path','content_sha256')
    if any(text.get(k)!=document.get(k) for k in identity):raise ValueError('Document/text lineage differs')
    for key in ('accession_number','source_archive_date','source_archive_member','source_archive_path'):
        if not text.get(key) or text[key]!=archive.get(key):raise ValueError('Archive occurrence identity differs')
    if (not re.fullmatch('0*[1-9][0-9]{0,9}',text.get('cik',''))
            or str(int(text['cik']))!=str(int(archive.get('primary_cik') or '0'))):
        raise ValueError('Archive issuer differs')
    for row,key in ((text,'content_sha256'),(archive,'source_content_sha256'),(archive,'source_version_key')):
        if not re.fullmatch('[0-9a-f]{64}',row.get(key) or ''):raise ValueError('Archive/document hash is missing')
    if text.get('actual_text_hash')!=text['content_sha256']:raise ValueError('Retained text bytes differ from their hash')
    if archive.get('source_revision_kind')!='archive_occurrence' or archive.get('pac_event_id'):
        raise ValueError('Candidate bridge requires an ordinary archive occurrence; PAC needs separate qualification')
    day=date.fromisoformat(str(archive['source_archive_date']))
    base=int(datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc).timestamp())*REVISION_TIE_RANGE
    rank=archive.get('source_revision_rank')
    if type(rank) is not int or not 0<=rank-base<REVISION_TIE_RANGE:raise ValueError('Archive occurrence rank is invalid')
    expected=source_revision(archive_date=day,archive_member=archive['source_archive_member'],
        archive_path=archive['source_archive_path'],source_content_sha256=archive['source_content_sha256'],
        occurrence_sequence=rank-base)
    if (expected.source_version_key!=archive['source_version_key']
            or expected.source_revision_at!=str(archive.get('source_revision_at'))):
        raise ValueError('Archive occurrence version cannot be reproduced')
    legacy=all(row.get('source_revision_kind')=='legacy_archive_occurrence' and not row.get('source_version_key') for row in (text,document))
    current=all(row.get('source_revision_kind') in ('archive_occurrence','archive_raw_lineage_recovery')
        and row.get('source_version_key')==archive['source_version_key']
        and row.get('source_revision_rank')==rank and str(row.get('source_revision_at'))==expected.source_revision_at
        for row in (text,document))
    if not legacy and not current:raise ValueError('Candidate document/text revision pair differs')
    result=dict(version='sec-document-lineage-candidate-v2',document={k:text[k] for k in identity},
        document_revision_state='legacy' if legacy else 'versioned_archive',
        archive_version_key=expected.source_version_key,archive_submission_hash=archive['source_content_sha256'],
        archive_revision_rank=rank,candidate_only=True,original_vintage_qualified=False)
    return dict(result,candidate_hash=digest(result))


def inspect(query,cik,accession,document_name,claim,source_guard):
    """At most two distinct versions per source; no publication or repair."""
    if (not isinstance(cik,str) or not re.fullmatch('0*[1-9][0-9]{0,9}',cik)
            or not isinstance(accession,str) or not re.fullmatch('[0-9]{10}-[0-9]{2}-[0-9]{6}',accession)
            or not isinstance(document_name,str) or not 1<=len(document_name)<=256):
        raise ValueError('Invalid explicit SEC document lineage scope')
    def guard():claim.check();source_guard();claim.check()
    ciks=','.join(literal(v) for v in sorted({str(int(cik)),str(int(cik)).zfill(10)}))
    where=f'accession_number={literal(accession)} AND document_name={literal(document_name)} AND cik IN ({ciks})'
    columns='document_id,accession_number,cik,sequence_number,document_name,document_type,source_archive_date,source_archive_member,source_archive_path,content_sha256,source_revision_kind,source_version_key,source_revision_rank,source_revision_at'
    def load():
        # Filter the exact document before reading large text payload columns.
        # DISTINCT collapses identical ingestion copies only. Do not add FINAL:
        # differing versions must remain visible to the ambiguity check below.
        text=query(f'SELECT DISTINCT {columns},lower(hex(SHA256(source_text))) actual_text_hash FROM q_live.sec_filing_text_v3 PREWHERE {where} LIMIT 2')
        document=query(f'SELECT DISTINCT {columns} FROM q_live.sec_filing_document_v3 WHERE {where} LIMIT 2')
        live_pair=len(text)==len(document)==1 and text[0].get('source_revision_kind')=='live_feed_occurrence'
        limit=3 if live_pair else 2
        archive=query('SELECT DISTINCT accession_number,primary_cik,source_archive_date,source_archive_member,source_archive_path,'
            'source_content_sha256,source_version_key,source_revision_rank,source_revision_at,source_revision_kind,pac_event_id '
            f'FROM q_live.sec_filing_archive_accession_v3 WHERE accession_number={literal(accession)} LIMIT {limit}')
        if live_pair:
            # Two legitimate transport occurrences are possible. Three means
            # ambiguity, never silently choose the newest occurrence.
            for row in (*text,*document,*archive):row['source_revision_rank']=int(row['source_revision_rank'])
            return live_archive_candidate(text[0],document[0],archive)
        if any(len(rows)!=1 for rows in (text,document,archive)):
            raise ValueError('Document lineage is missing or ambiguous; explicit reconciliation required')
        archive[0]['source_revision_rank']=int(archive[0]['source_revision_rank'])
        for row in (*text,*document):
            if 'source_revision_rank' in row:row['source_revision_rank']=int(row['source_revision_rank'])
        return bridge(text[0],document[0],archive[0])
    guard();first=load();guard();second=load();guard()
    if first!=second:raise ValueError('SEC candidate lineage changed during inspection')
    return first


def live_archive_candidate(text,document,occurrences):
    """Propose exact document bytes for archive verification, not historical authority.

    Both transport identities remain sealed. Only the subsequent archive witness
    may establish that the retained live document existed in the daily archive.
    """
    ordinary=[r for r in occurrences if r.get('source_revision_kind')=='archive_occurrence']
    live=[r for r in occurrences if r.get('source_revision_kind')=='live_feed_occurrence']
    if len(occurrences)!=2 or len(ordinary)!=1 or len(live)!=1:
        raise ValueError('Live/archive occurrence pair is missing or ambiguous')
    live=live[0];archive=ordinary[0]
    if live.get('pac_event_id') or archive.get('pac_event_id'):
        raise ValueError('PAC needs separate qualification')
    if not re.fullmatch('[0-9a-f]{64}',live.get('source_content_sha256') or ''):
        raise ValueError('Live submission hash is missing')
    for key in ('accession_number','source_archive_date','source_archive_member','source_archive_path'):
        if not text.get(key) or text[key]!=document.get(key) or text[key]!=live.get(key):
            raise ValueError('Live document transport identity differs')
    if (archive['accession_number']!=live['accession_number'] or
            str(int(archive.get('primary_cik') or '0'))!=str(int(live.get('primary_cik') or '0'))):
        raise ValueError('Live/archive issuer or accession differs')
    timestamp=datetime.fromisoformat(str(live['source_revision_at'])).replace(tzinfo=timezone.utc)
    revision=source_revision(archive_date=live['source_archive_date'],archive_member=live['source_archive_member'],
        archive_path=live['source_archive_path'],source_content_sha256=live['source_content_sha256'],occurrence_at_utc=timestamp)
    for row in (text,document,live):
        if any(row.get(k)!=getattr(revision,k) for k in ('source_version_key','source_revision_rank','source_revision_kind')) or str(row.get('source_revision_at'))!=revision.source_revision_at:
            raise ValueError('Live occurrence version cannot be reproduced')
    # Validate the proposed archive target through the same strict archive
    # validator. Preserve the actual live provenance separately in the seal.
    target=[]
    for row in (text,document):
        proposed=dict(row)
        for key in ('source_archive_date','source_archive_member','source_archive_path','source_version_key','source_revision_rank','source_revision_kind','source_revision_at'):
            proposed[key]=archive[key]
        target.append(proposed)
    result=bridge(*target,archive)
    result.pop('candidate_hash')
    result.update(version='sec-document-live-archive-candidate-v1',document_revision_state='live_pending_archive_bytes',
        retained_live_document=dict(document),retained_live_occurrence=dict(live),archive_byte_equivalence_verified=False)
    return dict(result,candidate_hash=digest(result))
