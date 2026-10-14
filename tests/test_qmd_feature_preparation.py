"""Exercise native producer composition and the app's existing input consumer."""
import asyncio
from dataclasses import replace
from datetime import date
from threading import get_ident
from types import MappingProxyType
import numpy as np
import polars as pl
import pytest

from pipelines.market_sip.events import qmd_feature_preparation as prep
from pipelines.market_sip.events.program_input_publication import ProgramInputPublication
from src.trading_runtime.arte_market_day_certification import MarketDayCertificate
from src.trading_runtime.arte_market_day_cold_preflight import MarketDayColdAudit
from src.market_engine import columnar_history_product as product
from src.market_engine.feature_source_plan import FeatureDependency,FeatureSourcePlan
from src.backend.qmd_columnar_history import CertifiedHistoryProduct
from src.backend.qmd_columnar_history_transport import decode_window
from src.backend.backtest_program_inputs import prepare_program_block
from src.trading_runtime.columnar_rule_program import InputField,RuleNode,RuleProgram,Operation
from src.trading_runtime.columnar_strategy_release import ProgramRelease
from test_columnar_history_publication import source
from test_qmd_extended_features import candles,FUNDAMENTAL_NAMES
from test_qmd_feature_source_publication import Store,Client,Owner
from test_qmd_columnar_history_transport import response_for


def audit(market):
    certificate=MarketDayCertificate(market.build_id,market.definition_hash,
        tuple((market.sessions[0],t) for t in market.tickers),
        tuple((u.session_date,u.ticker,u.stage,u.attempt_id,u.output_rows,u.output_hash) for u in market.units),{}, {})
    return MarketDayColdAudit(certificate,None,True,{},True,True,True,False)


def request():
    original=source(400);root=original.opening_source
    technical=replace(root.market.units[0],stage='technical')
    market=replace(root.market,units=root.market.units+(technical,))
    root=replace(root,market=market)
    current=audit(market);prior=audit(root.prior_market)
    return original,prep.QmdFeaturePreparationRequest(root,current,((market,current),),
        (root.prior_market,prior),('ABC',),14400,14800,10000000,10000000)


def test_native_funnel_prepares_once_seals_sources_and_reaches_app_polars(monkeypatch):
    old,req=request();main_thread=get_ident();calls=[];store=Store()
    # Only external database boundaries are fixtures; request admission,
    # calculations, source publication and app transport/evaluation are native.
    monkeypatch.setattr(prep,'_placement',lambda r:None)
    monkeypatch.setattr(prep,'_population',lambda r,q:(('ABC',),(),'f'*64,'snapshot'))
    b=candles()
    def read_unit(reader,market,ticker,stage,columns,**kw):
        assert get_ident()!=main_thread
        unit=next(u for u in market.units if u.stage==stage)
        if stage=='technical':
            frame=b.select('bucket_index').with_columns(*(pl.lit(1.).alias(v) for v in prep.INDICATORS[1:]))
        else:frame=b
        return frame.select(columns),unit
    monkeypatch.setattr(prep,'_read_unit',read_unit)
    monkeypatch.setattr(prep,'read_reference',lambda *args:(None,[],{k:0. for k in FUNDAMENTAL_NAMES},
        dict(float=None,splits=[],hash='c'*64)))
    monkeypatch.setattr(prep,'_verify_market_batch',lambda *args:calls.append(args[1]))
    anchor=product.session_clock_anchor_us(replace(old.scope('00000000-0000-0000-0000-000000000001','a'*64),first_bucket=14400,stop_bucket=14800))//1000000
    def rows(reader,sql,bound):
        if 'ticker_normalized' in sql:return [dict(ticker_normalized='ABC',symbol_interval_id='interval',source_content_sha256='a'*64)]
        if 'argMaxIf' in sql:
            assert 'execution_trade_count' not in sql
            return [dict(bucket=14400+i,bid=9.99,ask=10.01,quote_us=(anchor+14401+i)*1000000-1,volume=7.,notional=70.) for i in range(400)]
        raise AssertionError(sql)
    monkeypatch.setattr(prep,'_rows',rows)
    lane=ProgramInputPublication(query_limits=dict(max_threads=1,max_execution_time=30,
        max_memory_usage=100000000,max_result_rows=10000,max_result_bytes=1000000),maximum_concurrent_units=1)
    class ManagedClient(Client):
        def close(self):pass
    lane.history.reader_factory=lambda:ManagedClient(store)
    lane.history.writer_factory=lambda:ManagedClient(store,True)
    prepared=asyncio.run(lane.prepare_features(req,ownership=Owner()))
    assert store.writes[-1]=='qmd_feature_source_plan_v1'
    assert calls and lane.active==0
    assert not req.audit.active_cutover_authorized  # No Backtest launch authorization was needed.
    liquid_source=replace(old.liquidity_source,market=req.opening_source.market)
    extended=replace(old,opening_source=req.opening_source,liquidity_source=liquid_source,
        fields=product.MARKET_FIELDS,first_bucket=14400,stop_bucket=14800,feature_day=prepared)
    s=extended.scope('00000000-0000-0000-0000-000000000001','a'*64)
    liquid=pl.DataFrame({'bucket_index':b['bucket_index'],'execution_notional':np.full(400,70.)})
    frame=prepared.project(extended,s,b,liquid)
    native=product.prepare_product(s,b,liquid,extended.fields,volume_window_seconds=30,
        producer_code_hash='a'*64,feature_frame=frame,feature_plan_hash=prepared.plan.plan_hash)
    parents=extended.parents('a'*64)
    from datetime import datetime,timezone
    header=product.history_seal(native,parents,certified_at=datetime.now(timezone.utc))
    certificate=CertifiedHistoryProduct(native,parents,MappingProxyType(header))
    selected=('enriched.return.5s','indicator.ema.5.relative','log_close')
    fields={name:InputField(name,product.BY_NAME[name].unit,'q_live.qmd_causal_history_value_v1',s.product_revision,1000) for name in selected}
    rule=RuleProgram((RuleNode(Operation.FEATURE,field='enriched.return.5s'),
        RuleNode(Operation.CONSTANT,literal=0.,unit='ratio'),RuleNode(Operation.GREATER,left=0,right=1)),2)
    from src.market_engine.volume_rank_rules import VolumeUniverseRule
    release=ProgramRelease(dict(content_hash='e'*64,swing_left=0),fields,{'entry':rule},{},VolumeUniverseRule(30,10,.8,50.))
    def post(path,request,**kw):return response_for(certificate,request['first_ordinal'],request['stop_ordinal'],request['field_ids'])
    result=asyncio.run(prepare_program_block(release,{'listing':certificate.pin()},first_ordinal=0,stop_ordinal=400,
        evaluation_block='full-origin',as_of=product.completed_clock(s,s.stop_bucket-1),concurrency=1,history_post=post))
    assert result.market.height==400 and result.masks['entry'].any()
    assert all(name in result.market.columns for name in selected)
    # Existing volume histories remain independent products; both source
    # lineages and actual scanner volume must still agree in the real cohort.
    from src.backend.qmd_columnar_population import CertifiedPopulationPin
    from src.backend.backtest_program_inputs import prepare_program_cohort_inputs
    from datetime import datetime,timezone
    volume_scope=replace(s,product_revision=product.product_revision((),'d'*64,volume_window_seconds=30))
    volume=product.prepare_product(volume_scope,b,liquid,(),volume_window_seconds=30,producer_code_hash='d'*64)
    volume_parents=replace(parents,producer_code_hash='d'*64,feature_plan_hash='')
    volume_header=product.history_seal(volume,volume_parents,certified_at=datetime.now(timezone.utc))
    volume_certificate=CertifiedHistoryProduct(volume,volume_parents,MappingProxyType(volume_header))
    reference=replace(extended.opening_reference,rows_by_listing=MappingProxyType({'listing':MappingProxyType(dict(
        listing_id='listing',ticker='ABC',adjusted_prior_close=10.,status='resolved',
        available_at=datetime(2026,8,3,8,tzinfo=timezone.utc)))}))
    population=CertifiedPopulationPin(reference,{'listing':volume_certificate.pin()},release.universe)
    def cohort_post(path,request,**kw):
        cert=certificate if request['field_ids'] else volume_certificate
        return response_for(cert,request['first_ordinal'],request['stop_ordinal'],request['field_ids'])
    cohort=asyncio.run(prepare_program_cohort_inputs(release,{'listing':certificate.pin()},population,{'assignment':'listing'},
        first_ordinal=0,stop_ordinal=400,evaluation_block='full-origin',first_bucket=14750,stop_bucket=14800,
        as_of=product.completed_clock(s,s.stop_bucket-1),concurrency=1,history_post=cohort_post))
    assert cohort.blocks and cohort.population.ranked.height==50


def test_feature_request_rejects_unverified_or_wrong_session_sources():
    old,req=request()
    with pytest.raises(ValueError,match='source'):
        replace(req,audit=replace(req.audit,source_storage_verified=False))
    with pytest.raises(ValueError,match='RVOL'):
        replace(req,prior_volume=req.continuity[0])


def test_session_product_proof_is_exact_and_does_not_authorize_backtest(monkeypatch):
    from src.trading_runtime import arte_market_day_cold_preflight as cold
    old, req = request()
    authority = replace(req.audit, market_products_verified=False)
    calls = []
    def verify(reader, audit, **kwargs):
        calls.append(kwargs)
        assert audit is authority
        return req.opening_source.market
    monkeypatch.setattr(cold, 'certified_market_day_plan_from_cold_audit', verify)
    plan, proof = prep.certify_feature_source_day(None, authority,
        day=req.opening_source.market.sessions[0])
    assert calls[0]['tickers'] == ()
    assert calls[0]['sessions'] == plan.sessions
    assert not authority.market_products_verified and not authority.fixed_backtest_ready
    assert prep.source_audit_ready(proof, plan)
    assert not prep.source_audit_ready(proof, replace(plan, token='different-plan'))
    assert replace(req, audit=proof, continuity=((plan, proof),)).audit is proof
    with pytest.raises(ValueError, match='authority'):
        prep.certify_feature_source_day(None, replace(authority, source_storage_verified=False),
            day=plan.sessions[0])
