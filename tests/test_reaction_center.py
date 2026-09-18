from copy import deepcopy
import math
import pytest
import numpy as np
from types import SimpleNamespace
from src.market_engine.reaction_center import annotate,estimate,fit
from src.market_engine.historical_level_checkpoint import seed,consolidate
from tests.test_historical_level_checkpoint import day


def test_analytic_gradient_and_box_convergence_validation():
    from src.market_engine.reaction_center import objective_gradient,converged
    prices=np.array([-3.,0.,0.,1.,50.])
    point=np.array([.06,-.46])
    objective=lambda p:objective_gradient(p,prices)
    _,gradient=objective(point)
    numerical=[]
    for i in range(2):
        delta=np.zeros(2);delta[i]=1e-5
        numerical.append((objective(point+delta)[0]-objective(point-delta)[0])/2e-5)
    np.testing.assert_allclose(gradient,numerical,rtol=1e-6,atol=1e-7)
    bad=SimpleNamespace(success=True,fun=objective(point)[0],x=point)
    assert not converged(bad,objective,[(-3.,50.),(-.69,5.)])
    boundary=SimpleNamespace(success=True,fun=0.,x=np.array([0.,0.]))
    assert converged(boundary,lambda p:(0.,np.array([0.,1.])),[(0.,0.),(0.,5.)])
    boundary.success=False
    assert not converged(boundary,lambda p:(0.,np.zeros(2)),[(0.,0.),(0.,5.)])


def test_repeated_price_regression_with_collapsed_quantile_starts():
    # Captured failing input, compressed as price multiplicities; no ticker rule.
    counts={.9997:1,.9998:1,1.:162,1.0001:9,1.0002:1,1.0003:1,
            1.0004:2,1.0006:1,1.0007:5,1.0008:2,1.0009:3,
            1.0011:2,1.0012:1,1.0049:1,1.005:19}
    prices=[p for p,n in counts.items() for _ in range(n)]
    assert len(prices)==211
    result=fit(prices,.0001)
    assert result['status']=='estimated'
    assert result['center']==pytest.approx(1.0000058502245537,abs=1e-10)
    assert result['scale']==pytest.approx(6.263900477637807e-5,rel=1e-5)


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


def test_aci_boundary_fit_regression_preserves_likelihood_and_floor():
    from src.market_engine.reaction_center import objective_gradient, converged
    prices=[19.54]+[19.55]*6
    result=fit(prices,.01)
    assert result['status']=='estimated'
    assert result['center']==pytest.approx(19.54916403049,abs=1e-10)
    assert result['scale']==pytest.approx(.005,abs=1e-12)
    assert result['scale_at_floor']
    assert result==fit(prices,.01)
    assert fit(list(reversed(prices)),.01)['center']==pytest.approx(result['center'],abs=1e-9)
    y=(np.array(prices)-19.55)/.01
    point=np.array([(result['center']-19.55)/.01,math.log(result['scale']/.01)])
    objective=lambda p:objective_gradient(p,y)
    assert converged(SimpleNamespace(success=True,fun=objective(point)[0],x=point),
                     objective,[(y.min(),y.max()),(math.log(.5),math.log(np.ptp(y)*2))])


@pytest.mark.parametrize('success,point', [(False,[0.,math.log(.5)]),
    (True,[0.,math.log(.5)]), (True,[float('nan'),0.]), (True,[2.,0.])])
def test_retry_cannot_accept_failed_nonstationary_nonfinite_or_out_of_bounds_fit(monkeypatch,success,point):
    import src.market_engine.reaction_center as module
    calls=[]
    def reject(objective,start,**kwargs):
        calls.append(kwargs['method'])
        return SimpleNamespace(success=success,fun=0.,x=np.array(point))
    monkeypatch.setattr(module,'minimize',reject)
    assert fit([19.54]+[19.55]*6,.01)['status']=='fit_failed'
    assert calls==['L-BFGS-B','SLSQP']


def test_successful_primary_fit_never_uses_retry(monkeypatch):
    import src.market_engine.reaction_center as module
    original=module.minimize
    def primary_only(*args,**kwargs):
        assert kwargs['method']=='L-BFGS-B'
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'minimize',primary_only)
    assert fit([100.]*8,.01)['status']=='estimated'


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
