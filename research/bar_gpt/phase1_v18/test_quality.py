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
