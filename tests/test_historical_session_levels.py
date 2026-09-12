from copy import deepcopy
import numpy as np
import pytest
from src.market_engine.historical_session_levels import extract,Settings
from src.backend.historical_session_level_chart import render
from src.market_engine.historical_session_levels import role_timeline


def test_role_segments_start_on_confirmation_and_require_retest_to_flip():
    def event(at,end,role,outcome):return dict(at=at,resolved_at=end,role=role,outcome=outcome)
    result=role_timeline([event(1,3,'resistance','unresolved'),event(4,6,'resistance','rejection'),
        event(7,9,'resistance','acceptance'),event(10,12,'support','rejection'),
        event(13,15,'support','rejection'),event(16,18,'support','acceptance'),
        event(19,21,'support','rejection')],30)
    assert [(r['start'],r['end'],r['role']) for r in result]==[
        (6,9,'resistance'),(9,12,'transition'),(12,18,'support'),(18,21,'transition'),(21,30,'support')]
    assert role_timeline([event(1,3,'resistance','acceptance')],30)==[]


def test_late_resolution_of_old_encounter_does_not_override_new_role():
    result=role_timeline([dict(at=1,resolved_at=10,role='resistance',outcome='rejection'),
        dict(at=5,resolved_at=7,role='support',outcome='rejection')],30)
    assert len(result)==1 and result[0]['role']=='support' and result[0]['start']==7


def example():
    closes=np.tile(np.r_[np.linspace(10,11,60),np.linspace(11,10,60)],5)
    bars=[dict(t=float(i+1),open=float(c),high=float(c+.01),low=float(c-.01),close=float(c),volume=100.) for i,c in enumerate(closes)]
    return bars,[dict(price=10.,volume=10000.),dict(price=11.,volume=10000.)]


def run(bars,profile,**kwargs):
    return extract(bars,profile,ticker='TEST',session='2026-08-21',available_at=1000.,source={'test':True},**kwargs)


def test_full_session_extracts_repeated_extremes_without_prior_levels():
    bars,profile=example();original=deepcopy(bars)
    result=run(bars,profile)
    assert result['prior_level_count']==0 and result['retrospective']
    assert any(z['support_rejections']>=2 and z['price']<10.1 for z in result['levels'])
    assert any(z['resistance_rejections']>=2 and z['price']>10.9 for z in result['levels'])
    assert result['counts']['candidates']==len(result['levels'])+len(result['rejected'])
    assert bars==original and result==run(bars,profile)
    assert all(e['resolved_at']>=e['at'] for z in result['levels'] for e in z['encounters'])


def test_reaction_zone_is_not_erased_by_final_crossing():
    bars,profile=example()
    for c in np.linspace(10,12,150):
        bars.append(dict(t=bars[-1]['t']+1,open=float(c),high=float(c+.01),low=float(c-.01),close=float(c),volume=100.))
    result=run(bars,profile)
    zone=next(z for z in result['levels'] if abs(z['price']-11)<.1)
    assert zone['resistance_rejections']>=2 and zone['accepted_crossings']>=1
    assert zone['closing_role']=='support'


@pytest.mark.parametrize('kind',['nan','duplicate','ohlc','negative_volume'])
def test_invalid_data_fails_closed(kind):
    bars,profile=example()
    if kind=='nan':bars[5]['close']=float('nan')
    elif kind=='duplicate':bars[5]['t']=bars[4]['t']
    elif kind=='ohlc':bars[5]['high']=1.
    else:bars[5]['volume']=-1
    with pytest.raises(ValueError):run(bars,profile)


def test_no_claim_of_intraday_availability_or_silent_capacity_truncation():
    bars,profile=example()
    with pytest.raises(ValueError,match='before session end'):
        extract(bars,profile,ticker='TEST',session='2026-08-21',available_at=1,source={})
    with pytest.raises(ValueError,match='no truncation'):
        run(bars,profile,settings=Settings(maximum_candidates=1))


def test_chart_serializes_same_authoritative_levels_for_every_timeframe():
    bars,profile=example();result=run(bars,profile)
    result['source'].update(start='2026-08-21T04:00:00-04:00',end='2026-08-21T20:00:00-04:00',authority='fixture',clock='fixture')
    html=render(result,bars,profile)
    assert 'value="1"' in html and 'value="300"' in html
    assert 'book.levels.map' in html and 'No prior levels' in html
    result['ticker']='</script><script>alert(1)</script>'
    assert '</script><script>alert(1)' not in render(result,bars,profile)


@pytest.mark.parametrize('window_hours',[2,16])
def test_source_volume_reconciliation_and_utc_year_boundary(monkeypatch,window_hours):
    from src.backend import historical_session_level_source as source
    rules=[dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1)]
    monkeypatch.setattr(source,'source_metadata',lambda *a,**kw:({'token':'fixed'},rules))
    sqls=[]
    def query(sql):
        sqls.append(sql)
        if 'GROUP BY t' in sql:
            return [dict(t=len(sqls)*2,open=10.,high=10.,low=10.,close=10.,volume=10.,trades=1,last_count=1,extrema_count=1),
                dict(t=len(sqls)*2+1,volume=5.,trades=1,last_count=0,extrema_count=0)]
        return [dict(price=10.,volume=15.)]
    bars,profile,audit=source.load('TEST','2026-12-31',query=query,window_hours=window_hours)
    assert len(audit['audit'])==16//window_hours
    assert sum(p['volume'] for p in profile)==sum(b['volume'] for b in bars)+sum(a['price_unavailable_volume'] for a in audit['audit'])
    assert any('events_(2026|2027)' in s for s in sqls)
    assert all('market_sip_compact' in s and 'file(' not in s for s in sqls)
    def bad(sql):
        result=query(sql)
        if 'GROUP BY t' not in sql:result[0]['volume']=100.
        return result
    with pytest.raises(ValueError,match='disagree'):source.load('TEST','2026-12-31',query=bad,window_hours=window_hours)
