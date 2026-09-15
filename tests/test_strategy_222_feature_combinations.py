import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_feature_combinations import families, population, grouped_predictions, measures, temporal_population


def row(group,symbol,label,value=1.):
    return dict(group=group,symbol=symbol,label=label,kind='actual_entry',offset_s=0,status='sampled',
        decision_at=10.,at=10.,decision_sequence=5,sequence_limit=6,features={'x':value})


def test_only_closed_pre_fill_entry_population():
    rows=[row('a','X','winner'),row('b','X','open'),dict(row('c','X','nonwinner'),offset_s=5)]
    selected,excluded=population(rows)
    assert len(selected)==1 and excluded=={'open_outcome':1}
    with pytest.raises(ValueError,match='clock/sequence'):
        population([dict(rows[0],decision_sequence=6)])
    with pytest.raises(ValueError,match='Duplicate'):
        population([rows[0],rows[0]])


def test_identity_and_outcome_are_not_features():
    forbidden={'symbol','group','label','at','net','entry_at','hindsight_peak','episode_net'}
    assert all(not forbidden.intersection(names) for names in families().values())


def test_every_prediction_excludes_its_whole_ticker():
    rows=[row(f'{s}{i}',s,'winner' if i else 'nonwinner',float(i)) for s in ('X','Y','Z') for i in (0,1)]
    y,scores,baseline,folds=grouped_predictions(rows,['x'])
    assert np.isfinite(scores).all() and len(scores)==6
    assert np.allclose(baseline,.5)
    for fold in folds:
        assert fold['held_out_symbol'] not in fold['train_groups']
        assert fold['test_count']==2 and fold['train_count']==4
    assert measures(y,scores)['rank_auc']==1.


def test_held_out_outcome_cannot_affect_its_predictions():
    rows=[row(f'{s}{i}',s,'winner' if i else 'nonwinner',float(i)) for s in ('X','Y','Z') for i in (0,1)]
    _,scores,_,_=grouped_predictions(rows,['x'])
    changed=[dict(r,label='winner' if r['label']=='nonwinner' else 'nonwinner') if r['symbol']=='X' else r for r in rows]
    _,updated,_,_=grouped_predictions(changed,['x'])
    assert np.array_equal(scores[:2],updated[:2])


def test_missing_training_feature_is_not_filled_using_test_population():
    rows=[row(f'{s}{i}',s,'winner' if i else 'nonwinner',float(i)) for s in ('X','Y','Z') for i in (0,1)]
    for r in rows:
        if r['symbol']!='X':r['features']={}
    _,scores,_,_=grouped_predictions(rows,['x'])
    # The held-out values cannot create a coefficient learned from all-missing training data.
    assert np.allclose(scores[:2],.5)


def test_temporal_changes_ignore_future_samples_labels_and_preserve_inputs():
    from copy import deepcopy
    key='candle_1s.body_fraction'
    current=dict(row('a','X','winner'),features={key:.8})
    prior=dict(current,offset_s=-5,decision_at=5.,decision_sequence=3,features={key:.3})
    future=dict(current,offset_s=5,decision_at=15.,decision_sequence=8,features={key:999.})
    source=[current,prior,future];frozen=deepcopy(source)
    rows,_,names=temporal_population(source)
    assert source==frozen
    assert rows[0]['features']['change_5s.'+key]==pytest.approx(.5)
    assert 'change_10s.'+key not in rows[0]['features']
    future['features'][key]=-999.;prior['label']='nonwinner'
    changed,_,_=temporal_population(source)
    assert changed[0]['features']==rows[0]['features']
    assert all('label' not in n and 'symbol' not in n for ns in names.values() for n in ns)


@pytest.mark.parametrize('change',[{'decision_at':6.},{'decision_sequence':5},{'symbol':'Y'},{'at':11.}])
def test_temporal_changes_reject_wrong_clock_sequence_or_identity(change):
    current=row('a','X','winner')
    prior=dict(current,offset_s=-5,decision_at=5.,decision_sequence=3)
    prior.update(change)
    with pytest.raises(ValueError,match='causal clock/sequence'):
        temporal_population([current,prior])


def test_temporal_missing_and_duplicate_history_are_explicit():
    current=row('a','X','winner')
    prior=dict(current,offset_s=-5,status='missing_fresh_completed_decision')
    result,_,_=temporal_population([current,prior])
    assert result[0]['temporal_evidence']['-5']['status']=='missing'
    with pytest.raises(ValueError,match='Duplicate historical'):
        temporal_population([current,prior,prior])
