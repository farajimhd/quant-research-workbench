from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import tarfile

import pytest

from pipelines.certified_research import sec_archive_witness as witness
from pipelines.certified_research.contracts import digest
from pipelines.sec.edgar.sec_pipeline.revision import source_revision


@contextmanager
def fixture():
    payload='<XBRL><html>original payload</html></XBRL>'
    raw=('<ACCESSION-NUMBER>0000320193-25-000079\n<CIK>0000320193\n<TYPE>10-K\n'
        '<PUBLIC-DOCUMENT-COUNT>1\n<FILING-DATE>20251031\n<DATE-OF-FILING-DATE-CHANGE>20251031\n'
        '<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>1\n<FILENAME>filing.htm\n<TEXT>'+payload+'</TEXT>\n</DOCUMENT>').encode()
    member='./0000320193-25-000079.nc';original='D:/market-data/sec_core/20251031.nc.tar.gz'
    revision=source_revision(archive_date='2025-10-31',archive_member=member,archive_path=original,
        source_content_sha256=sha256(raw).hexdigest(),occurrence_sequence=2)
    lineage=dict(document=dict(source_archive_path=original,source_archive_member=member,source_archive_date='2025-10-31',
        accession_number='0000320193-25-000079',cik='320193',sequence_number=1,document_name='filing.htm',document_type='10-K',
        content_sha256=sha256(payload.encode()).hexdigest()),archive_submission_hash=sha256(raw).hexdigest(),
        archive_version_key=revision.source_version_key,archive_revision_rank=revision.source_revision_rank,candidate_hash='a'*64)
    prepared=dict(document=dict(document_key='b'*64,lineage=lineage))
    prepared['prepared_hash']=digest(prepared)
    with TemporaryDirectory(dir='D:/TradingML/runtimes',prefix='sec-archive-witness-') as directory:
        path=Path(directory)/'20251031.nc.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            for name,data in (('other.nc',b'other'),(member,raw)):
                entry=tarfile.TarInfo(name);entry.size=len(data);archive.addfile(entry,BytesIO(data))
        yield path,prepared


def test_original_submission_ordinal_and_document_bytes_match_without_public_certification():
    with fixture() as (path,prepared):
        result=witness.inspect_archive(path,prepared)
    assert result['member_ordinal']==2 and result['original_bytes_verified'] is True
    assert result['public_time_qualified'] is False and result['allocation_certified'] is False


@pytest.mark.parametrize('bound',('maximum_archive_bytes','maximum_member_bytes','maximum_scan_bytes','maximum_members'))
def test_archive_member_and_scan_bounds_fail_explicitly(bound):
    with fixture() as (path,prepared),pytest.raises(ValueError):
        witness.inspect_archive(path,prepared,**{bound:1})


def test_different_original_document_hash_is_not_a_witness():
    with fixture() as (path,prepared):
        prepared['document']['lineage']['document']['content_sha256']='c'*64
        prepared.pop('prepared_hash');prepared['prepared_hash']=digest(prepared)
        with pytest.raises(ValueError,match='payload differs'):witness.inspect_archive(path,prepared)


def test_v2_container_and_official_date_evidence_are_checked_independently():
    with fixture() as (path,prepared):
        staging=dict(status='staged',bytes=path.stat().st_size,sha256=sha256(path.read_bytes()).hexdigest())
        result=witness.inspect_archive(path,prepared,staging_receipt=staging)
        assert result['container_digest_verified'] is True
        assert result['official_filing_date']==result['filing_date_change']=='2025-10-31'
        assert result['public_document_count']==1 and result['acceptance_datetime_raw']==''
        staging['sha256']='c'*64
        with pytest.raises(ValueError,match='container digest'):witness.inspect_archive(path,prepared,staging_receipt=staging)
