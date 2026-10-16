"""Independent publication and explicitly assumed identity boundaries."""
from dataclasses import replace
from datetime import date
from unittest.mock import patch
import os
from uuid import uuid4
import pytest
from pipelines.certified_research import contracts as c,retrospective_identity as ri,independent_market as im,recipe,source

DAY=date(2026,1,2)


def inputs():
    row=dict(entity_key='provider:TEST',ticker='TEST',event_backed=True,provider_type='CS',status='resolved',conid=123,
        security_id='security',listing_ids=['listing'],evidence_hash='a'*64)
    pool=dict(rows=[row],pool_hash='b'*64,review_hash='c'*64,summary={'resolved':1})
    metadata=[dict(listing_id='listing',security_id='security',issuer_id='issuer',exchange_code='XNYS',
        currency_code='USD',product_type='STK',exchange_country='US',list_date=None,delisted_date=None)]
    base=dict(source_rows=[],source_hash='d'*64)
    return base,pool,metadata


def test_current_identity_assumption_is_explicit_and_preserves_known_delisting():
    base,pool,metadata=inputs();metadata[0]['delisted_date']='2026-01-06'
    snap=ri.compile_snapshot(base,pool,metadata,DAY,date(2026,1,9),date(2026,10,1))
    assert snap['ready'] and not snap['historical_membership_certified']
    assert snap['intervals'][0].valid_from.date()==DAY
    assert snap['intervals'][0].valid_to.date()==date(2026,1,6)
    assert ri.POLICY in next(iter(snap['assumption_proofs'].values()))


def test_current_identity_never_overwrites_contrary_historical_security():
    base,pool,metadata=inputs()
    base['source_rows']=[dict(listing_id='listing',interval_security_id='other-security')]
    snap=ri.compile_snapshot(base,pool,metadata,DAY,DAY,DAY)
    assert not snap['ready'] and not snap['intervals']
    assert snap['quarantine_counts']=={'conflicting_historical_identity':1}


@pytest.mark.parametrize('kind',['ETF','PFD','WARRANT','SP','UNIT','FUND','RIGHT','ETN','ETS','ETV','ADRP','ADRR',''])
def test_retrospective_admission_uses_canonical_tradability_not_provider_type(kind):
    base,pool,metadata=inputs();pool['rows'][0]['provider_type']=kind
    snap=ri.compile_snapshot(base,pool,metadata,DAY,DAY,DAY)
    assert len(snap['intervals'])==1 and not snap['intervals'][0].reason
    assert snap['eligibility_census']=={kind or '<missing>':{'admitted':1}}


@pytest.mark.parametrize('change,reason',[
    ({'product_type':'IND'},'unsupported_security'),
    ({'exchange_code':''},'unproven_exchange'),
    ({'exchange_code':'XNYS','exchange_name':'OTC Markets'},'unsupported_otc_venue'),
    ({'exchange_country':'CA'},'unproven_us_security')])
def test_provider_type_does_not_override_missing_exchange_tradability(change,reason):
    base,pool,metadata=inputs();pool['rows'][0]['provider_type']='ETF';metadata[0].update(change)
    snap=ri.compile_snapshot(base,pool,metadata,DAY,DAY,DAY)
    assert not snap['intervals'] and snap['quarantine_counts']=={reason:1}
    assert snap['eligibility_census']=={'ETF':{'quarantined':1}}


def test_noncommon_unresolved_candidate_is_explicitly_quarantined():
    base,pool,metadata=inputs();pool['rows'][0].update(provider_type='WARRANT',status='blocked',conid=0)
    snap=ri.compile_snapshot(base,pool,metadata,DAY,DAY,DAY)
    assert snap['quarantine_counts']=={'unresolved_conid':1}


def test_current_identity_keeps_known_rename_intervals():
    base,pool,metadata=inputs()
    base['source_rows']=[dict(listing_id='listing',interval_security_id='security',ticker=t,
        valid_from_date=a,valid_to_date_exclusive=b) for t,a,b in
        [('OLD','2025-01-01','2026-01-06'),('TEST','2026-01-06',None)]]
    snap=ri.compile_snapshot(base,pool,metadata,DAY,date(2026,1,9),DAY)
    assert [i.ticker for i in snap['intervals']]==['OLD','TEST']


def test_observation_date_is_not_identity_birth_but_known_ipo_is_respected():
    base,pool,metadata=inputs()
    base['source_rows']=[dict(listing_id='listing',interval_security_id='security',ticker='TEST',
        valid_from_date='2026-06-09',valid_to_date_exclusive=None)]
    first=ri.compile_snapshot(base,pool,metadata,DAY,date(2026,1,30),DAY)
    assert first['intervals'][0].valid_from.date()==DAY
    metadata[0]['list_date']='2026-01-15'
    second=ri.compile_snapshot(base,pool,metadata,DAY,date(2026,1,30),DAY)
    assert second['intervals'][0].valid_from.date()==date(2026,1,15)


def test_market_scope_context_uses_activity_requirements_not_short_features():
    from pipelines.certified_research import context
    cfg=c.CampaignConfig(DAY,DAY,product_scope='market_bars')
    assert context.preparation_start(cfg,DAY)==DAY
    with pytest.raises(ValueError,match='pinned conid'):
        replace(cfg,identity_policy=ri.POLICY)


def test_market_cli_does_not_require_sec_context():
    from scripts.prepare_certified_research import parse_args,campaign_config
    args=parse_args(['launch','--dataset-id',str(uuid4()),'--capacity-profile','pilot.json',
        '--product-scope','market_bars','--context-start-date',str(DAY),'--end-date',str(DAY)])
    assert campaign_config(args,DAY).product_scope=='market_bars'


def test_other_month_owned_flag_mutation_does_not_block_ready_market_month():
    stamp=1767362400000000
    inv=dict(expected=(DAY,),rows={DAY:dict(first_sip_timestamp_us=stamp,last_sip_timestamp_us=stamp+1)})
    marker='a'*64
    mutation=dict(table='events_2026',command=f"UPDATE event_meta=64 WHERE '{marker}'='{marker}'",parts_to_do_names=['202602_1_2_0'])
    def query(sql):
        return [dict(n=1)] if 'system.tables' in sql else [dict(target='market_sip_compact.events_2026',marker=marker)]
    assert not source.mutations_overlap(query,[mutation],inv)
    assert source.mutations_overlap(query,[dict(mutation,parts_to_do_names=['202601_1_2_0'])],inv)
    assert source.mutations_overlap(query,[dict(mutation,command='UPDATE price_primary_int=0 WHERE 1')],inv)
    assert source.mutations_overlap(query,[dict(mutation,command='UPDATE event_meta=64 WHERE 1')],inv)
    assert source.mutations_overlap(query,[dict(mutation,parts_to_do_names=[])],inv)


def test_independent_plan_has_no_sec_float_or_short_dependency():
    base,pool,metadata=inputs();snap=ri.compile_snapshot(base,pool,metadata,DAY,DAY,DAY)
    cfg=c.CampaignConfig(DAY,DAY,product_scope='market_bars',identity_policy=ri.POLICY,conid_review_hash='c'*64)
    canonical=dict(expected=(DAY,),missing=(),incomplete=(),content_hash='a'*64,source_hashes={str(DAY):'b'*64})
    reporting=dict(missing=(),mutating=False,content_hash='c'*64,verified={str(DAY):dict(source_digest='b'*64)})
    rules=tuple(dict(token_id=i,modifier_int=0,update_last=1,update_high_low=1,update_volume=1) for i in range(80))
    definition=recipe.market_recipe(cfg,rules)['definition']
    plan=im.assemble(cfg,snap,canonical,reporting,definition,rules,DAY)
    kinds={p.unit.product for p in plan['plans']}
    assert {'market_aggregate','activity','reference_availability'}<=kinds
    assert not {'fundamentals','sec_acquisition','short_interest','float_supply','technical','v7'}&kinds
    statuses=[p for p in plan['plans'] if p.unit.product=='reference_availability']
    assert statuses[0].arguments['status']=='unknown'
    assert set(statuses[0].arguments['families'])==set(im.UNKNOWN_FAMILIES)
    assert not any('sec:' in parent for p in plan['plans'] for parent in p.unit.parents)
    assert c.CampaignConfig(DAY,DAY).identity()['config'].get('product_scope') is None
    assert cfg.content_hash!=replace(cfg,product_scope='full').content_hash


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='Explicit bounded SSD ClickHouse fixture')
def test_unknown_publication_has_no_numeric_values_and_resumes():
    from scripts import build_market_day as b
    from pipelines.certified_research import reference_availability as av
    from pipelines.certified_research.campaign import PlannedUnit
    from pipelines.certified_research.execution import Unit
    database='certified_research_test_'+uuid4().hex;dataset=str(uuid4())
    client=b.Client(b.parse_args(['--date',str(DAY),'--env-file',str(b.ROOT/'.env')]),persistent=False)
    class Claim:
        def check(self):pass
    class Borrowed:
        query=client.query
        def close(self):pass
    try:
        client.query('CREATE DATABASE '+database,read=False)
        client.query(av.persistence_table().ddl(database),read=False)
        config=c.CampaignConfig(DAY,DAY,database=database,product_scope='market_bars')
        handlers=av.Handlers(dataset,config,Claim(),Borrowed,lambda:None)
        plan=PlannedUnit(Unit('reference','reference_availability'),dict(entity_key=1,start=str(DAY),end=str(DAY),
            families=im.UNKNOWN_FAMILIES,status='unknown',reason='not_qualified_in_independent_market_scope'))
        assert handlers.verifier(plan,{}) is None
        first=handlers.producer(plan,{},None)
        assert first==handlers.verifier(plan,{})==handlers.producer(plan,{},None)
        rows=client.query(f'SELECT * FROM {database}.{av.TABLE}')
        assert len(rows)==4 and all(r['status']==0 for r in rows)
        assert not any('value' in r for r in rows)
        bad=replace(plan,arguments=dict(plan.arguments,status='available'))
        with pytest.raises(ValueError):handlers.producer(bad,{},None)
        # Assumption provenance is independently persisted and checked, rather
        # than being promoted into a fabricated historical Gateway observation.
        from pipelines.certified_research import schema
        from pipelines.certified_research.universe_publication import UniversePublisher
        for table in schema.TABLES:client.query(table.ddl(database),read=False)
        base,pool,metadata=inputs();snapshot=ri.compile_snapshot(base,pool,metadata,DAY,DAY,DAY)
        dictionary=Claim();dictionary.dictionary_database=database
        publisher=UniversePublisher(client.query,database,dataset,Claim(),dictionary,lambda:None)
        receipt=publisher.publish(snapshot,DAY,DAY,DAY)
        assert receipt==publisher.receipt(snapshot)==publisher.publish(snapshot,DAY,DAY,DAY)
        proof=client.query(f'SELECT proof FROM {database}.research_provenance_v1')[0]['proof']
        assert ri.POLICY in proof
        client.query(f"ALTER TABLE {database}.research_provenance_v1 UPDATE proof='corrupt' WHERE 1 SETTINGS mutations_sync=2",read=False)
        with pytest.raises(ValueError,match='provenance'):publisher.receipt(snapshot)
    finally:
        client.query('DROP DATABASE IF EXISTS '+database+' SYNC',read=False);client.close()
