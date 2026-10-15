from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from pipelines.market_sip.events import shared_reference_listing_basis as b
from src.market_engine.opening_price_reference import previous_regular_session


def packet():
    identity = dict(ticker='GORO', symbol_id='symbol', listing_id='gold-resource',
        security_id='resource-security', ibkr_conid='40743577',
        source_run_id='current-capture', source_inserted_at='2026-07-30 07:54:15.592')
    provenance = dict(source_table='q_live.feature_tradable_universe_v1',
        source_event_id='retained', source_evidence_ref='reference:retained',
        source_content_sha256='a' * 64, source_run_id='reference-run',
        inserted_at='2026-07-01 00:00:00')
    keys = dict(provider_ticker='GORO', symbol_id='symbol',
        listing_id='gold-resource', security_id='resource-security')
    routing = [dict(provenance, **keys, routing_fact_id='route',
        ibkr_conid='40743577', broker='ibkr', sec_type='STK', currency_code='USD', routing_status='valid',
        valid_from_utc='2026-07-01 00:00:00', valid_to_utc=None)]
    tradability = [dict(provenance, **keys, tradability_fact_id='tradable',
        effective_at_utc='2026-07-01 00:00:00', observed_at_utc='2026-07-01 00:00:00',
        is_tradable=1, block_status='tradable')]
    retained = [dict(universe_date=day, ticker='GORO', symbol_id='symbol',
        listing_id='gold-resource', security_id='resource-security', ibkr_conid='40743577',
        is_tradable=1, source_run_id=run, inserted_at=stamp)
        for day, run, stamp in (
            ('2026-07-29', 'prior-capture', '2026-07-29 08:02:04.002'),
            ('2026-07-30', 'current-capture', identity['source_inserted_at']))]
    return identity, routing, tradability, retained


def resolve(parts):
    return b.resolve_listing(*parts, day=date(2026, 7, 30),
        regular=previous_regular_session(date(2026, 7, 30)),
        cutoff=datetime(2026, 7, 30, 8, tzinfo=timezone.utc))


def test_prior_capture_after_premarket_but_before_rth_is_not_backdated():
    listing = resolve(packet())
    assert listing.listing_id == 'gold-resource'
    assert listing.valid_from == date(2026, 7, 29)
    assert listing.valid_to_exclusive == date(2026, 7, 31)
    assert listing.identity_interval_id == 'shared-reference:' + listing.identity_source_hash
    assert resolve(packet()) == listing


@pytest.mark.parametrize('case', ['collision', 'conid', 'prior_late', 'current_late',
    'missing_prior', 'duplicate_prior', 'blocked', 'expired', 'late_route', 'changed_capture',
    'currency', 'broker', 'boolean'])
def test_invalid_reference_proof_fails_closed(case):
    parts = deepcopy(packet())
    identity, routes, states, retained = parts
    if case == 'collision': routes[0]['security_id'] = 'goldgroup-security'
    elif case == 'conid': routes[0]['ibkr_conid'] = '587763590'
    elif case == 'prior_late': retained[0]['inserted_at'] = '2026-07-29 13:30:00'
    elif case == 'current_late': retained[1]['inserted_at'] = '2026-07-30 08:00:00'
    elif case == 'missing_prior': retained.pop(0)
    elif case == 'duplicate_prior': retained.append(deepcopy(retained[0]))
    elif case == 'blocked':
        states.append(dict(states[0], tradability_fact_id='blocked', is_tradable=0,
            block_status='blocked', effective_at_utc='2026-07-29 14:00:00',
            observed_at_utc='2026-07-29 14:00:00', inserted_at='2026-07-29 14:00:00'))
    elif case == 'expired': routes[0]['valid_to_utc'] = '2026-07-30 07:59:00'
    elif case == 'late_route': routes[0]['inserted_at'] = '2026-07-30 07:59:00'
    elif case == 'changed_capture': retained[1]['source_run_id'] = 'another-source'
    elif case == 'currency': routes[0]['currency_code'] = 'CAD'
    elif case == 'broker': routes[0]['broker'] = 'another-broker'
    elif case == 'boolean': states[0]['is_tradable'] = True
    with pytest.raises(ValueError): resolve(parts)


def test_changed_provenance_changes_proof_and_duplicate_latest_state_is_rejected():
    parts = deepcopy(packet())
    before = resolve(parts)
    parts[1][0]['source_content_sha256'] = 'b' * 64
    assert resolve(parts).identity_source_hash != before.identity_source_hash
    parts[2].append(deepcopy(parts[2][0]))
    with pytest.raises(ValueError, match='ambiguous'): resolve(parts)


def test_bounded_loader_pins_identity_and_cold_reloads_exact_sources():
    parts = packet()
    market = SimpleNamespace(sessions=('2026-07-30',), tickers=('GORO',))
    seen = []
    def read(reader, query, *, bound):
        assert reader is owner
        seen.append(query)
        assert bound == 4097 and 'LIMIT 4097' in query
        assert 'gold-resource' in query and 'resource-security' in query and 'symbol' in query
        if 'security_routing_fact_v1' in query: return parts[1]
        if 'security_tradability_fact_v1' in query: return parts[2]
        if 'feature_tradable_universe_v1' in query: return parts[3]
        raise AssertionError(query)
    owner = object()
    expected = (resolve(parts),)
    assert b.load_listings(owner, market, {'GORO': parts[0]}, read_rows=read) == expected
    assert b.load_listings(owner, market, {'GORO': parts[0]}, read_rows=read) == expected
    assert len(seen) == 6
    parts[2][0]['block_status'] = 'blocked'
    with pytest.raises(ValueError, match='tradability'):
        b.load_listings(owner, market, {'GORO': parts[0]}, read_rows=read)


def test_native_opening_builder_reverifies_shared_basis_and_retains_parent_checks(monkeypatch):
    from dataclasses import asdict
    from pipelines.market_sip.events import opening_price_reference_producer as p
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
    from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
    from src.trading_runtime.historical_reference_identity import ReferencePin
    uid = '00000000-0000-0000-0000-000000000001'
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), 'current', 'a'*64,
        ('2026-07-30',), ('GORO',), (), (1000,), 'b'*64)
    unit = MarketDayUnit('prior', '2026-07-29', 'GORO', 'bars', uid, 'c'*64, 2, '123')
    prior = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), 'prior', 'd'*64,
        ('2026-07-29',), ('GORO',), (unit,), (1000,), 'e'*64)
    identity = CertifiedIdentityPlan('current', '2026-07-30', uid, market.token,
        ('GORO',), (40743577,), 'f'*64, '1'*64)
    pin = ReferencePin('snapshot', 'revision', '2'*64,
        '2026-07-30 07:55:00', '2026-07-30 08:00:00')
    parts = packet()
    monkeypatch.setattr(p, 'certify_identity_plan', lambda *args, **kwargs: identity)
    monkeypatch.setattr(p, 'load_reference_pin', lambda *args: pin)
    monkeypatch.setattr(p, '_identity_rows', lambda *args: {'GORO': parts[0]})
    parent_reads = []
    monkeypatch.setattr(p, '_verify_market_batch', lambda *args: parent_reads.append(args[3]))
    def read(reader, query, *, bound=25001):
        if 'FROM system.tables' in query:
            names = [('arte','bars_v1'), ('q_live','id_symbol_interval_v1'),
                ('q_live','market_stock_split_v1'), *[('q_live', name) for name in b.TABLES]]
            return [dict(database=db, name=name, engine='MergeTree', storage_policy='live_market_ssd')
                for db,name in names]
        if 'FROM system.parts' in query: return []
        if 'security_routing_fact_v1' in query: return parts[1]
        if 'security_tradability_fact_v1' in query: return parts[2]
        if 'feature_tradable_universe_v1' in query: return parts[3]
        raise AssertionError(query)
    monkeypatch.setattr(p, '_rows', read)
    source = p.certified_opening_input(object(), market, prior, listing_basis=b.BASIS)
    assert source.population_reference_hash == p.contract.content_hash(asdict(pin))
    assert source.listings == (resolve(parts),)
    assert parent_reads == ['2026-07-29']
    source.verify(object())
    assert parent_reads == ['2026-07-29', '2026-07-29']
    parts[2][0]['block_status'] = 'blocked'
    with pytest.raises(ValueError, match='tradability'): source.verify(object())


@pytest.mark.parametrize('failure', [None,'late','ambiguous','security','capture','conid','blocked'])
def test_feature_source_day_needs_only_current_timely_capture(failure):
    fact,routes,states,retained=deepcopy(packet())
    retained=retained[1:]
    if failure=='late': routes[0]['inserted_at']='2026-07-30 08:00:00'
    if failure=='ambiguous': routes.append(deepcopy(routes[0]))
    if failure=='security': states[0]['security_id']='other'
    if failure=='capture': retained[0]['source_run_id']='other'
    if failure=='conid': routes[0]['ibkr_conid']='99'
    if failure=='blocked': states[0]['is_tradable']=0
    if failure:
        with pytest.raises(ValueError): b.source_day_proof(fact,routes,states,retained,day=date(2026,7,30))
    else:
        proof=b.source_day_proof(fact,routes,states,retained,day=date(2026,7,30))
        assert proof['basis']==b.FEATURE_BASIS and len(proof['retained'])==1


def test_feature_identity_cache_certifies_all_three_days_and_historical_alias(monkeypatch):
    from pipelines.market_sip.events import qmd_feature_preparation as q
    from pipelines.market_sip.events import opening_price_reference_producer as opening
    from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
    from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
    from src.market_engine.opening_price_reference import OpeningListing
    days=('2026-07-30','2026-07-31','2026-08-03')
    plans=[CertifiedMarketDayPlan(ExecutionInterval.fixed(100), 'build', 'a'*64,
        (day,),('OLD' if i==0 else 'GORO',),(),(100,),'b'*64) for i,day in enumerate(days)]
    row=OpeningListing('gold-resource','GORO','symbol','resource-security','proof','c'*64,date(2026,7,31),None)
    request=SimpleNamespace(opening_source=SimpleNamespace(listing_basis=b.BASIS,market=plans[-1],listings=(row,)),
        continuity=tuple((p,None) for p in plans),prior_volume=(plans[1],None),tickers=('GORO',))
    certified=[]; facts={}; packets={}
    for i,plan in enumerate(plans):
        fact,routes,states,retained=deepcopy(packet());ticker=plan.tickers[0]
        fact.update(ticker=ticker,symbol_id='old-symbol' if i==0 else 'symbol',source_inserted_at=days[i]+' 07:00:00')
        for group in (routes,states):
            for item in group:item.update(provider_ticker=ticker,symbol_id=fact['symbol_id'])
        retained=[dict(retained[-1],universe_date=days[i],ticker=ticker,symbol_id=fact['symbol_id'],inserted_at=fact['source_inserted_at'])]
        facts[days[i]]={ticker:fact};packets[days[i]]=(routes,states,retained)
    def certify(plan,**kw):
        certified.append(plan.sessions[0]);return CertifiedIdentityPlan('build',plan.sessions[0],
            '00000000-0000-0000-0000-000000000001',plan.token,plan.tickers,(40743577,),'d'*64,'e'*64)
    monkeypatch.setattr(opening,'source_storage_preflight',lambda *a,**kw: None)
    monkeypatch.setattr(opening,'certify_identity_plan',certify)
    monkeypatch.setattr(opening,'_identity_rows',lambda reader,plan,identity:facts[plan.sessions[0]])
    def read(reader,sql,bound=25001):
        if 'system.tables' in sql:return [{'name':'strategy_one_identity_v3'}]
        day=next(day for day in days if day in sql)
        if 'FROM arte.strategy_one_identity_v3' in sql:return list(facts[day].values())
        for table,packet_rows in zip(b.TABLES,packets[day],strict=True):
            if table in sql:return deepcopy(packet_rows)
        raise AssertionError(sql)
    monkeypatch.setattr(q,'_rows',read)
    cache=q._source_identity_cache(object(),request)
    assert certified==list(days)
    ticker,deps=q._feature_identity(None,row,date(2026,7,30),cache)
    assert ticker=='OLD' and len(deps)==4
    assert {v.source_table for v in deps}=={'strategy_one_identity_v3',*b.TABLES}
    first={v for _,rows in cache.values() for item in rows.values() for v in item[1]}
    from src.market_engine.feature_source_plan import FeatureDependency
    universe=FeatureDependency('universe','q_live','feature_tradable_universe_snapshot_v2',date(2026,8,3),'','snapshot',1,'a'*64)
    prepared=SimpleNamespace(request=request,plan=SimpleNamespace(dependencies=tuple(first)+(universe,),algorithm_hash=q.algorithm_hash()),references={})
    monkeypatch.setattr(q,'_placement',lambda reader:None)
    monkeypatch.setattr(q,'_population',lambda *args:((),(),'a'*64,'snapshot'))
    ownership=SimpleNamespace(assert_owner=lambda:None)
    q.PreparedQmdFeatureDay.verify_sources(prepared,object(),ownership=ownership)
    packets[days[0]][0][0]['source_content_sha256']='f'*64
    reloaded=q._source_identity_cache(object(),request)
    second={v for _,rows in reloaded.values() for item in rows.values() for v in item[1]}
    assert first!=second and len(first)==12 and len(second)==12
    with pytest.raises(ValueError,match='identity changed'):
        q.PreparedQmdFeatureDay.verify_sources(prepared,object(),ownership=ownership)
    facts[days[0]]['OLD']['security_id']='wrong'
    with pytest.raises(ValueError,match='2026-07-30.*GORO'):q._source_identity_cache(object(),request)
    facts[days[0]]['OLD']['security_id']='resource-security'
    facts[days[-1]].clear()
    with pytest.raises(ValueError,match='missing|changed'):
        q._source_identity_cache(object(),request)
    with pytest.raises(ValueError,match='2026-08-03.*GORO'):
        q._feature_identity(None,row,date(2026,8,3),{})


def test_legacy_feature_identity_dispatch_remains_interval_owned(monkeypatch):
    from pipelines.market_sip.events import qmd_feature_preparation as q
    request=SimpleNamespace(opening_source=SimpleNamespace(listing_basis='symbol-intervals-v1'))
    assert q._source_identity_cache(None,request) is None
    calls=[]
    monkeypatch.setattr(q,'_listing_identity',lambda *args:calls.append(args) or [{'ticker_normalized':'OLD'}])
    listing=SimpleNamespace(ticker='NEW',listing_id='stable')
    ticker,deps=q._feature_identity(None,listing,date(2026,7,30),None)
    assert ticker=='OLD' and len(calls)==1 and deps[0].source_table=='id_symbol_interval_v1'
