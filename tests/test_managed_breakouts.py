from copy import deepcopy

import pytest

from scripts.evaluate_managed_breakouts import replay


LEVEL = dict(unified_level_id='origin', lower=9.6, upper=9.7, side=-1, confirmed_at_ms=90000)


def row(at, state='breakout_accepted', low=9.9, close=10):
    return dict(at=at, bar=dict(time=at-1, end=at, low=low, high=close+.1, close=close),
                levels=[deepcopy(LEVEL),dict(lower=11,upper=11.01,side=-1)],
                events=[dict(state=state,level=deepcopy(LEVEL))] if state else [],
                shares=200000,dollars=2000000,rate10=10,rate60=10,sequence=[],direction='up')


def q(at, bid=10, ask=None):
    return dict(at=at,bid=bid,ask=bid+.01 if ask is None else ask)


def test_target_and_costs():
    r = replay([row(100)],[q(100),q(100.1),q(101,11)],200)
    assert r['summary']['trades']==1
    assert r['trades'][0]['entry_at']==100.1
    assert r['trades'][0]['net']==pytest.approx((11*.9995-10.01*1.0005)*100-2)


def test_tight_stop_rejected():
    r = row(100)
    r['events'][0]['level']['lower']=9.995
    result = replay([r],[q(100),q(100.1)],200)
    assert result['summary']['blocked']=={'room_stop_or_spread':1}
    assert not result['trades']


def test_quote_after_decision_not_used_for_gates():
    r = replay([row(100)],[q(100.1)],200)
    assert r['summary']['blocked']['stale_quote']==1


def test_quality_rechecked_at_fill():
    r = replay([row(100)],[q(100),q(100.1,9.59)],200)
    assert r['summary']['blocked']['execution_quality']==1
    assert not any(e['action']=='buy' for e in r['journal'])


def test_persistent_acceptance_does_not_reenter_after_stop():
    r = replay([row(100),row(102)],[q(100),q(100.1),q(101,9.5),q(102),q(102.1)],200)
    assert r['summary']['trades']==1
    assert r['summary']['blocked']['fresh_break_required']==1


def test_new_break_then_acceptance_allows_reentry():
    r = replay([row(100),row(102,'breakout'),row(103)],
               [q(100),q(100.1),q(101,9.5),q(102),q(103),q(103.1)],200)
    assert sum(e['action']=='buy' for e in r['journal'])==2
    assert r['summary']['unresolved']==1


def test_confirmed_higher_low_ratchets_and_never_loosens():
    rows=[row(100),row(101,None,9.9),row(102,None,9.8),row(103,None,9.9),row(104,None,9.7),row(105,None,9.9)]
    r = replay(rows,[q(100),q(100.1),q(101),q(102),q(103),q(104),q(105)],200)
    raises=[e for e in r['journal'] if e['action']=='raise_stop']
    assert len(raises)==1
    assert raises[0]['at']==103
    assert raises[0]['stop']==pytest.approx(9.79)


def test_failed_breakout_exits_after_latency():
    r = replay([row(100),row(102,'failed_breakout')],
               [q(100),q(100.1),q(102),q(102.05),q(102.1)],200)
    assert r['trades'][0]['reason']=='structural_failure'
    assert r['trades'][0]['exit_at']==102.1


def test_half_profit_runner_and_fee_accounting():
    r = replay([row(100)],[q(100),q(100.1),q(101,11),q(200,10.5)],200,partial=True)
    sells=[e for e in r['journal'] if e['action']=='sell']
    assert [e['quantity'] for e in sells]==[50,50]
    assert r['trades'][0]['net']==pytest.approx((50*11+50*10.5)*.9995-100*10.01*1.0005-3)


def test_prefix_decisions_unchanged_by_future():
    rows=[row(100),row(101,None),row(102,'failed_breakout')]
    quotes=[q(100),q(100.1),q(101),q(102),q(102.1)]
    prefix=replay(rows[:2],quotes[:3],200)
    full=replay(rows,quotes,200)
    assert prefix['journal']==[e for e in full['journal'] if e['at']<=101]


def test_missing_end_quote_keeps_position_unresolved():
    r=replay([row(100)],[q(100),q(100.1),q(199),q(202,11)],200)
    assert r['summary']['unresolved']==1
    assert r['summary']['trades']==0


def test_future_level_rejected():
    r=row(100);r['levels'][0]['confirmed_at_ms']=100001
    with pytest.raises(ValueError,match='Future level'):
        replay([r],[q(100)],200)
