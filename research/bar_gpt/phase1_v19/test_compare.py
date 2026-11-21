from .compare import compare


def test_aggregate_improvement_cannot_hide_regression():
    reference={'all_visible/raw_mae/1s/trade_close':1.,'masked/raw_mae/100ms/bid_size':100.,
               'all_visible/candle/1s/trade/moving_direction_pct':88.}
    candidate={**reference,'masked/raw_mae/100ms/bid_size':1.,'all_visible/raw_mae/1s/trade_close':1.01}
    result=compare(candidate,reference)
    assert not result['strict_no_regression'] and result['error_regressions']==1
    candidate['all_visible/raw_mae/1s/trade_close']=.99
    assert compare(candidate,reference)['strict_no_regression']


def test_training_guard_and_resume_traversal(tmp_path):
    import pytest
    from .train import protected_regression
    from .run import verify_resume_workers
    reference={'all_visible/raw_mae/1s/trade_close':1.,'all_visible/candle/1s/trade/moving_direction_pct':88.}
    assert not protected_regression(reference,reference)[0]
    assert protected_regression({**reference,'all_visible/raw_mae/1s/trade_close':1.11},reference)[0]
    assert protected_regression({**reference,'all_visible/candle/1s/trade/moving_direction_pct':86.9},reference)[0]
    (tmp_path/'manifest.json').write_text('{"workers":16}')
    verify_resume_workers(tmp_path,16)
    with pytest.raises(ValueError,match='worker count'):verify_resume_workers(tmp_path,32)
