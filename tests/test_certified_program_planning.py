"""Combined execution dependencies, with shared calculations and failure gates."""
from dataclasses import replace
from datetime import date

import pytest

from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.execution import Unit,execute
from pipelines.certified_research.planning import MarketSession,IssuerInput,SecurityInput,program_units


def inputs():
    day=date(2026,1,2)
    config=CampaignConfig(day,day)
    prerequisites=[]
    def add(key,product,parents=(),**arguments):
        prerequisites.append(PlannedUnit(Unit(key,product,parents),arguments))
    add('fetch','event_acquisition')
    add('repair','event_repair',('fetch',))
    add('universe','universe')
    add('sec','sec_acquisition',('universe',))
    add('technical_seed','technical_seed');add('v7_seed','v7_seed')
    add('corporate','corporate_actions',entity_key=101,start=str(day),end=str(day))
    add('allocation','float_allocation',entity_key=101,start=str(day),end=str(day))
    add('sv','short_volume_acquisition');add('si','short_interest_acquisition')
    security=SecurityInput(101,1,('market:11:2026-01-02:aggregate',),'corporate','allocation',
        'a'*64,'b'*64,('sv',),('si',))
    return dict(config=config,sessions=(MarketSession(day,11,'TEST'),),
        issuers=(IssuerInput(1,'123','c'*64,('sec',)),),securities=(security,),
        prerequisites=tuple(prerequisites),source_parents={day:('repair',)},universe_parent='universe',
        seed_parents={11:('technical_seed','v7_seed')},corporate_parents={11:'corporate'},
        security_listings={101:(11,)},fundamental_start=day,price_start=day,short_start=day)


def test_combined_execution_reuses_aggregate_and_blocks_only_failed_descendants():
    arguments=inputs();plans=program_units(**arguments)
    by_key={p.unit.key:p for p in plans}
    aggregate='market:11:2026-01-02:aggregate'
    assert by_key[aggregate].unit.parents==('repair','universe')
    assert by_key['security:101:prices'].unit.parents==(aggregate,)
    assert by_key['market:11:2026-01-02:technical'].unit.parents[0]==aggregate
    assert by_key['market:11:2026-01-02:v7'].unit.parents[0]==aggregate
    calls=[]
    def run(unit,parents,stop):
        calls.append(unit.key)
        assert set(parents)==set(unit.parents)
        return {'output_hash':unit.key}
    handlers={p.unit.product:run for p in plans}
    result=execute(tuple(p.unit for p in plans),handlers,workers=4,
        memory_bytes=arguments['config'].global_memory_bytes)
    assert result['status']=='complete' and calls.count(aggregate)==1
    assert calls.index('fetch')<calls.index('repair')<calls.index(aggregate)
    def fail(*args):raise ValueError('Original reporting evidence is missing')
    result=execute(tuple(p.unit for p in plans),dict(handlers,event_repair=fail),workers=4,
        memory_bytes=arguments['config'].global_memory_bytes)
    assert result['units'][aggregate]=='blocked'
    assert result['units']['security:101:float']=='blocked'
    assert result['units']['issuer:1:derived']=='certified'


@pytest.mark.parametrize('failure',('missing_source','wrong_prices','wrong_owner','missing_seed'))
def test_combined_plan_rejects_incomplete_or_competing_dependencies(failure):
    arguments=inputs()
    if failure=='missing_source':
        arguments['prerequisites']=tuple(p for p in arguments['prerequisites'] if p.unit.key!='repair')
    elif failure=='wrong_prices':
        arguments['securities']=(replace(arguments['securities'][0],market_parents=('other',)),)
    elif failure=='wrong_owner':arguments['security_listings']={101:(12,)}
    else:arguments['seed_parents']={11:('missing','v7_seed')}
    with pytest.raises(ValueError):program_units(**arguments)


def test_historical_listing_change_reuses_same_security_without_duplicate_day_prices():
    arguments=inputs();second=date(2026,1,5)
    arguments['config']=replace(arguments['config'],end=second)
    arguments['sessions']+= (MarketSession(second,12,'RENAMED'),)
    arguments['security_listings']={101:(11,12)}
    arguments['source_parents'][second]=('repair',)
    arguments['seed_parents'][12]=('technical_seed','v7_seed')
    arguments['corporate_parents'][12]='corporate'
    for key in ('corporate','allocation'):
        arguments['prerequisites']=tuple(replace(p,arguments=dict(p.arguments,end=str(second)))
            if p.unit.key==key else p for p in arguments['prerequisites'])
    arguments['securities']=(replace(arguments['securities'][0],market_parents=(
        'market:11:2026-01-02:aggregate','market:12:2026-01-05:aggregate')),)
    plans=program_units(**arguments)
    prices=next(p for p in plans if p.unit.product=='regular_prices')
    assert prices.unit.parents==arguments['securities'][0].market_parents
    # Overlapping admitted listings require identity resolution, not two closes.
    arguments['sessions']+=(MarketSession(second,11,'TEST'),)
    arguments['securities']=(replace(arguments['securities'][0],market_parents=(
        *prices.unit.parents,'market:11:2026-01-05:aggregate')),)
    with pytest.raises(ValueError,match='Competing market listings'):program_units(**arguments)
