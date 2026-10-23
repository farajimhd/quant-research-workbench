"""Ingestion-owned bounded SEC archive byte witnesses; no market flatfiles."""
from hashlib import sha256
from pathlib import Path,PureWindowsPath
import re
import tarfile

from pipelines.sec.edgar.sec_filing_text_extract_parts import parse_filing
from pipelines.sec.edgar.sec_pipeline.revision import source_revision
from .contracts import digest


def inspect_archives(path,prepared,*,stop=None,progress=None,maximum_archive_bytes=2*1024**3,
                    maximum_member_bytes=64*1024**2,maximum_scan_bytes=32*1024**3,maximum_members=100000,staging_receipt=None,
                    original_document_sink=None):
    """Read only a designated D copy and compare raw bytes with retained lineage.

    Staging owns the G→D copy/digest/reserve policy. This verifier establishes
    submission/document byte parity, not original public-time qualification.
    """
    path=Path(path).resolve();location=str(path).replace('\\','/').lower()
    if not (location.startswith('d:/') or location.startswith('//desktop-saai85t/workstation-d/') or location.startswith('/mnt/d/')):
        raise ValueError('SEC archive witness requires a designated D copy')
    for value in (maximum_archive_bytes,maximum_member_bytes,maximum_scan_bytes,maximum_members):
        if type(value) is not int or value<=0:raise ValueError('Invalid SEC archive witness bound')
    if not isinstance(prepared,(tuple,list)) or not 1<=len(prepared)<=1000:
        raise ValueError('SEC archive verification needs 1 to 1000 prepared documents')
    pending={};keys=[];seen=set();archive_identity=None
    for source in prepared:
        body=dict(source);hash_=body.pop('prepared_hash',None)
        if hash_!=digest(body):raise ValueError('SEC prepared source fingerprint differs')
        document=source['document']['lineage']['document'];key=source['document']['document_key']
        if key in seen:raise ValueError('SEC archive documents are duplicated')
        seen.add(key);keys.append(key)
        canonical=PureWindowsPath(document['source_archive_path'])
        identity=(canonical,document['source_archive_date'])
        if path.name!=canonical.name or (archive_identity is not None and identity!=archive_identity):
            raise ValueError('SEC archive file identity differs')
        archive_identity=identity
        pending.setdefault(document['source_archive_member'].lstrip('./'),[]).append(source)
    before=path.stat()
    if not 0<before.st_size<=maximum_archive_bytes:raise ValueError('SEC archive exceeds its compressed byte bound')
    def check():
        if stop is not None and stop.is_set():raise InterruptedError('SEC archive witness stopped')
    check();container_hash=None
    if staging_receipt is not None:
        if (staging_receipt.get('status')!='staged' or staging_receipt.get('bytes')!=before.st_size
                or not re.fullmatch('[0-9a-f]{64}',staging_receipt.get('sha256') or '')):
            raise ValueError('SEC staging receipt is not a verified bounded copy')
        checksum=sha256()
        with path.open('rb') as reader:
            while chunk:=reader.read(8*1024**2):check();checksum.update(chunk)
        container_hash=checksum.hexdigest()
        if container_hash!=staging_receipt['sha256']:raise ValueError('Staged SEC container digest differs')
    scanned=0;results={}
    with tarfile.open(path,'r|gz') as archive:
        for index,member in enumerate(archive,start=1):
            check()
            scanned+=512+((member.size+511)//512)*512
            if index>maximum_members or scanned>maximum_scan_bytes:raise ValueError('SEC archive scan exceeds its bound')
            if progress and index%250==0:progress(index,scanned)
            wanted=member.name.lstrip('./')
            if wanted not in pending:continue
            if not member.isfile() or not 0<member.size<=maximum_member_bytes:
                raise ValueError('SEC submission is not a bounded regular archive member')
            reader=archive.extractfile(member)
            if reader is None:raise ValueError('SEC submission cannot be opened')
            with reader:raw=reader.read(maximum_member_bytes+1)
            if len(raw)!=member.size:raise ValueError('SEC submission member byte count differs')
            check();sources=pending.pop(wanted)
            submission_hash=sha256(raw).hexdigest();filing=parse_filing(raw,wanted)
            selected={(s['document']['lineage']['document']['sequence_number'],
                s['document']['lineage']['document']['document_name'],
                s['document']['lineage']['document']['document_type']) for s in sources}
            document_hashes={key:[] for key in selected}
            document_payloads={}
            for row in filing['documents']:
                identity=(row['sequence_number'],row['document_name'],row['document_type'])
                if identity in document_hashes:
                    document_hashes[identity].append(sha256(row['payload'].encode('utf-8')).hexdigest())
                    document_payloads[identity]=row['payload']
            for source in sources:
                check();result=_document_witness(source,len(raw),submission_hash,index,filing,document_hashes,before.st_size,container_hash,
                    archive_original=original_document_sink is not None)
                if result['version']=='sec-archive-original-document-witness-v3':
                    doc=source['document']['lineage']['document']
                    original_document_sink(result,document_payloads[(doc['sequence_number'],doc['document_name'],doc['document_type'])])
                results[result['document_key']]=result
            if not pending:break
    if pending:raise ValueError('Selected SEC submission is absent from the archive: '+','.join(sorted(pending)[:4]))
    after=path.stat();check()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Staged SEC archive changed during verification')
    return tuple(results[key] for key in keys)


def _document_witness(prepared,raw_bytes,submission_hash,ordinal,filing,document_hashes,archive_bytes,container_hash,*,archive_original=False):
    lineage=prepared['document']['lineage'];document=lineage['document']
    if submission_hash!=lineage['archive_submission_hash']:raise ValueError('Original SEC submission hash differs')
    revision=source_revision(archive_date=document['source_archive_date'],archive_member=document['source_archive_member'],
        archive_path=document['source_archive_path'],source_content_sha256=submission_hash,occurrence_sequence=ordinal)
    if revision.source_version_key!=lineage['archive_version_key'] or revision.source_revision_rank!=lineage['archive_revision_rank']:
        raise ValueError('Original SEC occurrence ordinal/version differs')
    if filing['accession_number']!=document['accession_number'] or str(int(filing['cik']))!=str(int(document['cik'])):
        raise ValueError('Original SEC submission identity differs')
    selected=document_hashes[(document['sequence_number'],document['document_name'],document['document_type'])]
    if len(selected)!=1 or (selected[0]!=document['content_sha256'] and not archive_original):
        raise ValueError('Original SEC document payload differs')
    header=filing['header_text']
    public_counts=re.findall(r'(?:<PUBLIC-DOCUMENT-COUNT>\s*|PUBLIC DOCUMENT COUNT:\s*)([0-9]+)',header,re.I)
    filing_dates=re.findall(r'(?:<FILING-DATE>\s*|FILED AS OF DATE:\s*)([0-9]{8})',header,re.I)
    filing_day=(filing_dates[0][:4]+'-'+filing_dates[0][4:6]+'-'+filing_dates[0][6:]) if len(filing_dates)==1 else None
    change_dates=re.findall(r'<DATE-OF-FILING-DATE-CHANGE>\s*([0-9]{8})',header,re.I)
    change_day=(change_dates[0][:4]+'-'+change_dates[0][4:6]+'-'+change_dates[0][6:]) if len(change_dates)==1 else None
    result=dict(version='sec-archive-document-byte-witness-v2',document_key=prepared['document']['document_key'],
        candidate_hash=lineage['candidate_hash'],archive_bytes=archive_bytes,submission_bytes=raw_bytes,member_ordinal=ordinal,
        submission_hash=submission_hash,document_hash=document['content_sha256'],archive_version_key=revision.source_version_key,
        source_archive_date=document['source_archive_date'],form_type=filing['form_type'],
        official_filing_date=filing_day,filing_date_change=change_day,
        acceptance_datetime_raw=filing['acceptance_datetime_raw'],public_document_count=int(public_counts[0]) if len(public_counts)==1 else None,
        special_release=any(re.search(r'<'+tag+r'(?:\s|>)',header,re.I) for tag in ('CORRECTION','DELETION','PRIVATE-TO-PUBLIC','PAPER'))
            or bool(re.search(r'^\s*(?:CORRECTION|DELETION|PRIVATE[- ]TO[- ]PUBLIC|PAPER)\s*:',header,re.I|re.M)),
        container_sha256=container_hash,container_digest_verified=container_hash is not None,
        original_bytes_verified=True,public_time_qualified=False,allocation_certified=False)
    if selected[0]!=document['content_sha256']:
        if lineage.get('document_revision_state')!='live_pending_archive_bytes':
            raise ValueError('Archive-original transport requires an explicit live/archive candidate')
        result.update(version='sec-archive-original-document-witness-v3',document_hash=selected[0],
            retained_document_hash=document['content_sha256'],cik=document['cik'],accession=document['accession_number'],
            document_name=document['document_name'],source_authority='original_archive_document')
    return dict(result,witness_hash=digest(result))


def inspect_archive(path,prepared,**options):
    """Single-document compatibility entry point through the grouped reader."""
    return inspect_archives(path,(prepared,),**options)[0]
