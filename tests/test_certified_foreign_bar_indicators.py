from dataclasses import replace
from datetime import date,datetime,timezone
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pipelines.certified_research import producer,market,warmup_state
from pipelines.certified_research.adjustments import Adjustment


def test_hourly_only_warmup_recipe_does_not_require_unconsumed_v7_bars():
    from pipelines.certified_research import contracts,recipe
    config=contracts.CampaignConfig(date(2026,1,2),date(2026,1,2),product_scope='market_bars',resolutions_ms=(3600000,))
    rules=({'token_id':1,'modifier_int':0,'update_high_low':1,'update_last':1,'update_volume':1},)
    frozen=recipe.market_recipe(config,rules)
    assert recipe.verify_definition(frozen['definition'],rules)==frozen['hash']
    with pytest.raises(ValueError,match='V7'):replace(config,product_scope='full')


def request():
    unit=market.MarketUnit(str(uuid4()),date(2026,1,2),'TEST',1,str(uuid4()))
    return producer.MarketRequest(unit,'a'*64,'b'*64,({'token_id':1},),{},'c'*64,'d'*64,
        datetime(2026,1,2,21,tzinfo=timezone.utc),True,bars_dataset_id=str(uuid4()),bars_parent_hash='e'*64)


def test_foreign_bar_scope_and_consumer_lineage_bind_exact_authority():
    r=request();scope=r.bar_scope()
    assert scope.dataset_id==r.bars_dataset_id and scope.parent_hash=='e'*64
    assert r.parent('technical')!=replace(r,bars_parent_hash='f'*64).parent('technical')
    assert r.parent('technical')!=replace(r,bars_dataset_id=str(uuid4())).parent('technical')
    with pytest.raises(ValueError,match='parent'):replace(r,bars_dataset_id='')


def test_state_readback_joins_target_indicators_to_exact_foreign_bars():
    r=request();query=Mock(return_value=[])
    assert producer.indicator_state(query,'arte',r.unit,str(uuid4()),{},bar_attempt=str(uuid4()),
        frames=(60000,),bars_dataset_id=r.bars_dataset_id)=={}
    sql=query.call_args.args[0]
    assert "b.dataset_id=toUUID('"+r.bars_dataset_id+"')" in sql
    assert "i.dataset_id=toUUID('"+r.unit.dataset_id+"')" in sql
    assert 'b.bucket_index=i.bucket_index' in sql


def observations():
    return {60000:[dict(session_date='2025-12-31',resolution_ms=60000,bucket_index=i,
        close_int=1000000+i*100,high_int=1000100+i*100,low_int=999900+i*100) for i in range(50)]}


def test_warmup_never_uses_january_or_incomplete_frame_history():
    selected=observations();selected[60000][0]['session_date']='2026-01-02'
    query=Mock()
    adjustment=lambda a,b:Adjustment(1.,'a'*64,'b'*64)
    with pytest.raises(ValueError,match='December'):
        warmup_state.calculate(query,selected,adjustment,dataset_id=str(uuid4()),listing_key=1,ticker='TEST')
    query.assert_not_called()
    selected=observations();selected[60000].pop()
    with pytest.raises(ValueError,match='exactly'):
        warmup_state.calculate(query,selected,adjustment,dataset_id=str(uuid4()),listing_key=1,ticker='TEST')


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded warehouse opt-in')
def test_real_shared_sql_warmup_has_fifty_samples_and_rebases_all_price_state():
    from contextlib import closing
    from scripts import build_market_day as builder
    args=SimpleNamespace(env_file=Path('.env'),max_threads=1,max_memory_gb=1,query_timeout=30)
    with closing(builder.Client(args,persistent=True,readonly=True)) as client:
        results=[]
        for factor in (1.,.5):
            result=warmup_state.calculate(client.query,observations(),lambda a,b:Adjustment(factor,'a'*64,'b'*64),
                dataset_id=str(uuid4()),listing_key=1,ticker='TEST')
            results.append(result['state'][60000])
    assert results[0]['sample_count']==results[1]['sample_count']==50
    expected=100.
    for i in range(1,50):expected+=(2/51)*(100+i*.01-expected)
    assert results[0]['ema_50']==pytest.approx(expected)
    assert results[0]['atr_14']==pytest.approx(.02)
    for name,value in results[0].items():
        if name!='sample_count':assert results[1][name]==pytest.approx(value*.5,abs=1e-10)


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded warehouse opt-in')
def test_real_foreign_bar_publication_and_idempotent_indicator_state():
    from tests.test_certified_research_contracts import ClickHouseIntegration as fixture
    from pipelines.certified_research import recipe,contracts,indicator_state
    from pipelines.market_sip.events import market_day_sql
    from pipelines.certified_research.execution import Unit
    from threading import Event
    fixture.setUpClass()
    try:
        day=date(2026,1,2);frames=(1000,60000)
        config=contracts.CampaignConfig(day,day,resolutions_ms=frames)
        rules=({'token_id':1,'modifier_int':0,'update_high_low':1,'update_last':1,'update_volume':1},)
        frozen=recipe.market_recipe(config,rules)
        base=market.MarketUnit(str(uuid4()),day,'FOREIGN',1,str(uuid4()))
        original=producer.MarketRequest(base,frozen['hash'],'a'*64,rules,{},'b'*64,'c'*64,
            datetime(2026,1,2,21,tzinfo=timezone.utc),True,frozen['definition'],'d'*64)
        class Claim:
            def check(self):pass
        clients=lambda:fixture.builder.Client(fixture.args,persistent=True)
        source=(f"SELECT toUInt64({market_day_sql.bounds(day,'04:05:00')}+number*1000000) sip_timestamp_us,"
            'toUInt64(number) ordinal,toUInt8(65) event_meta,toUInt32(1000000+number*100) price_primary_int,'
            'toUInt32(0) price_secondary_int,toFloat32(1) size_primary,toFloat32(0) size_secondary,'
            +','.join(f'toUInt8(0) condition_token_{i}' for i in range(1,6))+' FROM numbers(60)')
        _,handler=producer.market_execution(original,clients,Claim(),lambda:None,None,
            database=fixture.database,fixture_source=source)
        aggregate=handler(Unit('aggregate','aggregate'),{},Event())
        bars=handler(Unit('bars','bars'),{'aggregate':aggregate},Event())
        foreign=replace(original,unit=replace(base,dataset_id=str(uuid4())),bars_dataset_id=base.dataset_id,
            bars_parent_hash=original.parent('bars'))
        _,consumer=producer.market_execution(foreign,clients,Claim(),lambda:None,None,database=fixture.database)
        result=consumer(Unit('technical','technical'),{'bars':bars},Event())
        assert consumer(Unit('technical','technical'),{'bars':bars},Event())==result
        loaded=indicator_state.load(fixture.client.query,fixture.database,Claim(),
            indicator_state.scope_for(foreign,result['technical_receipt']),foreign.dataset_hash,foreign.prior_indicator_hash,
            source_hash=foreign.source_hash,rules_hash=contracts.digest(rules),frames=frames)
        assert loaded['state'][1000]['sample_count']==60
        assert loaded['state'][60000]['sample_count']==1
    finally:
        fixture.tearDownClass()
