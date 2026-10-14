from hashlib import sha256
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_share_capture as capture,sec_share_source
from pipelines.certified_research.publication import DictionaryClaim
from tests.test_certified_sec_share_context import document


def prepared():
    payload=document();source_hash=sha256(payload.encode()).hexdigest()
    linkage=dict(candidate_hash='a'*64,document=dict(content_sha256=source_hash,source_archive_date='2025-10-31'))
    query=Mock(side_effect=[[dict(n=1,bytes=len(payload.encode()))],[dict(source_text=payload)]])
    with patch.object(sec_share_source.sec_document_lineage,'inspect',return_value=linkage):
        return sec_share_source.prepare(query,'320193','0000320193-25-000079','filing.htm',Mock(),Mock())


def test_capture_schemas_are_normalized_monthly_and_explicitly_ssd_compressed():
    tables=capture.tables()
    assert len(tables)==3
    for table in tables:
        ddl=table.ddl('q_live')
        assert "storage_policy='live_market_ssd'" in ddl
        assert 'PARTITION BY toYYYYMM(source_archive_date)' in ddl
        assert all('ZSTD' in c.codec for c in table.columns)
    assert next(c.type for c in tables[2].columns if c.name=='value')=='Nullable(String)'


def test_records_factor_contexts_and_reject_mutated_fingerprints():
    source=prepared();groups=capture.records(source)
    assert [len(rows) for rows in groups]==[1,1,1]
    assert groups[0][0]['cik']==320193
    source['observations'][0]['value']='999'
    with pytest.raises(ValueError,match='fingerprint'):capture.records(source)


def test_missing_admission_is_rejected_before_any_database_access():
    query=Mock()
    with pytest.raises(ValueError,match='admission'):
        capture.capture(query,'q_live',prepared(),Mock(),Mock(),admission=None)
    query.assert_not_called()


def test_misplaced_inactive_parts_block_installation_before_ddl():
    catalog=capture.tables();queries=[]
    def query(sql,*,read=True):
        queries.append(sql)
        if 'system.storage_policies' in sql:return [dict(disk_name='live_market_ssd')]
        if 'system.tables' in sql:return [dict(name=t.name,engine='MergeTree',sorting_key=','.join(t.order),
            partition_key=t.partition,storage_policy='live_market_ssd') for t in catalog]
        if 'system.columns' in sql:return [dict(table=t.name,name=c.name,type=c.type,compression_codec='CODEC('+c.codec+')')
            for t in catalog for c in t.columns]
        if 'system.parts' in sql:
            assert 'WHERE active' not in sql
            return [dict(table=catalog[0].name,disk_name='default')]
        raise AssertionError('Unexpected writer/query')
    claim=Mock(spec=DictionaryClaim);claim.dictionary_database='q_live'
    with pytest.raises(ValueError,match='active/inactive parts'):
        capture.install(query,'q_live',claim)
    assert all(sql.startswith('SELECT ') for sql in queries)
