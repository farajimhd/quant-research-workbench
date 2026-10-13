"""Original-public SEC inputs are fingerprinted and checked before derivation."""
from datetime import date
import os
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4

import pytest

from pipelines.certified_research.reference_sql import sec_source_evidence
from pipelines.certified_research.handlers import PreparedSecSourceHandlers,ReferenceHandlers
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit


def test_sec_fingerprint_binds_identity_range_and_original_proof_in_one_scan():
    query=Mock(return_value=[dict(n=4,unique_keys=4,h0=1,h1=2,h2=3,h3=4)])
    start=date(2025,1,1);end=date(2026,1,2)
    first=sec_source_evidence(query,1,'320193',start,end)
    assert first==sec_source_evidence(query,1,'0000320193',start,end)
    assert query.call_args.args[0].startswith('SELECT count()')
    assert 'source_record,source_hash,proof' in query.call_args.args[0]
    assert first['source_hash']!=sec_source_evidence(query,2,'320193',start,end)['source_hash']
    query.return_value=[dict(n=4,unique_keys=4,h0=1,h1=2,h2=3,h3=5)]
    assert first['source_hash']!=sec_source_evidence(query,1,'320193',start,end)['source_hash']
    query.return_value=[dict(n=0,unique_keys=0,h0=0,h1=0,h2=0,h3=0)]
    with pytest.raises(ValueError,match='empty'):sec_source_evidence(query,1,'320193',start,end)


def test_sec_handler_rejects_frozen_source_drift_and_closes_client():
    args=dict(entity_key=1,cik='320193',start='2025-01-01',end='2026-01-02',source_hash='a'*64)
    plan=PlannedUnit(Unit('sec','sec_acquisition'),args)
    client=SimpleNamespace(query=Mock(),close=Mock())
    handler=PreparedSecSourceHandlers(SimpleNamespace(check=Mock()),lambda:client,Mock())
    with patch('pipelines.certified_research.schema.verify_auxiliary_storage'),patch(
            'pipelines.certified_research.reference_sql.sec_source_evidence',return_value=dict(source_hash='b'*64)):
        with pytest.raises(ValueError,match='changed'):handler.verifier(plan,{})
    client.close.assert_called_once()


def test_fundamental_consumer_requires_matching_verified_sec_receipt():
    args=dict(entity_key=1,cik='320193',start='2025-01-01',end='2026-01-02',source_hash='a'*64)
    plan=PlannedUnit(Unit('fundamentals','fundamentals',('sec',)),args)
    handler=ReferenceHandlers(str(uuid4()),'arte',SimpleNamespace(check=Mock()),Mock(),Mock(),(plan,))
    receipt=dict(product='sec_acquisition',**args)
    assert handler.scope(plan,{'sec':receipt}).parent_hash=='a'*64
    receipt['source_hash']='b'*64
    with pytest.raises(ValueError,match='fingerprint differs'):handler.scope(plan,{'sec':receipt})
    with pytest.raises(ValueError,match='verified SEC'):handler.scope(plan,{'sec':dict(output_hash='a'*64)})


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded ClickHouse opt-in required')
def test_retained_sec_input_freezes_and_verifies_without_writes():
    from scripts import build_market_day as builder
    args=builder.parse_args(['--date','2026-01-02','--max-threads','2','--env-file',str(builder.ROOT/'.env')])
    class Reader:
        def __init__(self):self.client=builder.Client(args,persistent=False)
        def query(self,sql):return self.client.query(sql+' SETTINGS readonly=1')
        def close(self):self.client.close()
    reader=Reader()
    try:evidence=sec_source_evidence(reader.query,1,'320193',date(2025,1,1),date(2026,1,2))
    finally:reader.close()
    plan=PlannedUnit(Unit('sec','sec_acquisition'),dict(entity_key=1,cik='320193',
        start='2025-01-01',end='2026-01-02',source_hash=evidence['source_hash']))
    handler=PreparedSecSourceHandlers(SimpleNamespace(check=lambda:None),Reader,lambda:None)
    receipt=handler.verifier(plan,{})
    assert receipt['output_hash']==evidence['source_hash'] and receipt['rows']>0
