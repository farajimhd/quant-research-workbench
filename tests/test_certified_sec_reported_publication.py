import os
import uuid
from unittest.mock import Mock

import pytest

from pipelines.certified_research import sec_reported_publication as reported,sec_fundamental_publication as source_publication,sec_fundamental_context,sec_public_archive_time
from pipelines.certified_research import sec_witness_publication,sec_reported_readback
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_fundamental_publication import source
from tests.test_certified_sec_share_context import document
from hashlib import sha256


def valid_source():
    extraction,bulk,witness,qualification,receipt=source()
    payload=document().replace('decimals="-3"','decimals="0"')
    extraction=sec_fundamental_context.extract(payload,sha256(payload.encode()).hexdigest(),'320193')
    witness.pop('witness_hash');witness.update(document_hash=extraction['source_text_hash'],submission_hash='c'*64,
        archive_version_key='d'*64,member_ordinal=1);witness['witness_hash']=digest(witness)
    return extraction,bulk,witness,sec_public_archive_time.qualify(witness,receipt),receipt


def test_projection_rows_reference_sources_without_repeating_values_or_concepts():
    extraction,bulk,witness,qualification,receipt=valid_source()
    source_hash=source_publication.records(extraction,bulk,witness,qualification,receipt)[-1][0]['reconciliation_hash']
    groups,headers=reported.records(extraction,witness,qualification,receipt,numeric_mode='round-to-nearest',source_reconciliation_hash=source_hash)
    assert len(groups)==len(headers)==1 and 'value' not in groups[0] and 'concept' not in groups[0]
    assert groups[0]['all_source_positions']==(0,)
    assert headers[0]['source_reconciliation_hash']==source_hash
    for table in reported.tables():
        assert "storage_policy='live_market_ssd'" in table.ddl('q_live')
        assert all('ZSTD' in c.codec for c in table.columns)


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse opt-in')
def test_real_projection_publication_readback_and_restart_reuse():
    from scripts import build_market_day as builder
    args=builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')])
    client=builder.Client(args,persistent=False);database='certified_research_test_'+uuid.uuid4().hex;created=False
    try:
        reported.sec_share_capture.verify(client.query,database,catalog=reported.tables())
        client.query('CREATE DATABASE '+database,read=False);created=True
        for table in (*source_publication.tables(),*reported.tables(),*sec_witness_publication.tables()):client.query(table.ddl(database),read=False)
        extraction,bulk,witness,qualification,receipt=valid_source()
        def admission(size):return dict(status='admitted',metadata_bytes=size,isolated_fixture=True)
        sec_witness_publication.Publisher(client.query,database,Mock(),Mock(),admission).publish((witness,),(qualification,),receipt)
        original=source_publication.capture(client.query,database,extraction,bulk,witness,qualification,receipt,Mock(),Mock(),admission)
        def capture():return reported.capture(client.query,database,extraction,witness,qualification,receipt,Mock(),Mock(),admission,
            numeric_mode='round-to-nearest',source_reconciliation_hash=original['reconciliation_hash'])
        first=capture();assert first['inserted_rows']==2 and not first['certified']
        assert capture()['inserted_rows']==0
        verified=sec_reported_readback.verify(client.query,database,first['binding_hash'],Mock(),Mock())
        assert verified['groups']==1 and verified['public_time_qualified'] and not verified['certified']
        reported.sec_share_capture.verify(client.query,database,required=True,catalog=reported.tables())
        table=reported.tables()[0].name
        client.query(f'INSERT INTO {database}.{table} SELECT * FROM {database}.{table}',read=False)
        with pytest.raises(ValueError):sec_reported_readback.verify(client.query,database,first['binding_hash'],Mock(),Mock())
    finally:
        if created:client.query('DROP DATABASE '+database+' SYNC',read=False)
        client.close()
