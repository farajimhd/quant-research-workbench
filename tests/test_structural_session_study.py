from copy import deepcopy

from scripts.structural_session_study import LevelCatalog,expand,variants


def data(target=11):
    catalog=[dict(unified_level_id='origin',lower=9.5,upper=9.6,side=-1,selection_score=60,confirmed_at_ms=90000),
             dict(unified_level_id='near',lower=target,upper=target+.01,side=-1,selection_score=40),
             dict(unified_level_id='far',lower=12,upper=12.01,side=-1,selection_score=80)]
    return dict(level_catalog=catalog,rows=[dict(at=100,bar=dict(close=10),level_refs=[0,1,2],
        events=[dict(state='breakout_accepted',level_ref=0)],shares=200000,dollars=2000000,rate10=10,rate60=10,
        direction='up',progression={},sequence=[])],quotes=[dict(at=t,bid=b,ask=b+.01) for t,b in [(100,10),(100.1,10),(101,11),(130,12)]])


def test_catalog_deduplicates_without_mutable_back_reference():
    catalog=LevelCatalog();level=dict(lower=1,upper=2,side=-1,timeframes=['1s'])
    first=catalog.intern(level)
    assert catalog.intern(deepcopy(level))==first
    level['timeframes'].append('1m');level['upper']=3
    assert catalog.values[first]['upper']==2
    assert catalog.values[first]['timeframes']==['1s']
    assert catalog.intern(level)!=first


def test_expansion_retains_every_level_and_event_identity():
    d=data();rows=expand(d)
    assert len(rows[0]['levels'])==3
    assert rows[0]['events'][0]['level'] is rows[0]['levels'][0]


def test_variants_change_one_rule_and_preserve_input():
    d=data();before=deepcopy(d);r=variants(d)
    assert r['baseline']['summary']['trades']==1
    assert r['near_origin']['summary']['trades']==0
    assert r['near_origin']['summary']['blocked']['extended_from_origin']==1
    assert r['positive_net_target']['summary']['trades']==1
    assert r['stronger_target']['trades'][0]['target']==12
    assert r['baseline']['trades'][0]['target']==11
    assert d==before


def test_positive_net_gate_rejects_too_close_target():
    r=variants(data(target=10.02))
    assert r['positive_net_target']['summary']['trades']==0
    assert r['positive_net_target']['summary']['blocked']['target_net_nonpositive']==1


def test_execution_net_gate_does_not_depend_on_observing_horizon():
    d=data(target=10.05);d['quotes']=[dict(at=100,bid=10,ask=10.01),dict(at=100.1,bid=10.02,ask=10.04)]
    r=variants(d)
    assert r['positive_net_target']['summary']['blocked']['execution_target_net_nonpositive']==1
    assert r['positive_net_target']['summary']['unresolved']==0
