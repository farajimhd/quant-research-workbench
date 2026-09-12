from copy import deepcopy
from datetime import datetime
import pytest
from src.market_engine.historical_session_levels import extract
from src.market_engine.historical_level_checkpoint import seed,consolidate,digest,totals,Policy
from tests.test_historical_session_levels import example


def day(date,shift=0.,factor=1.,flat=False):
    bars,profile=example()
    start=datetime.fromisoformat(date+'T04:00:00-04:00');end=datetime.fromisoformat(date+'T20:00:00-04:00')
    for b in bars:
        b['t']+=start.timestamp()
        for k in ('open','high','low','close'):b[k]=20. if flat else b[k]*factor+shift
    for p in profile:p['price']=20. if flat else p['price']*factor+shift
    extraction=extract(bars,profile,ticker='TEST',session=date,available_at=end.timestamp(),source=dict(start=start.isoformat(),end=end.isoformat()))
    return extraction,bars,profile


def test_merge_keeps_geometry_identity_ancestry_and_single_daily_evidence():
    first,_,_=day('2026-08-21');old=seed(first);saved=deepcopy(old)
    second,bars,profile=day('2026-08-24',shift=.01)
    result=consolidate(old,second,bars,profile)
    assert old==saved and result==consolidate(old,second,bars,profile)
    assert result['counts']['matched_zones']>0
    for original in old['levels']:
        row=next(r for r in result['levels'] if r['id']==original['id'])
        assert row['historical'] and row['origin_session']=='2026-08-21'
        assert (row['lower'],row['upper'])==(original['lower'],original['upper'])
        assert row['role_segments'][:len(original['role_segments'])]==original['role_segments']
        assert [e['session'] for e in row['contributions']]==['2026-08-21','2026-08-24']
        assert row['support_rejections']==sum(e['support_rejections'] for e in row['contributions'])
    assert result['checkpoint_hash']==digest({k:v for k,v in result.items() if k!='checkpoint_hash'})
    with pytest.raises(ValueError,match='only once'):consolidate(result,second,bars,profile)


def test_unmatched_untouched_levels_are_carried_without_erasure_or_new_evidence():
    first,_,_=day('2026-08-21');old=seed(first)
    second,bars,profile=day('2026-08-24',flat=True)
    result=consolidate(old,second,bars,profile)
    assert len(result['levels'])==len(old['levels'])
    assert result['counts']['matched_zones']==0
    for r in result['levels']:
        assert r['contributions'][-1]['encounters']==[]
        assert r['strength_status']=='qualified'
        assert r['role_segments'][-1]['reason']=='carried_checkpoint'


def test_split_changes_future_geometry_not_prior_chart_segments():
    first,_,_=day('2026-08-21');old=seed(first)
    second,bars,profile=day('2026-08-24',factor=.5)
    with pytest.raises(ValueError,match='corporate-action'):consolidate(old,second,bars,profile,split_factor=.5)
    result=consolidate(old,second,bars,profile,split_factor=.5,split_evidence=[dict(execution_date='2026-08-24',split_from=1,split_to=2)])
    for original in old['levels']:
        r=next(r for r in result['levels'] if r['id']==original['id'])
        assert r['price']==original['price']*.5
        assert r['role_segments'][0]==original['role_segments'][0]
        assert r['role_segments'][-1]['price']==original['price']*.5


def test_corrupt_checkpoint_and_wrong_input_fail_closed():
    first,_,_=day('2026-08-21');old=seed(first)
    second,bars,profile=day('2026-08-24')
    bad=deepcopy(old);bad['levels'][0]['lower']=1
    with pytest.raises(ValueError,match='integrity'):consolidate(bad,second,bars,profile)
    bars[0]['volume']+=1
    with pytest.raises(ValueError,match='input hash'):consolidate(old,second,bars,profile)


def test_weakening_is_explicit_and_untouched_day_does_not_restore_strength():
    row=dict(contributions=[dict(session='2026-08-21',encounters=[{}],support_rejections=1,
        resistance_rejections=0,accepted_crossings=4,profile_volume=100)])
    totals(row,Policy());assert row['strength_status']=='weakened'
    row['contributions'].append(dict(session='2026-08-24',encounters=[],support_rejections=0,
        resistance_rejections=0,accepted_crossings=0,profile_volume=0))
    totals(row,Policy());assert row['strength_status']=='weakened'


def test_match_does_not_chain_merge_or_double_count_new_zone_evidence():
    first,_,_=day('2026-08-21');first['levels']=first['levels'][:1];old=seed(first)
    second,bars,profile=day('2026-08-24');base=deepcopy(first['levels'][0]);width=base['upper']-base['lower']
    second['levels']=[]
    for i,offset in enumerate((width*.2,width*.4,width*.7)):
        z=deepcopy(base);z['id']='proposal-'+str(i)
        for k in ('lower','upper','price'):z[k]+=offset
        second['levels'].append(z)
    result=consolidate(old,second,bars,profile)
    row=next(r for r in result['levels'] if r['id']==base['id'])
    assert row['matched_today']==['proposal-0','proposal-1']
    assert result['counts']['new']==1
    assert len(row['contributions'])==2
    assert row['lower']==base['lower'] and row['upper']==base['upper']
