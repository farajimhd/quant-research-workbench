import os
import uuid
from unittest.mock import Mock

import pytest

from pipelines.certified_research import sec_fundamental_publication as publication,sec_public_archive_time
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_fundamental_reconciliation import inputs_for_parity


def source():
    extraction,bulk,witness,qualified,receipt=inputs_for_parity()
    witness.pop('witness_hash');witness['document_key']='b'*64;witness['witness_hash']=digest(witness)
    return extraction,bulk,witness,sec_public_archive_time.qualify(witness,receipt),receipt


def test_records_are_lossless_normalized_and_header_last():
    contexts,observations,comparisons,headers=publication.records(*source())
    assert len(observations)==len(comparisons)==len(headers)==1
    assert observations[0]['value']=='1234'
    assert observations[0]['context_hash']==contexts[0]['context_hash']
    assert 'dimensions' not in observations[0] and 'unit' not in observations[0]
    assert comparisons[0]['original_positions']==(0,) and 'original_values' not in comparisons[0]
    assert headers[0]['observations']==1 and headers[0]['comparisons']==1
    for table in publication.tables():
        assert "storage_policy='live_market_ssd'" in table.ddl('q_live')
        assert all('ZSTD' in column.codec for column in table.columns)


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse opt-in')
def test_real_partial_resume_exact_readback_and_zero_insert_reuse():
    from scripts import build_market_day as builder
    args=builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')])
    client=builder.Client(args,persistent=False);database='certified_research_test_'+uuid.uuid4().hex;created=False
    try:
        publication.sec_share_capture.verify(client.query,database,catalog=publication.tables())
        client.query('CREATE DATABASE '+database,read=False);created=True
        for table in publication.tables():client.query(table.ddl(database),read=False)
        def admission(size):return dict(status='admitted',metadata_bytes=size,isolated_fixture=True)
        def stopped(sql,*,read=True):
            if not read and sql.startswith('INSERT INTO '+database+'.'+publication.PREFIX+'document_v2'):
                raise ValueError('interrupted before completion header')
            return client.query(sql,read=read)
        with pytest.raises(ValueError,match='interrupted'):
            publication.capture(stopped,database,*source(),Mock(),Mock(),admission)
        assert publication.capture(client.query,database,*source(),Mock(),Mock(),admission)['inserted_rows']==1
        assert publication.capture(client.query,database,*source(),Mock(),Mock(),admission)['inserted_rows']==0
        from pipelines.certified_research import sec_fundamental_readback
        original_hash=publication.records(*source())[-1][0]['reconciliation_hash']
        assert sec_fundamental_readback.verify(client.query,database,original_hash,Mock(),Mock())['readback_verified']
        changed=list(source());changed[1][0]['value']='999'
        assert publication.capture(client.query,database,*changed,Mock(),Mock(),admission)['inserted_rows']==2
        changed_hash=publication.records(*changed)[-1][0]['reconciliation_hash']
        assert sec_fundamental_readback.verify(client.query,database,changed_hash,Mock(),Mock())['all_bulk_matched'] is False
        assert publication.capture(client.query,database,*source(),Mock(),Mock(),admission)['inserted_rows']==0
        publication.sec_share_capture.verify(client.query,database,required=True,catalog=publication.tables())
        table=publication.tables()[0].name
        client.query(f'INSERT INTO {database}.{table} SELECT * FROM {database}.{table}',read=False)
        with pytest.raises(ValueError,match='conflicts'):
            publication.capture(client.query,database,*source(),Mock(),Mock(),admission)
        with pytest.raises(ValueError,match='uniqueness'):
            sec_fundamental_readback.verify(client.query,database,original_hash,Mock(),Mock())
    finally:
        if created:client.query('DROP DATABASE '+database+' SYNC',read=False)
        client.close()
