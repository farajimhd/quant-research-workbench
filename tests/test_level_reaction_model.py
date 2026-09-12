from copy import deepcopy
from datetime import datetime
import numpy as np
import pandas as pd
import pytest
from research.reaction_levels.v1.data import feature_rows
from research.reaction_levels.v1.objectives import label_rows


def example():
    start=int(datetime.fromisoformat('2026-07-01T04:00:00-04:00').timestamp())
    bars=[dict(t=start+i,open=100.,high=100.01,low=99.99,close=100.,volume=100) for i in range(1,181)]
    level=lambda p:dict(id=str(p),lower=p-.05,upper=p+.05,price=p,support_rejections=3,resistance_rejections=3,
                        accepted_crossings=1,contributions=[{}],role_segments=[dict(role='resistance')],strength_status='qualified')
    inputs=dict(bars=bars,quotes=[],source=dict(start='2026-07-01T04:00:00-04:00',end='2026-07-01T04:03:00-04:00'))
    return inputs,dict(available_at=start-1,levels=[level(99),level(101)])


def test_prefix_features_ignore_future_and_pair_is_causal():
    inputs,book=example();rows,features,audit,grid=feature_rows(inputs,book)
    changed=deepcopy(inputs)
    for b in changed['bars'][100:]:b.update(close=200,high=201,low=199,volume=100000)
    other,features2,_,_=feature_rows(changed,book)
    a=rows[rows.grid_index<100];b=other[other.grid_index<100]
    pd.testing.assert_frame_equal(a[['t','level_id']+features].reset_index(drop=True),b[['t','level_id']+features2].reset_index(drop=True))
    assert set(rows[rows.target_upper==1].level_id)=={'101'}
    assert set(rows[rows.target_upper==0].level_id)=={'99'}
    assert rows[features].select_dtypes(include='object').empty
    book['available_at']+=86400
    with pytest.raises(ValueError,match='Future'):feature_rows(inputs,book)


def test_no_touch_censor_reject_and_two_close_break():
    inputs,book=example();rows,_,_,grid=feature_rows(inputs,book)
    sample=rows[(rows.target_upper==1)&(rows.grid_index==59)]
    assert label_rows(sample,grid,book['levels']).label.iloc[0]==0
    missing=grid.copy();missing.iloc[61:67]=np.nan
    assert label_rows(sample,missing,book['levels']).label.iloc[0]==-1
    rejection=grid.copy();rejection.iloc[60,rejection.columns.get_loc('high')]=101.
    assert label_rows(sample,rejection,book['levels']).label.iloc[0]==1
    breakout=grid.copy()
    for j in (60,61):
        breakout.iloc[j,breakout.columns.get_loc('high')]=101.2
        breakout.iloc[j,breakout.columns.get_loc('close')]=101.1
    result=label_rows(sample,breakout,book['levels'])
    assert result.label.iloc[0]==2 and result.label_end.iloc[0]==grid.index[61]
    one=breakout.copy();one.iloc[61,one.columns.get_loc('close')]=101.
    # A single breakout close is insufficient; later retreat qualifies rejection.
    assert label_rows(sample,one,book['levels']).label.iloc[0]==1


def test_missing_side_and_inside_band_and_quote_age():
    inputs,book=example();book['levels']=book['levels'][:1]
    inputs['quotes']=[dict(t=inputs['bars'][59]['t'],ask=100.01,bid=99.99,ask_size=200,bid_size=300,sip_us=(inputs['bars'][59]['t']-.1)*1e6)]
    rows,_,audit,_=feature_rows(inputs,book)
    assert set(rows.target_upper)=={0}
    assert audit['missing_upper']>0
    assert rows.iloc[0].quote_valid==1
    assert rows[rows.grid_index==70].quote_valid.iloc[0]==0
    book['levels'][0].update(lower=99.95,upper=100.05,price=100)
    rows,_,_,_=feature_rows(inputs,book)
    assert set(rows.target_upper)=={0,1}
    assert (rows.upper_inside==1).all() and (rows.lower_inside==1).all()


def test_labels_never_follow_new_pair_or_past_horizon():
    inputs,book=example();rows,_,_,grid=feature_rows(inputs,book)
    sample=rows[(rows.target_upper==1)&(rows.grid_index==59)]
    grid.iloc[120,grid.columns.get_loc('high')]=102
    assert label_rows(sample,grid,book['levels']).label.iloc[0]==0
    end=rows[(rows.target_upper==1)&(rows.grid_index==170)]
    assert label_rows(end,grid,book['levels']).label.iloc[0]==-1


def test_lower_side_is_symmetric_and_later_gap_does_not_erase_resolution():
    inputs,book=example();rows,_,_,grid=feature_rows(inputs,book)
    sample=rows[(rows.target_upper==0)&(rows.grid_index==59)]
    reject=grid.copy();reject.iloc[60,reject.columns.get_loc('low')]=99.
    reject.iloc[70:80]=np.nan
    result=label_rows(sample,reject,book['levels'])
    assert result.label.iloc[0]==1 and result.label_end.iloc[0]==grid.index[60]
    broken=grid.copy()
    for j in (60,61):
        broken.iloc[j,broken.columns.get_loc('low')]=98.8
        broken.iloc[j,broken.columns.get_loc('close')]=98.9
    result=label_rows(sample,broken,book['levels'])
    assert result.label.iloc[0]==2 and result.label_end.iloc[0]==grid.index[61]


def test_split_adjustment_preserves_previous_book_and_estimate_snapshot(monkeypatch):
    from pathlib import Path
    # Match the documented script entry point's import path.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    from research.reaction_levels.v1.train import adjusted
    _,book=example();book['levels'][0]['reaction_center']={'roles':{'support':{'center':99.,'scale':.1}}}
    saved=deepcopy(book);updated=adjusted(book,.5)
    assert book==saved
    assert updated['levels'][0]['price']==49.5
    assert updated['levels'][0]['reaction_center']['roles']['support']['scale']==.05


def test_quote_query_uses_both_utc_year_partitions(monkeypatch):
    from pathlib import Path
    from research.reaction_levels.v1 import source
    captured=[]
    monkeypatch.setattr(source,'source_metadata',lambda *a,**k:({'token':'fixed'},[]))
    monkeypatch.setattr(source,'load',lambda *a,**k:([],[],{'revision':{'token':'fixed'}}))
    monkeypatch.setattr(source,'_query',lambda sql:captured.append(sql) or [])
    monkeypatch.setattr(source,'write_json',lambda *a,**k:None)
    source.session_inputs(Path('nonexistent-test-reaction-source'),'TEST','2026-12-31')
    assert len(captured)==1 and 'events_(2026|2027)' in captured[0]
    assert 'bitAnd(event_meta,4)' in captured[0]


def test_recent_baseline_uses_only_supplied_calibration_counts():
    from research.reaction_levels.v1.audit import frequency_baseline
    p=frequency_baseline([1,2,3,4])
    np.testing.assert_allclose(p,np.array([2,3,4,5])/14)
    with pytest.raises(ValueError):frequency_baseline([1,-1,2,3])


def test_empty_seed_keeps_schema_and_explicit_zero_example_partition():
    inputs,book=example();_,expected,_,_=feature_rows(inputs,book)
    book['levels']=[]
    rows,names,audit,grid=feature_rows(inputs,book)
    assert names==expected and rows.empty and audit['reason']=='no_historical_levels'
    labeled=label_rows(rows,grid,[])
    assert labeled.empty and 'label' in labeled
