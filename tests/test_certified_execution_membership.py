"""Dated Gateway intervals drive execution sessions, renames and removal."""
from dataclasses import replace
from datetime import date,datetime
from zoneinfo import ZoneInfo

import pytest

from pipelines.certified_research.identity import ListingInterval,identity_key,SECURITY,LISTING,ISSUER
from pipelines.certified_research.universe import execution_membership


NY=ZoneInfo('America/New_York')
def stamp(day,hour=0):return datetime.fromisoformat(day).replace(hour=hour,tzinfo=NY)
def interval(ticker='OLD',listing='listing',first='2026-01-01',last='2026-01-06'):
    return ListingInterval(listing,'security','issuer',ticker,123,stamp(first),stamp(last),
        'US','NASDAQ','USD','STK',True,True,False,False,'a'*64)
def snapshot(*rows):return dict(ready=True,source_hash='b'*64,intervals=rows)


def test_rename_delisting_and_otc_are_dated_and_share_security_identity():
    old=interval(last='2026-01-05');new=interval('NEW','new_listing','2026-01-05','2026-01-07')
    otc=replace(interval('OTC','otc_listing'),exchange_code='OTC')
    days=(date(2026,1,2),date(2026,1,5),date(2026,1,6),date(2026,1,7))
    result=execution_membership(snapshot(old,new,otc),days)
    assert result['ready']
    assert {(s.day,s.ticker) for s in result['sessions']}=={(days[0],'OLD'),(days[1],'NEW'),(days[2],'NEW')}
    assert result['security_listings'][identity_key(SECURITY,'security')]==tuple(sorted((
        identity_key(LISTING,'listing'),identity_key(LISTING,'new_listing'))))
    assert sum(result['excluded_intervals'].values())==1
    assert result['membership_hash']==execution_membership(snapshot(new,otc,old),days)['membership_hash']


def test_partial_day_is_explicit_blocker_and_never_expanded_to_full_session():
    row=replace(interval(),valid_from=stamp('2026-01-02',12))
    result=execution_membership(snapshot(row),(date(2026,1,2),date(2026,1,5)))
    assert not result['ready'] and len(result['partial'])==1
    assert [s.day for s in result['sessions']]==[date(2026,1,5)]


def test_competing_security_listings_and_metadata_overflow_fail_closed():
    with pytest.raises(ValueError,match='Competing market listings'):
        execution_membership(snapshot(interval(),interval('SECOND','second')),(date(2026,1,2),))
    with pytest.raises(ValueError,match='metadata bound'):
        execution_membership(snapshot(interval()),(date(2026,1,2),date(2026,1,5)),maximum_sessions=1)


def test_actual_pm_ah_boundary_is_half_open_and_timezone_aware():
    row=replace(interval(first='2026-03-09',last='2026-03-10'),valid_from=stamp('2026-03-09',4),valid_to=stamp('2026-03-09',20))
    result=execution_membership(snapshot(row),(date(2026,3,9),))
    assert result['ready'] and len(result['sessions'])==1


def test_frozen_universe_drives_joined_graph_and_shared_price_dependencies():
    from tests.test_certified_program_planning import inputs
    from pipelines.certified_research.planning import program_from_universe
    arguments=inputs();arguments.pop('sessions');arguments.pop('security_listings')
    listing=identity_key(LISTING,'listing');security=identity_key(SECURITY,'security');issuer=identity_key(ISSUER,'issuer')
    arguments['issuers']=(replace(arguments['issuers'][0],issuer_key=issuer),)
    arguments['securities']=(replace(arguments['securities'][0],security_key=security,issuer_key=issuer,market_parents=()),)
    arguments['seed_parents']={listing:('technical_seed','v7_seed')};arguments['corporate_parents']={listing:'corporate'}
    prerequisites=[]
    for plan in arguments['prerequisites']:
        if plan.unit.product=='universe':
            plan=replace(plan,arguments=dict(source_hash='b'*64,context_start='2026-01-02',end='2026-01-02'))
        elif plan.unit.product in ('corporate_actions','float_allocation'):
            plan=replace(plan,arguments=dict(plan.arguments,entity_key=security))
        prerequisites.append(plan)
    arguments['prerequisites']=tuple(prerequisites)
    result=program_from_universe(snapshot=snapshot(interval()),days=(date(2026,1,2),),**arguments)
    prices=next(p for p in result['plans'] if p.unit.product=='regular_prices')
    assert prices.unit.parents==(f'market:{listing}:2026-01-02:aggregate',)
    arguments['securities']=(replace(arguments['securities'][0],issuer_key=1),)
    with pytest.raises(ValueError,match='do not match'):program_from_universe(
        snapshot=snapshot(interval()),days=(date(2026,1,2),),**arguments)
