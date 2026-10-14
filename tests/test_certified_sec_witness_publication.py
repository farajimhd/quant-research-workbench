import json
import os
import uuid
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_witness_publication as publication,sec_public_archive_time
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_public_archive_time import inputs


def group():
    witness,receipt=inputs()
    witness.pop('witness_hash')
    witness.update(document_key='b'*64,candidate_hash='f'*64,submission_hash='c'*64,document_hash='d'*64,
                   archive_version_key='e'*64,member_ordinal=3)
    witness['witness_hash']=digest(witness)
    return (witness,),(sec_public_archive_time.qualify(witness,receipt),),receipt


def test_normalized_lossless_ssd_schema_and_verified_records():
    archives,documents=publication.records(*group())
    assert len(archives)==len(documents)==1
    assert documents[0]['available_at']=='2025-11-01 04:00:00.000000'
    for table in publication.tables():
        assert "storage_policy='live_market_ssd'" in table.ddl('q_live')
        assert all('ZSTD' in column.codec for column in table.columns)
    witnesses,qualified,receipt=group();qualified[0]['precision']=1
    with pytest.raises(ValueError):publication.records(witnesses,qualified,receipt)


def test_restart_reuses_exact_rows_and_rejects_duplicate_evidence():
    retained={};writes=[]
    def query(sql,*,read=True):
        if not read:
            head,body=sql.split('\n',1);name=head.split()[2].split('.')[1]
            retained.setdefault(name,[]).append(json.loads(body));writes.append(name)
            return []
        name=sql.split(' FROM ')[1].split()[0].split('.')[1]
        return retained.get(name,[])
    publisher=publication.Publisher(query,'q_live',Mock(),Mock(),
        lambda size:dict(status='admitted',metadata_bytes=size))
    with patch.object(publication.sec_share_capture,'verify'):
        assert publisher.publish(*group())['inserted_rows']==2
        assert publisher.publish(*group())['inserted_rows']==0
        retained[publication.tables()[1].name]*=2
        with pytest.raises(ValueError,match='immutable'):publisher.publish(*group())
    assert len(writes)==2


def test_admission_change_is_checked_before_writing():
    source_guard=Mock(side_effect=[None,None,ValueError('changed source')])
    query=Mock(return_value=[])
    publisher=publication.Publisher(query,'q_live',Mock(),source_guard,
        lambda size:dict(status='admitted',metadata_bytes=size))
    with patch.object(publication.sec_share_capture,'verify'):
        with pytest.raises(ValueError,match='changed source'):publisher.publish(*group())
    assert all(call.kwargs.get('read',True) for call in query.call_args_list)


def test_readonly_incomplete_publication_never_inserts_or_admits():
    query=Mock(return_value=[]);admission=Mock()
    publisher=publication.Publisher(query,'q_live',Mock(),Mock(),admission)
    with patch.object(publication.sec_share_capture,'verify'):
        assert publisher.publish(*group(),write=False) is None
    admission.assert_not_called()
    assert all(call.kwargs.get('read',True) for call in query.call_args_list)


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse integration opt-in')
def test_real_clickhouse_publication_and_exact_restart():
    from scripts import build_market_day as builder
    args=builder.parse_args(['--date','2026-01-02','--max-threads','2','--env-file',str(builder.ROOT/'.env')])
    client=builder.Client(args,persistent=False)
    database='certified_research_test_'+uuid.uuid4().hex
    created=False
    try:
        publication.sec_share_capture.verify(client.query,database,catalog=publication.tables())
        client.query('CREATE DATABASE '+database,read=False);created=True
        for table in publication.tables():client.query(table.ddl(database),read=False)
        publisher=publication.Publisher(client.query,database,Mock(),Mock(),
            lambda size:dict(status='admitted',metadata_bytes=size,isolated_fixture=True))
        assert publisher.publish(*group())['inserted_rows']==2
        assert publisher.publish(*group())['inserted_rows']==0
        document=dict(document_key='b'*64,lineage=dict(candidate_hash='f'*64,archive_submission_hash='c'*64,
            document=dict(content_sha256='d'*64)))
        source=dict(document=document);source['prepared_hash']=digest(source)
        reused=publisher.reuse((source,),group()[2],mode='acceptance_checked_day',write=False)
        assert reused['inserted_rows']==0 and reused['readback_verified']
        source['document']['lineage']['candidate_hash']='1'*64
        source.pop('prepared_hash');source['prepared_hash']=digest(source)
        with pytest.raises(ValueError,match='lineage'):
            publisher.reuse((source,),group()[2],mode='acceptance_checked_day')
        publication.sec_share_capture.verify(client.query,database,required=True,catalog=publication.tables())
    finally:
        if created:client.query('DROP DATABASE '+database+' SYNC',read=False)
        client.close()
