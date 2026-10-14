"""Ingestion-owned bounded SEC archive byte witnesses; no market flatfiles."""
from hashlib import sha256
from pathlib import Path,PureWindowsPath
import tarfile

from pipelines.sec.edgar.sec_filing_text_extract_parts import parse_filing
from pipelines.sec.edgar.sec_pipeline.revision import source_revision
from .contracts import digest


def inspect_archive(path,prepared,*,stop=None,progress=None,maximum_archive_bytes=2*1024**3,
                    maximum_member_bytes=64*1024**2,maximum_scan_bytes=32*1024**3,maximum_members=100000):
    """Read only a designated D copy and compare raw bytes with retained lineage.

    Staging owns the G→D copy/digest/reserve policy. This verifier establishes
    submission/document byte parity, not original public-time qualification.
    """
    path=Path(path).resolve();location=str(path).replace('\\','/').lower()
    if not (location.startswith('d:/') or location.startswith('//desktop-saai85t/workstation-d/') or location.startswith('/mnt/d/')):
        raise ValueError('SEC archive witness requires a designated D copy')
    for value in (maximum_archive_bytes,maximum_member_bytes,maximum_scan_bytes,maximum_members):
        if type(value) is not int or value<=0:raise ValueError('Invalid SEC archive witness bound')
    body=dict(prepared);hash_=body.pop('prepared_hash',None)
    if hash_!=digest(body):raise ValueError('SEC prepared source fingerprint differs')
    lineage=prepared['document']['lineage'];document=lineage['document']
    if path.name!=PureWindowsPath(document['source_archive_path']).name:raise ValueError('SEC archive file identity differs')
    before=path.stat()
    if not 0<before.st_size<=maximum_archive_bytes:raise ValueError('SEC archive exceeds its compressed byte bound')
    def check():
        if stop is not None and stop.is_set():raise InterruptedError('SEC archive witness stopped')
    check();wanted=document['source_archive_member'].lstrip('./');scanned=0;raw=None;ordinal=None
    with tarfile.open(path,'r|gz') as archive:
        for index,member in enumerate(archive,start=1):
            check()
            scanned+=512+((member.size+511)//512)*512
            if index>maximum_members or scanned>maximum_scan_bytes:raise ValueError('SEC archive scan exceeds its bound')
            if progress and index%250==0:progress(index,scanned)
            if member.name.lstrip('./')!=wanted:continue
            if not member.isfile() or not 0<member.size<=maximum_member_bytes:
                raise ValueError('SEC submission is not a bounded regular archive member')
            reader=archive.extractfile(member)
            if reader is None:raise ValueError('SEC submission cannot be opened')
            with reader:raw=reader.read(maximum_member_bytes+1)
            if len(raw)!=member.size:raise ValueError('SEC submission member byte count differs')
            ordinal=index;break
    if raw is None:raise ValueError('Selected SEC submission is absent from the archive')
    check()
    submission_hash=sha256(raw).hexdigest()
    if submission_hash!=lineage['archive_submission_hash']:raise ValueError('Original SEC submission hash differs')
    revision=source_revision(archive_date=document['source_archive_date'],archive_member=document['source_archive_member'],
        archive_path=document['source_archive_path'],source_content_sha256=submission_hash,occurrence_sequence=ordinal)
    if revision.source_version_key!=lineage['archive_version_key'] or revision.source_revision_rank!=lineage['archive_revision_rank']:
        raise ValueError('Original SEC occurrence ordinal/version differs')
    filing=parse_filing(raw,wanted)
    if filing['accession_number']!=document['accession_number'] or str(int(filing['cik']))!=str(int(document['cik'])):
        raise ValueError('Original SEC submission identity differs')
    selected=[row for row in filing['documents'] if (row['sequence_number'],row['document_name'],row['document_type'])==
        (document['sequence_number'],document['document_name'],document['document_type'])]
    if len(selected)!=1 or sha256(selected[0]['payload'].encode('utf-8')).hexdigest()!=document['content_sha256']:
        raise ValueError('Original SEC document payload differs')
    after=path.stat();check()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Staged SEC archive changed during verification')
    result=dict(version='sec-archive-document-byte-witness-v1',document_key=prepared['document']['document_key'],
        candidate_hash=lineage['candidate_hash'],archive_bytes=before.st_size,submission_bytes=len(raw),member_ordinal=ordinal,
        submission_hash=submission_hash,document_hash=document['content_sha256'],archive_version_key=revision.source_version_key,
        original_bytes_verified=True,public_time_qualified=False,allocation_certified=False)
    return dict(result,witness_hash=digest(result))
