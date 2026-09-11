import pytest

from scripts.audit_structural_baseline import outcome
from scripts.evaluate_structural_runner import runner


def q(at,bid=10):
    return dict(at=at,bid=bid,ask=bid+.01)


def sample(quotes):
    s=dict(at=100,stop=9.5,target=11,level=dict(unified_level_id='x'))
    s['outcome']=outcome(s,quotes,[q['at'] for q in quotes])
    return s


def test_partial_then_horizon_same_entry_three_fees():
    quotes=[q(100.1),q(102,11),q(130,12)]
    s=sample(quotes);r=runner(s,[],quotes)
    assert r['entry']==s['outcome']['entry']
    assert [e['quantity'] for e in r['journal']]==[50,50]
    assert r['net']==pytest.approx(round(50*23*.9995-100*10.01*1.0005-3,4))


def test_stop_before_target_exits_all():
    quotes=[q(100.1),q(102,9),q(130,12)]
    r=runner(sample(quotes),[],quotes)
    assert r['journal'][0]['quantity']==100
    assert r['journal'][0]['reason']=='stop'
    assert len(r['journal'])==1


def test_no_future_boundary_quote_is_invented():
    quotes=[q(100.1),q(102,11),q(132,12)]
    r=runner(sample(quotes),[],quotes)
    assert r['unresolved']
    assert r['remaining']==50
    assert r['net'] is None


def test_confirmed_low_never_uses_right_bar_early():
    quotes=[q(100.1),q(102,11),q(103,11.2),q(104,11.2),q(105,11.2),q(105.1,11.2),q(106,10.8),q(130,12)]
    rows=[dict(at=t,bar=dict(time=t-1,end=t,low=low),events=[]) for t,low in [(103,11),(104,10.9),(105,11)]]
    r=runner(sample(quotes),rows,quotes)
    changes=[e for e in r['journal'] if e['action']=='raise_stop']
    assert len(changes)==1
    assert changes[0]['at']==105
    assert changes[0]['stop']==pytest.approx(10.89)
    assert r['journal'][-1]['at']==106
