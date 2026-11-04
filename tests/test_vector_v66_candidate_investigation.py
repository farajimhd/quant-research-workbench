import numpy as np
from research.vectorized_backtest.v66.candidate_investigation import price_causes


def test_configured_review_supports_different_candidates_and_routing():
    from research.vectorized_backtest.v66.candidate_investigation import configured_variants,endpoint_layout
    from dataclasses import replace
    from research.vectorized_backtest.v66.genome import sample,Network,Neuron
    from research.vectorized_backtest.v66.authority import construct,ALLOWED
    from research.vectorized_backtest.v66.strategy_mix import route,leaves
    parents=[construct(replace(m,networks=dict(m.networks,entry=Network((Neuron((ALLOWED[0],),(1.,),0.,op='identity'),)))))
             for m in sample(np.random.default_rng(83),2)]
    parents.append(route(*parents))
    for serial,parent in enumerate(parents):
        members=configured_variants(parent,serial)
        layout=endpoint_layout(members)
        assert members[0][1] is parent
        assert len(layout['parent'])==len(leaves(parent))
        for actual,branch in zip(layout['parent'],leaves(parent)):
            assert set(actual['networks'])==set(branch.networks)
            assert actual['prices']==branch.payload()['policy']['price_program']


def test_resume_identity_uses_json_semantics():
    import json
    from research.vectorized_backtest.v66.identity import digest
    spec={'sources':(1,2),'nested':{'weights':(.2,.3)}}
    assert digest(spec)==digest(json.loads(json.dumps(spec)))


def test_missing_swing_is_separate_from_spread_and_stop_relation():
    prices=np.array([[10.,10.,np.nan,11.,9.],[10.,10.,9.99,11.,9.],[10.,10.,9.,np.nan,9.]])
    result=price_causes(prices,np.array([9.99,9.99,9.]),np.array([10.,10.,10.]))
    assert result['missing_stop'].tolist()==[True,False,False]
    assert result['invalid_stop'].tolist()==[False,True,True]
    assert result['missing_target'].tolist()==[False,False,True]
    assert result['excessive_spread'].tolist()==[False,False,True]


def test_exact500bps_is_allowed():
    result=price_causes(np.array([[10.5,10.,9.,12.,9.]]),np.array([10.]),np.array([10.5]))
    assert not any(v.any() for v in result.values())
