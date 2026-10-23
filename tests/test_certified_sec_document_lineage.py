from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from pipelines.certified_research import sec_document_lineage as lineage
from pipelines.sec.edgar.sec_pipeline.revision import source_revision


def fixture():
    common=dict(accession_number='0000320193-25-000079',source_archive_date='2025-10-31',
        source_archive_member='./0000320193-25-000079.nc',source_archive_path='D:/TradingML/runtimes/sec/archive.nc')
    archive=dict(common,primary_cik='320193',source_content_sha256='a'*64,pac_event_id=None)
    revision=source_revision(archive_date=common['source_archive_date'],archive_member=common['source_archive_member'],
        archive_path=common['source_archive_path'],source_content_sha256='a'*64,occurrence_sequence=2900)
    archive.update(asdict(revision))
    document=dict(common,document_id='document',cik='0000320193',sequence_number=1,document_name='filing.htm',
        document_type='10-K',content_sha256='b'*64,source_revision_kind='legacy_archive_occurrence',source_version_key='')
    return dict(document,actual_text_hash='b'*64),document,archive


def test_candidate_bridge_preserves_full_submission_and_distinct_document_hashes():
    result=lineage.bridge(*fixture())
    assert result['archive_submission_hash']=='a'*64
    assert result['document']['content_sha256']=='b'*64
    assert result['candidate_only'] is True and result['original_vintage_qualified'] is False
    assert len(result['candidate_hash'])==64


def test_matching_versioned_archive_pair_is_candidate_without_backdating():
    text,document,archive=fixture()
    for row in (text,document):
        for key in ('source_version_key','source_revision_rank','source_revision_at','source_revision_kind'):
            row[key]=archive[key]
    result=lineage.bridge(text,document,archive)
    assert result['document_revision_state']=='versioned_archive'
    assert result['original_vintage_qualified'] is False


@pytest.mark.parametrize('index,key,value',[
    (0,'actual_text_hash','c'*64),(1,'document_name','other.htm'),
    (2,'source_content_sha256','c'*64),(2,'source_revision_rank',1),
    (2,'source_revision_kind','pac'),(2,'primary_cik','1'),
    (0,'source_version_key','c'*64),
])
def test_mismatches_are_not_reconstructed_as_authority(index,key,value):
    rows=list(fixture());rows[index]=dict(rows[index],**{key:value})
    with pytest.raises(ValueError):lineage.bridge(*rows)


def test_inspection_is_bounded_read_only_and_rechecks_stability():
    text,document,archive=fixture();queries=[]
    def query(sql):
        queries.append(sql)
        return [deepcopy(text if '.sec_filing_text_v3 ' in sql else
            document if '.sec_filing_document_v3 ' in sql else archive)]
    owner=SimpleNamespace(check=lambda:None)
    result=lineage.inspect(query,'320193',text['accession_number'],'filing.htm',owner,lambda:None)
    assert result['candidate_only'] is True and len(queries)==6
    assert all(sql.startswith('SELECT ') and sql.endswith('LIMIT 2') for sql in queries)
    assert all(sql.startswith('SELECT DISTINCT ') for sql in queries)
    payload_queries=[sql for sql in queries if '.sec_filing_text_v3 ' in sql]
    assert len(payload_queries)==2
    assert all(' PREWHERE ' in sql and ' FINAL ' not in sql for sql in payload_queries)


def test_ambiguous_physical_versions_require_reconciliation():
    owner=SimpleNamespace(check=lambda:None)
    with pytest.raises(ValueError,match='ambiguous'):
        lineage.inspect(lambda sql:[], '320193','0000320193-25-000079','filing.htm',owner,lambda:None)


def test_valid_but_changed_archive_occurrence_does_not_return_candidate():
    text,document,archive=fixture();calls=[]
    later=dict(archive,**asdict(source_revision(archive_date=archive['source_archive_date'],
        archive_member=archive['source_archive_member'],archive_path=archive['source_archive_path'],
        source_content_sha256=archive['source_content_sha256'],occurrence_sequence=2901)))
    def query(sql):
        calls.append(sql)
        if '.sec_filing_text_v3 ' in sql:return [text]
        if '.sec_filing_document_v3 ' in sql:return [document]
        return [archive if len(calls)==3 else later]
    owner=SimpleNamespace(check=lambda:None)
    with pytest.raises(ValueError,match='changed'):
        lineage.inspect(query,'320193',text['accession_number'],'filing.htm',owner,lambda:None)


def test_live_archive_candidate_requires_later_byte_witness_and_seals_both_transports():
    from datetime import datetime,timezone
    text,document,archive=fixture()
    live=dict(archive,source_archive_path='D:/live/submission.txt',source_content_sha256='c'*64)
    revision=source_revision(archive_date=live['source_archive_date'],archive_member=live['source_archive_member'],
        archive_path=live['source_archive_path'],source_content_sha256=live['source_content_sha256'],
        occurrence_at_utc=datetime(2025,10,31,20,12,1,tzinfo=timezone.utc))
    live.update(asdict(revision))
    for row in (text,document):
        row['source_archive_path']=live['source_archive_path'];row.update(asdict(revision))
    result=lineage.live_archive_candidate(text,document,[archive,live])
    assert result['document']['source_archive_path']==archive['source_archive_path']
    assert result['retained_live_document']['source_archive_path']==live['source_archive_path']
    assert result['candidate_only'] and not result['original_vintage_qualified']
    assert not result['archive_byte_equivalence_verified']
    with pytest.raises(ValueError):lineage.live_archive_candidate(text,document,[archive,live,archive])
    bad=dict(text,actual_text_hash='d'*64)
    with pytest.raises(ValueError):lineage.live_archive_candidate(bad,document,[archive,live])
    bad=dict(live,source_version_key='d'*64)
    with pytest.raises(ValueError):lineage.live_archive_candidate(text,document,[archive,bad])


def test_metadata_only_instance_is_never_claimed_as_verified_text():
    _,document,archive=fixture();document['document_type']='EX-101.INS'
    result=lineage.bridge(document,document,archive,metadata_only=True)
    assert result['document_revision_state']=='archive_metadata_only'
    assert result['retained_text_verified'] is False and result['candidate_only']
    document['document_type']='10-K'
    with pytest.raises(ValueError,match='instance metadata'):
        lineage.bridge(document,document,archive,metadata_only=True)
