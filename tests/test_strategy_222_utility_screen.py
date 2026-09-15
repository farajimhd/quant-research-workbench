import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_utility_screen import predictions,feature_names


def population():
    rows=[];labels=[]
    for symbol in ('A','B','C'):
        for i in range(40):
            key=f'{symbol}:{i}'
            rows.append(dict(id=key,symbol=symbol,features={'x':i/40}))
            labels.append(dict(id=key,net=20. if i%2 else -10.,entry_ask=4.))
    return rows,labels


@pytest.mark.parametrize('objective',['binary','cost_weighted','expected_return'])
def test_held_out_outcomes_cannot_affect_own_predictions(objective):
    rows,labels=population();scores,folds=predictions(rows,labels,['x'],objective)
    changed=[dict(l,net=-3*l['net']) if i<40 else l for i,l in enumerate(labels)]
    again,_=predictions(rows,changed,['x'],objective)
    assert np.array_equal(scores[:40],again[:40])
    assert all(f['held_out_symbol'] not in f['train_symbols'] for f in folds)
    assert sum(f['test_count'] for f in folds)==len(rows)


def test_rejects_bad_join_and_nonpositive_denominator():
    rows,labels=population()
    with pytest.raises(ValueError,match='identity'):
        predictions(rows,labels[::-1],['x'],'binary')
    labels[0]['entry_ask']=0
    with pytest.raises(ValueError,match='utility'):
        predictions(rows,labels,['x'],'expected_return')


def test_feature_allowlist_excludes_identity_and_future_outcomes():
    names=feature_names()
    assert len(names)==73 and len(set(names))==73
    assert not set(names)&{'symbol','at','net','profitable','opportunity_id','major_good','entry_ask','exit_bid'}
