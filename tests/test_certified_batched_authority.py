import os
from datetime import date
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pipelines.certified_research import schema
from pipelines.certified_research.publication import Publisher,Scope


def test_batched_receipts_reject_corruption_missing_evidence_and_duplicates():
    claim=Mock();scope=Scope(str(uuid4()),date(2026,1,2),1,'bars','a'*64)
    from pipelines.certified_research.contracts import digest
    proof=dict(n=1,unique_keys=1,h0=1,h1=2,h2=3,h3=4)
    row=dict(dataset_id=scope.dataset_id,session_date=str(scope.session_date),listing_key=1,
             product='bars',parent_hash=scope.parent_hash,status=1,attempt_id=str(uuid4()),
             output_rows=1,output_hash=digest(proof))
    publisher=object.__new__(Publisher);publisher.database='arte';publisher.claim=claim
    query=Mock(side_effect=[[row],[dict(product='bars',**proof)]])
    publisher.query=query
    assert publisher.market_receipts((scope,))=={'bars':row}
    assert query.call_count==2
    for rows,evidence in (([row,row],[]),([row],[]),([row],[dict(product='bars',**dict(proof,h0=9))]),
                          ([row],[dict(product='bars',**dict(proof,unique_keys=0))])):
        publisher.query=Mock(side_effect=[rows,evidence])
        with pytest.raises(ValueError):publisher.market_receipts((scope,))


def test_batched_storage_reads_are_fresh_not_cached():
    query=Mock(return_value=[]);wrapped=schema.BatchedStorageQuery(query)
    for _ in range(2):
        with pytest.raises(ValueError,match='policy'):schema.verify_storage(wrapped)
    assert query.call_count==2
    assert ' UNION ALL ' in query.call_args.args[0]


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded warehouse opt-in')
def test_real_batched_storage_and_market_receipts_match_original_and_detect_tamper():
    from tests.test_certified_research_contracts import ClickHouseIntegration as fixture
    from pipelines.certified_research import producer,market
    from pipelines.certified_research.execution import Unit
    from pipelines.market_sip.events import market_day_sql as core
    from datetime import datetime,timezone
    from threading import Event
    fixture.setUpClass()
    try:
        query=fixture.client.query;wrapped=schema.BatchedStorageQuery(query)
        assert schema.verify_storage(wrapped,fixture.database,require_tables=True)==schema.verify_storage(query,fixture.database,require_tables=True)
        schema.verify_auxiliary_storage(wrapped,fixture.database,tuple(market.STAGING.values()))
        class Claim:
            def check(self):pass
        base=market.MarketUnit(str(uuid4()),date(2026,1,2),'BATCH',1,str(uuid4()))
        request=producer.MarketRequest(base,'a'*64,'b'*64,({'token_id':1,'modifier_int':0,'update_high_low':1,'update_last':1,'update_volume':1},),
            {},'c'*64,'d'*64,datetime(2026,1,2,21,tzinfo=timezone.utc),True)
        source=(f'SELECT toUInt64({core.bounds(base.day,"04:05:00")}+number*1000000) sip_timestamp_us,'
            'toUInt64(number) ordinal,toUInt8(65) event_meta,toUInt32(1000000) price_primary_int,'
            'toUInt32(0) price_secondary_int,toFloat32(1) size_primary,toFloat32(0) size_secondary,'
            +','.join(f'toUInt8(0) condition_token_{i}' for i in range(1,6))+' FROM numbers(10)')
        def clients():
            client=fixture.builder.Client(fixture.args,persistent=True)
            client.query=schema.BatchedStorageQuery(client.query)
            return client
        _,handler=producer.market_execution(request,clients,Claim(),lambda:None,None,database=fixture.database,fixture_source=source)
        stage=handler(Unit('aggregate','aggregate'),{},Event())
        for kind in ('bars','broker_100ms'):handler(Unit(kind,kind),{'aggregate':stage},Event())
        scopes=tuple(Scope(base.dataset_id,base.day,1,kind,request.parent(kind)) for kind in ('bars','broker_100ms'))
        publisher=Publisher(wrapped,fixture.database,Claim())
        assert publisher.market_receipts(scopes)=={s.product:publisher.receipt(s) for s in scopes}
        query(f'INSERT INTO {fixture.database}.research_bars_v1 SELECT * FROM {fixture.database}.research_bars_v1 LIMIT 1',read=False)
        with pytest.raises(ValueError,match='Duplicate'):publisher.market_receipts(scopes)
    finally:fixture.tearDownClass()
