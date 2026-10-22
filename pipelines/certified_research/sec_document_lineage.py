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
    """At most two rows per source; no payload transfer, publication or repair."""
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
        # Do not add FINAL: multiple physical versions must remain visible to
        # the ambiguity check below, rather than being silently reconciled.
        text=query(f'SELECT {columns},lower(hex(SHA256(source_text))) actual_text_hash FROM q_live.sec_filing_text_v3 PREWHERE {where} LIMIT 2')
        document=query(f'SELECT {columns} FROM q_live.sec_filing_document_v3 WHERE {where} LIMIT 2')
        archive=query('SELECT accession_number,primary_cik,source_archive_date,source_archive_member,source_archive_path,'
            'source_content_sha256,source_version_key,source_revision_rank,source_revision_at,source_revision_kind,pac_event_id '
            f'FROM q_live.sec_filing_archive_accession_v3 WHERE accession_number={literal(accession)} LIMIT 2')
        if any(len(rows)!=1 for rows in (text,document,archive)):
            raise ValueError('Document lineage is missing or ambiguous; explicit reconciliation required')
        archive[0]['source_revision_rank']=int(archive[0]['source_revision_rank'])
        for row in (*text,*document):
            if 'source_revision_rank' in row:row['source_revision_rank']=int(row['source_revision_rank'])
        return bridge(text[0],document[0],archive[0])
    guard();first=load();guard();second=load();guard()
    if first!=second:raise ValueError('SEC candidate lineage changed during inspection')
    return first
