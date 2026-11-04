import pytest
from research.vectorized_backtest.v66.review_price_checks import probe
from research.vectorized_backtest.v66.price_program import Prices,Expression


def test_missing_swing_is_not_silently_filled_and_spread_remains_mandatory():
    prices=Prices(stop=Expression('swing.low.45.20',offset=-.03))
    result=probe(prices,[1.,10.,100.])
    assert result['above_500_guard_pass']==0
    assert result['endpoints']['stop']['missing_input_results']['swing.low.45.20']['nonfinite']==18
    assert result['endpoints']['buy']['missing_input_results']['bid']['finite']==18
    assert result['endpoints']['buy']['missing_input_results']['ask']['nonfinite']==18
    with pytest.raises(ValueError,match='anchors'):probe(prices,[float('nan')])
