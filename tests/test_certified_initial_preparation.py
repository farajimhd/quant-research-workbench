from dataclasses import replace
from datetime import date
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock,patch
import os

import pytest

from pipelines.certified_research import initial_preparation as prep,recipe
from pipelines.certified_research.contracts import digest
from pipelines.certified_research.identity import ListingInterval,identity_key,ISSUER,SECURITY,LISTING
from pipelines.certified_research.planning import MarketSession,IssuerInput,SecurityInput
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit
from pipelines.certified_research.checkpoints import NY
from datetime import datetime


def inputs():
    from tests.test_certified_program_planning import inputs as base
    values=base();config=values['config'];day=config.start
    rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    frozen=recipe.market_recipe(config,rules)
    interval=ListingInterval('listing','security','issuer','TEST',1,datetime(2025,1,1,tzinfo=NY),datetime(2026,1,6,tzinfo=NY),
        'US','XNAS','USD','STK',True,True,False,False,'a'*64)
    listing=identity_key(LISTING,'listing');issuer=identity_key(ISSUER,'issuer');security=identity_key(SECURITY,'security')
    parents=tuple(replace(p,arguments=dict(entity_key=issuer,cik='123',source_hash='c'*64,start=str(day),end='2026-01-03'))
        if p.unit.product=='sec_acquisition' else replace(p,arguments=dict(p.arguments,entity_key=security,end='2026-01-03'))
        if p.unit.product in ('corporate_actions','float_allocation') else p for p in values['prerequisites']
        if p.unit.product not in ('technical_seed','v7_seed') and p.unit.key not in ('fetch','repair'))
    issuers=(IssuerInput(issuer,'123','c'*64,('sec',)),)
    securities=(replace(values['securities'][0],security_key=security,issuer_key=issuer,market_parents=()),)
    canonical=dict(missing=(),incomplete=(),expected=(day,),source_hashes={str(day):'a'*64},content_hash='b'*64)
    reporting=dict(missing=(),mutating=False,verified={str(day):dict(source_digest='a'*64)},content_hash='c'*64)
    snapshot=dict(ready=True,intervals=(interval,),source_hash='e'*64)
    context=dict(measurements=(),sessions=(),sec_source_hash='c'*64,price_gap_days=7,sec_age_days=520)
    context['context_hash']=digest(context)
    seeds=tuple(PlannedUnit(Unit(k,p),dict(dependency={})) for k,p in (('technical_seed','technical_seed'),('v7_seed','v7_seed')))
    prepared=dict(ready=True,blocked={},failed={},prepared={listing:dict(recipe_hash=frozen['hash'])},
        sessions=(MarketSession(day,listing,'TEST'),),seed_units=seeds,seed_parents={listing:('technical_seed','v7_seed')},
        state_start_dates={listing:(day,day)},required_source_days=(day,),preparation_hash='f'*64)
    arguments=dict(query=Mock(),config=config,claim=Mock(),client_factory=Mock(return_value=SimpleNamespace(query=Mock(),close=Mock())),
        source_guard=Mock(),canonical=canonical,reporting=reporting,definition=frozen['definition'],rules=rules,issuers=issuers,
        securities=securities,prerequisites=parents,universe_parent='universe',corporate_parents={listing:'corporate'},
        adjustment_hashes={},fundamental_start=day,short_start=date(2025,12,1),anchor_date=date(2026,10,13))
    return arguments,snapshot,context,prepared


def test_initial_preparation_feeds_shared_graph_without_publication():
    args,snapshot,context,prepared=inputs()
    with patch.object(prep.sec_coverage,'require_ready'),patch.object(prep.source,'require_ready',return_value='b'*64),\
         patch.object(prep.source,'inventory',return_value=args['canonical']),\
         patch.object(prep.source,'reporting_inventory',return_value=args['reporting']),\
         patch.object(prep.universe,'load',return_value=snapshot),\
         patch.object(prep.float_data,'source_price_sessions',return_value=context) as floats,\
         patch.object(prep.seed_preparation,'prepare_universe',return_value=prepared) as seeds:
        result=prep.prepare(**args)
        graph={p.unit.key:p for p in result['plans']}
        aggregate=next(p for p in graph.values() if p.unit.product=='market_aggregate')
        assert set(aggregate.unit.parents)=={'source:2026-01-02','universe','calculation_recipe'}
        assert floats.call_args.kwargs['measurement_end']==args['config'].end
        assert seeds.call_args.kwargs['price_sessions']=={}
        assert result['float_price_unavailable']==()
        assert all(not call.args[0].startswith(('INSERT','ALTER','CREATE')) for call in args['query'].call_args_list)
    args['client_factory'].return_value.close.assert_called_once()


def test_missing_short_context_or_stop_blocks_before_preparation_pool():
    args,_,_,_=inputs()
    with pytest.raises(ValueError,match='prior sessions'):prep.prepare(**dict(args,short_start=args['config'].start))
    signal=Event();signal.set()
    with patch.object(prep.sec_coverage,'require_ready'),patch.object(prep.source,'require_ready',return_value='b'*64):
        with pytest.raises(InterruptedError):prep.prepare(**dict(args,stop=signal))
    args['client_factory'].assert_not_called()


def test_earlier_float_listing_gets_shared_price_aggregate_without_state_rebuild():
    args,snapshot,context,prepared=inputs();early=date(2025,12,31)
    current=replace(snapshot['intervals'][0],valid_from=datetime(2026,1,2,tzinfo=NY))
    prior=replace(current,listing_id='old-listing',ticker='OLD',valid_from=datetime(2025,1,1,tzinfo=NY),valid_to=datetime(2026,1,1,tzinfo=NY))
    snapshot=dict(snapshot,intervals=(current,prior))
    context=dict(context,measurements=(early,),sessions=(early,));context['context_hash']=digest({k:v for k,v in context.items() if k!='context_hash'})
    canonical=dict(args['canonical'],expected=(early,args['config'].start),source_hashes={str(early):'d'*64,**args['canonical']['source_hashes']})
    reporting=dict(args['reporting'],verified={str(early):dict(source_digest='d'*64),**args['reporting']['verified']})
    order=[]
    preparer=Mock(side_effect=lambda days:order.append('prepare_sources'))
    def inventory(*args,**kwargs):order.append('inventory');return canonical
    def spellings(query,sessions,guard):guard();return dict(sessions=sessions)
    with patch.object(prep.sec_coverage,'require_ready'),patch.object(prep.source,'require_ready',return_value='b'*64),\
         patch.object(prep.source,'inventory',side_effect=inventory),patch.object(prep.source,'reporting_inventory',return_value=reporting),\
         patch.object(prep.universe,'load',return_value=snapshot),patch.object(prep.float_data,'source_price_sessions',return_value=context),\
         patch.object(prep.seed_preparation,'prepare_universe',return_value=prepared),patch.object(prep.source,'resolve_spellings',side_effect=spellings):
        result=prep.prepare(**args,source_preparer=preparer)
    preparer.assert_called_once_with((early,args['config'].start))
    assert order[:2]==['prepare_sources','inventory']
    old=identity_key(LISTING,'old-listing')
    lane=[p for p in result['plans'] if p.arguments.get('listing_key')==old]
    assert len(lane)==1 and lane[0].unit.product=='market_aggregate'
    assert lane[0].arguments['day']==str(early)
    prices=next(p for p in result['plans'] if p.unit.product=='regular_prices')
    assert lane[0].unit.key in prices.unit.parents
    assert prices.arguments['start']==str(early)


@pytest.mark.parametrize('failure',('source_drift','identity_drift','missing_seed'))
def test_initial_preparation_cannot_return_a_plan_after_changed_or_missing_dependencies(failure):
    args,snapshot,context,prepared=inputs()
    epochs=['b'*64]*3+['f'*64] if failure=='source_drift' else ['b'*64]*10
    snapshots=[snapshot,dict(snapshot,source_hash='f'*64)] if failure=='identity_drift' else [snapshot,snapshot]
    if failure=='missing_seed':prepared=dict(prepared,ready=False,states={1:'blocked'})
    with patch.object(prep.sec_coverage,'require_ready'),patch.object(prep.source,'require_ready',side_effect=epochs),\
         patch.object(prep.source,'inventory',return_value=args['canonical']),\
         patch.object(prep.source,'reporting_inventory',return_value=args['reporting']),\
         patch.object(prep.universe,'load',side_effect=snapshots),\
         patch.object(prep.float_data,'source_price_sessions',return_value=context),\
         patch.object(prep.seed_preparation,'prepare_universe',return_value=prepared):
        with pytest.raises(ValueError,match='changed|blocked'):prep.prepare(**args)
    args['client_factory'].return_value.close.assert_called_once()


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded ClickHouse opt-in')
def test_real_initial_preparation_rejects_uncertified_sec_range_without_writers():
    from scripts import build_market_day as builder
    args,_,_,_=inputs();args['config']=replace(args['config'],end=date(2026,10,2))
    client=builder.Client(builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')]),persistent=False)
    logs=[]
    def query(sql):logs.append(sql);return client.query(sql+' SETTINGS readonly=1')
    try:
        with pytest.raises(ValueError,match='SEC acquisition coverage is incomplete'):
            prep.prepare(**dict(args,query=query))
    finally:client.close()
    args['client_factory'].assert_not_called()
    assert logs and all(sql.startswith('SELECT') for sql in logs)
    assert all('sec_xbrl_company_fact_v3' not in sql for sql in logs)


@pytest.mark.parametrize('ready',(False,True))
def test_combined_discovery_routes_only_complete_reference_inputs_into_preparation(ready):
    from pipelines.certified_research import reference_discovery
    args,snapshot,_,_=inputs()
    refs=dict(ready=ready,issuers=args['issuers'],securities=args['securities'],prerequisites=(),
        corporate_parents=args['corporate_parents'],missing_authorities=({'family':'float_allocation'},) if not ready else ())
    with patch.object(prep.source,'require_ready'),patch.object(prep.universe,'load',return_value=snapshot),\
         patch.object(reference_discovery,'discover',return_value=refs),patch.object(prep,'prepare',return_value=dict(plans=())) as prepare:
        result=prep.discover_and_prepare(args['query'],args['config'],'00000000-0000-0000-0000-000000000001',
            args['claim'],args['client_factory'],args['source_guard'],args['canonical'],args['reporting'],args['definition'],args['rules'],
            fundamental_start=args['fundamental_start'],short_start=args['short_start'],anchor_date=args['anchor_date'],adjustment_hashes={})
    assert result['ready']==ready
    assert prepare.call_count==int(ready)
