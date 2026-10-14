import os
import uuid
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_scope_specification as bank
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_scope_parity import inputs


def test_normalized_specification_stores_metadata_once_and_has_lossless_ssd_ddl():
    _,spec=inputs();rows,headers=bank.records(spec)
    assert len(rows)==len(headers)==1 and headers[0]['bindings']==1
    assert 'metrics' not in rows[0] and 'cik' not in rows[0] and 'certified' not in headers[0]
    for table in bank.tables():
        assert "storage_policy='live_market_ssd'" in table.ddl('q_live')
        assert all('ZSTD' in c.codec for c in table.columns)


def test_roundtrip_checks_hash_and_duplicate_rows():
    _,spec=inputs();groups=bank.records(spec)
    def query(sql):
        rows=groups[1] if 'research_sec_scope_specification_v1' in sql else groups[0]
        return [dict(n=len(rows),bytes=100)] if 'count()' in sql else list(rows)
    with patch.object(bank.sec_share_capture,'verify'):
        assert bank.load(query,'q_live',spec['specification_hash'],Mock(),Mock())==spec
    changed=dict(spec,entity_key=2)
    with pytest.raises(ValueError,match='fingerprint'):bank.records(changed)
    body={k:v for k,v in spec.items() if k!='specification_hash'}
    body['bindings']=body['bindings']*2
    with pytest.raises(ValueError,match='Duplicate'):bank.records(dict(body,specification_hash=digest(body)))


def test_stored_guard_uses_frozen_locator_and_rechecks_metadata_after_parity():
    scope,spec=inputs()
    from types import SimpleNamespace
    plan=SimpleNamespace(unit=SimpleNamespace(product='fundamentals'),arguments=dict(entity_key=1,start=str(scope.start),
        end=str(scope.end),source_hash=scope.parent_hash,original_specification_hash=spec['specification_hash']))
    guard=bank.StoredGuard(Mock(),(plan,),Mock(),Mock())
    with patch.object(bank,'load',return_value=spec) as load,patch.object(bank.sec_scope_parity,'verify',return_value='verified'):
        assert guard(scope)=='verified' and load.call_count==2
    with patch.object(bank,'load',side_effect=[spec,dict(spec,entity_key=2)]),\
         patch.object(bank.sec_scope_parity,'verify',return_value='verified'),pytest.raises(ValueError,match='changed'):
        guard(scope)


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse opt-in')
def test_real_normalized_scope_publication_readback_reuse_and_duplicate_rejection():
    from scripts import build_market_day as b
    args=b.parse_args(['--date','2026-01-02','--env-file',str(b.ROOT/'.env')])
    client=b.Client(args,persistent=False);database='certified_research_test_'+uuid.uuid4().hex;created=False
    try:
        bank.sec_share_capture.verify(client.query,database,catalog=bank.tables())
        client.query('CREATE DATABASE '+database,read=False);created=True
        for table in bank.tables():client.query(table.ddl(database),read=False)
        _,spec=inputs();admit=lambda size:dict(status='admitted',metadata_bytes=size,isolated_fixture=True)
        first=bank.capture(client.query,database,spec,Mock(),Mock(),admit)
        assert first['inserted_rows']==2 and not first['certified']
        assert bank.capture(client.query,database,spec,Mock(),Mock(),admit)['inserted_rows']==0
        assert bank.load(client.query,database,spec['specification_hash'],Mock(),Mock())==spec
        table=bank.tables()[0].name
        client.query(f'INSERT INTO {database}.{table} SELECT * FROM {database}.{table}',read=False)
        with pytest.raises(ValueError,match='fingerprint|Duplicate|count'):
            bank.load(client.query,database,spec['specification_hash'],Mock(),Mock())
    finally:
        if created:client.query('DROP DATABASE '+database+' SYNC',read=False)
        client.close()
