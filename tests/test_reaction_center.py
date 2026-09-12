from copy import deepcopy
import math
import pytest
from src.market_engine.reaction_center import annotate,estimate,fit
from src.market_engine.historical_level_checkpoint import seed,consolidate
from tests.test_historical_level_checkpoint import day


def test_robust_fit_small_samples_and_tick_floor():
    assert fit([100.,100.01],.01)['center'] is None
    flat=fit([100.]*8,.01)
    assert flat['center']==100. and flat['scale_at_floor']
    prices=[100,100.01,99.99,100,100.02,99.98,110]
    result=fit(prices,.01)
    assert result['status']=='estimated' and abs(result['center']-100)<.02
    assert result==fit(prices,.01)
    for bad in ([float('nan')],[float('inf')],[0.]):
        with pytest.raises(ValueError):fit(bad,.01)


def test_reaction_observations_and_overlap_are_not_crossing_prices():
    bars=[dict(t=i,high=100+i*.01,low=99-i*.01) for i in range(10)]
    events=[dict(at=1,resolved_at=3,role='resistance',outcome='rejection'),
            dict(at=2,resolved_at=4,role='resistance',outcome='rejection'),
            dict(at=5,resolved_at=7,role='support',outcome='rejection'),
            dict(at=5,resolved_at=7,role='resistance',outcome='acceptance')]
    observations=annotate(events,bars)
    assert observations[0]['reaction_price']==100.03
    assert observations[2]['reaction_price']==98.93
    assert observations[3]['reaction_price'] is None
    assert 'reaction_price' not in events[0]
    result=estimate([dict(encounters=observations)],.01)['roles']
    assert result['resistance']['count']==1
    assert result['resistance']['overlapping_excluded']==1
    assert result['resistance']['accepted_crossings_excluded']==1


def test_checkpoint_estimates_preserve_geometry_history_and_split_units():
    first,bars,profile=day('2026-08-21')
    old=seed(first,reaction_inputs=(bars,profile));saved=deepcopy(old)
    second,b2,p2=day('2026-08-24',factor=.5)
    result=consolidate(old,second,b2,p2,split_factor=.5,split_evidence=[{'test_split':True}])
    assert old==saved
    for original in old['levels']:
        r=next(r for r in result['levels'] if r['id']==original['id'])
        assert r['price']==original['price']*.5
        assert r['reaction_center_history'][0]==original['reaction_center']
        assert r['reaction_center_history'][-1]['available_at']==second['available_at']
        assert r['contributions'][0]['reaction_price_factor']==.5
        assert r['contributions'][0]['encounters']==original['contributions'][0]['encounters']
        for value in r['reaction_center']['roles'].values():
            assert value['center'] is None or math.isfinite(value['center'])
    assert result==consolidate(old,second,b2,p2,split_factor=.5,split_evidence=[{'test_split':True}])
    with pytest.raises(ValueError,match='input hash'):seed(first,reaction_inputs=(bars,[]))


def test_separate_roles_are_never_averaged_into_false_center():
    events=[]
    for role,price in [('support',99),('resistance',101)]:
        for i in range(5):events.append(dict(at=i*10,resolved_at=i*10+2,role=role,outcome='rejection',reaction_price=price))
    result=estimate([dict(encounters=events)],.01)
    assert result['roles']['support']['center']==99
    assert result['roles']['resistance']['center']==101
