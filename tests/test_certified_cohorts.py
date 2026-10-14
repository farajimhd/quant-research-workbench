"""Issuer cohorts keep complete histories without materializing the whole DAG."""
from dataclasses import replace
from datetime import date,datetime,timedelta
from unittest.mock import patch
import pytest

from pipelines.certified_research import cohorts,universe
from pipelines.certified_research.contracts import CampaignConfig,digest
from pipelines.certified_research.checkpoints import NY
from pipelines.certified_research.identity import ListingInterval,identity_key,ISSUER


def snapshot(count=3):
    intervals=tuple(ListingInterval('L'+str(n),'S'+str(n),'I'+str(n),'T'+str(n),n+1,
        datetime(2025,1,1,tzinfo=NY),datetime(2027,1,1,tzinfo=NY),'US','XNAS','USD','STK',True,True,False,False,digest(n))
        for n in range(count))
    return dict(ready=True,rejected=(),intervals=intervals,source_hash='a'*64,
        source_rows=tuple(dict(listing_id=i.listing_id) for i in intervals))


def test_selection_keeps_share_classes_and_historical_listings_and_full_epoch():
    source=snapshot();old=source['intervals'][0]
    source['intervals']+=(replace(old,listing_id='historical',security_id='historical-security',ticker='OLD'),)
    key=identity_key(ISSUER,old.issuer_id);selected=cohorts.select(source,(key,))
    assert {i.listing_id for i in selected['intervals']}=={'L0','historical'}
    assert selected['full_source_hash']==source['source_hash'] and selected['source_hash']!=source['source_hash']
    assert cohorts.select(selected,(key,))['source_hash']==selected['source_hash']
    source['ready']=False;source['rejected']=({'unrelated':'bad'},)
    with pytest.raises(ValueError,match='Full historical universe'):cohorts.select(source,(key,))


def test_interval_census_equals_execution_membership_and_keeps_day_binding():
    source=snapshot();config=CampaignConfig(date(2026,1,2),date(2026,1,6))
    days=(date(2025,12,31),date(2026,1,2),date(2026,1,5),date(2026,1,6))
    full=universe.execution_membership(source,days)
    actual=cohorts.partition_snapshot(source,config,days,maximum_issuers=2,maximum_sessions=8)
    expected=cohorts.partition(source,full,config,maximum_issuers=2,maximum_sessions=8)
    assert actual['groups']==expected['groups'] and actual['session_counts']==expected['session_counts']
    changed=cohorts.partition_snapshot(source,config,(date(2025,12,30),*days[1:]),maximum_issuers=2,maximum_sessions=8)
    assert changed['session_counts']==actual['session_counts'] and changed['partition_hash']!=actual['partition_hash']


def test_large_universe_partition_never_expands_half_a_million_session_rows():
    source=snapshot(3000);config=CampaignConfig(date(2026,1,1),date(2026,10,2))
    days=tuple(date(2026,1,1)+timedelta(days=n) for n in range(200))
    with patch.object(universe,'execution_membership',side_effect=AssertionError('Expanded whole universe')):
        result=cohorts.partition_snapshot(source,config,days,maximum_issuers=128)
    assert sum(n for _,n in result['session_counts'])==600000
    assert len(result['groups'])==24
    assert len({key for group in result['groups'] for key in group})==3000


@pytest.mark.parametrize('failure',('partial','competing','oversized'))
def test_census_blocks_invalid_membership_or_one_issuer_exceeding_budget(failure):
    source=snapshot(1);config=CampaignConfig(date(2026,1,2),date(2026,1,5));days=(config.start,config.end)
    if failure=='partial':source['intervals']=(replace(source['intervals'][0],valid_from=datetime(2026,1,2,5,tzinfo=NY)),)
    elif failure=='competing':source['intervals']+=(replace(source['intervals'][0],listing_id='other'),)
    with pytest.raises(ValueError):cohorts.partition_snapshot(source,config,days,maximum_sessions=1 if failure=='oversized' else 10)


def test_cohort_selection_is_frozen_without_changing_calculation_identity():
    config=CampaignConfig(date(2026,1,2),date(2026,1,5));selected=replace(config,issuer_keys=(1,2))
    assert 'issuer_keys' not in config.identity()['config']
    assert selected.content_hash!=config.content_hash and selected.calculation_hash==config.calculation_hash
    with pytest.raises(ValueError):replace(config,issuer_keys=(2,1))
