import pytest
from .quality import score


def test_balanced_score_and_missing_groups():
    reference={'all_visible/raw_mae/1s/trade_close':2.,'masked/raw_mae/1s/trade_close':4.,
               'activity/all_visible/1s/trade_volume/moving_direction_pct':60.}
    baseline,_=score(reference,reference)
    improved=dict(reference);improved['all_visible/raw_mae/1s/trade_close']=1.
    improved['activity/all_visible/1s/trade_volume/moving_direction_pct']=70.
    assert score(improved,reference)[0]<baseline
    worse=dict(reference);worse['masked/raw_mae/1s/trade_close']=44.
    assert score(worse,reference)[1]['worst_raw_ratio']==11
    del improved['masked/raw_mae/1s/trade_close']
    with pytest.raises(ValueError):score(improved,reference)


def test_resume_cannot_change_traversal_workers(tmp_path):
    from .run import verify_resume_workers
    (tmp_path/'manifest.json').write_text('{"workers":16}')
    verify_resume_workers(tmp_path,16)
    with pytest.raises(ValueError,match='worker count'):verify_resume_workers(tmp_path,32)
