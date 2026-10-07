"""Actual native entry rebind with explicit DB-loader fixture seams only."""
from dataclasses import replace
from datetime import date
from hashlib import sha256
from uuid import uuid4
import json
import re

import pytest

from test_backtest_strategy_episode_activity_source import authority
from test_strategy_one_intent import _proposal
from test_strategy_one_position import level
from src.backend import backtest_fixed_structural_lot_source as owner
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority, bind_certified_price_break_proposal
from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal, certified_episode_entry_intent
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan, CertifiedV7IntervalUnit
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.fixed_structural_lot_entry import _same_typed
from src.trading_runtime.strategy_one_v7_intervals import V7IntervalProjector, clock_hash, interval_hash
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
from src.trading_runtime.journal_contract import canonical_json


def prepared(monkeypatch, *, target_centers=((1,10.1),(2,10.2),(3,12.),(4,13.),(5,14.))):
    run = str(uuid4())
    episode = replace(authority(number=42), run_id=run)
    market = episode.plan.market
    broker=replace(market.units[0],stage='broker_100ms',
        attempt_id=episode.plan.parent.candidates.coverage[0].source_attempts[2])
    market=replace(market,units=(*market.units,broker))
    # The fixture's activity/price market remains content-equivalent by token;
    # native loader verifies actual market storage in production.
    price = CertifiedPriceReadbackAuthority(run, episode.plan.parent, episode)
    day = date.fromisoformat(market.sessions[0])
    original = replace(_proposal(), strategy_number=18, boundary_ms=41000,target_level_id='R3',bos_support_level_id='R3',
        momentum=price.plan.momentum.lookup('AAA',41000),
        initial_momentum=price.plan.source.parent.selection_witness('AAA',41000))
    proposal = bind_episode_activity_proposal(price,
        bind_certified_price_break_proposal(price.plan, original, strategy_number=36), session_date=day)
    payload = {'strategy': {'strategy_id':'early-squeeze-strategy','revision':42,
        'strategy_number':42,'parameters':{'execution':{'tick_size':.01}}},
        'accounts': {'bindings': [{'account_key':'account','currency':'USD'}]}}
    parent = CertifiedStrategyOneConfiguration(str(uuid4()), sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash(encode_nodes(payload)), 'candidate', 'c'*64, 'd'*64, payload)
    projector = V7IntervalProjector()
    from src.backend.backtest_market_data import market_day_boundary
    origin = int(market_day_boundary(day,0).timestamp()*1000)
    rows = [dict(level(index,center), unified_level_id=f'R{index}', historical=True,
        confirmed_at_ms=origin, book_version='causal-level-book-v7-mle-1')
        for index,center in target_centers]
    projector.observe(boundary_ms=0, levels=rows, valid_completed_second=False)
    projector.observe(boundary_ms=41000, levels=rows, valid_completed_second=True)
    clocks, intervals = projector.finish()
    unit = CertifiedV7IntervalUnit('AAA',str(uuid4()),str(uuid4()),'1'*64,'2'*64,'3'*64,'4'*64,
        'legacy-unfiltered',len(clocks),len(intervals),clock_hash(clocks),interval_hash(intervals))
    product = CertifiedV7IntervalPlan(market.build_id,day.isoformat(),(unit,),(('AAA',clocks),),
        (('AAA',intervals),),'5'*64)
    calls = {'configuration':0,'product':0,'full_children':0}
    query_log=[]
    class NativeQuotes:
        def execute(self,sql):
            query_log.append(sql)
            from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS
            values=[]
            for ticker,bucket,attempt in re.findall(r"\('([^']+)',(\d+),toUUID\('([^']+)'\)\)",sql):
                boundary=(int(bucket)+1)*100-SESSION_OPEN_OFFSET_MS
                values.append(dict(ticker=ticker,bucket_index=int(bucket),liquidity_attempt_id=attempt,
                    bid_int=100000,ask_int=100100,quote_timestamp_us=origin*1000+boundary*1000-1000,quote_valid=1))
            return '\n'.join(json.dumps(row) for row in values)
    def configuration(client, number):
        assert number == 42
        calls['configuration'] += 1
        return parent
    def product_loader(*args, **kwargs):
        assert kwargs['candidate_tickers'] == market.tickers
        calls['product'] += 1
        return product
    validate = owner._validate_interval_ticker
    def full_children(*args, **kwargs):
        calls['full_children'] += 1
        return validate(*args,**kwargs)
    monkeypatch.setattr(owner,'certify_numbered_configuration',configuration)
    monkeypatch.setattr(owner,'certify_v7_interval_plan',product_loader)
    monkeypatch.setattr(owner,'_validate_interval_ticker',full_children)
    monkeypatch.setattr(owner,'verify_market_day_plan',lambda *args,**kwargs:None)
    source = owner.prepare_fixed_structural_lot_source(NativeQuotes(),run_id=run,parent_number=42,
        session_date=day,policy=FixedStructuralLotPolicy(),market=market,seeds=object(),price_authority=price)
    return source, proposal, calls


def test_actual_native_factory_fields_and_preparation_once(monkeypatch):
    source, proposal, calls = prepared(monkeypatch)
    for assignment in ('assignment-1','assignment-2','assignment-3'):
        changed = replace(proposal,assignment_id=assignment)
        request = source.request(changed)
        expected = certified_episode_entry_intent(source.price_authority,changed,session_date=source.session_date)
        assert _same_typed(request.original,expected)
        assert _same_typed(replace(request.intent,intent_id=expected.intent_id,
            protection_profile=expected.protection_profile),expected)
        assert [t.price for t in request.entry.targets] == [12.,13.,14.]
        request.verify()
    assert calls == {'configuration':1,'product':1,'full_children':1}
    assert source.quotes[0].source_build_id == 'c'*64
    with pytest.raises(ValueError,match='installed own'):
        source.require_installed_admission()


@pytest.mark.parametrize('change',['ask','price','episode','clock','momentum'])
def test_actual_native_source_rebind_rejects_carried_fact_drift(monkeypatch,change):
    source, proposal, _ = prepared(monkeypatch)
    changes = {'ask':dict(reference_ask=10.02),
        'price':dict(first_price=replace(proposal.first_price,current_close_int=999)),
        'episode':dict(episode_start_ms=30001),'clock':dict(boundary_ms=41100),
        'momentum':dict(momentum=replace(proposal.momentum,boundary_ms=41100))}
    with pytest.raises(ValueError):
        source.request(replace(proposal,**changes[change]))


@pytest.mark.parametrize('change',['clone','plan','configuration','tick','count'])
def test_resealed_or_forged_prepared_source_never_issues_capability(monkeypatch,change):
    source, proposal, _ = prepared(monkeypatch)
    with pytest.raises(ValueError):
        if change == 'configuration':
            payload = source.selected_payload
            payload['inherited_configuration']['accounts']['bindings'][0]['currency']='EUR'
            encoded=canonical_json(payload)
            forged=replace(source,selected_json=encoded,selected_configuration_hash=sha256(encoded.encode()).hexdigest())
        elif change == 'tick':
            forged=replace(source,tick=.02)
        elif change == 'count':
            forged=replace(source,validated_ticker_count=True)
        elif change == 'plan':
            forged=replace(source,intervals=replace(source.intervals,token='f'*64))
        else:
            forged=replace(source)
        forged.request(proposal)


def test_parent_and_selected_configuration_copies_cannot_mutate_capability(monkeypatch):
    source, proposal, _ = prepared(monkeypatch)
    source.parent_payload['strategy']['parameters']['execution']['tick_size']=.02
    source.selected_payload['fixed_structural_lot_policy']['count']=32
    assert source.request(proposal).entry.tick == .01
