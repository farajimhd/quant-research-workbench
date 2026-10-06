import numpy as np
import pytest
from research.rl_trading.v6.prepare_price_forecast_targets import observed_returns


def fixture():
    features=np.zeros((6,147),np.float32);features[1:,3]=np.log([100,101,102,103,104]);features[1:,35]=1
    windows=np.zeros((4,120),np.int32);windows[:,-1]=np.arange(1,5)
    return dict(features=features,windows=windows,clock=np.array([10,20,30,40]),max_input_clock=np.array([0,10,20,30]),
        identity=np.zeros(4,np.int64),action=np.ones(4,np.int64),held=np.zeros((4,11),np.float32))


def test_forecast_targets_require_exact_clock_binding_and_mask_boundary():
    data=fixture();result=observed_returns(data)
    np.testing.assert_array_equal(result['mask'][0],[True,True,True,False,False])
    np.testing.assert_allclose(result['return_bps'][0,:3],np.log(np.array([101,102,103])/100)*10000,atol=.004)
    np.testing.assert_array_equal(result['target_close_us'][0],[10,20,30,40,-1])
    assert result['unavailable_counts']==[1,2,3,4,4]
    data['features'][5,3]=999 # Unreferenced final price cannot become a target by index arithmetic.
    np.testing.assert_array_equal(observed_returns(data)['return_bps'],result['return_bps'])


def test_forecast_rejects_duplicate_or_noncausal_clock_bindings():
    data=fixture();data['max_input_clock'][0]=10
    with pytest.raises(ValueError,match='multiple'):observed_returns(data)
    data=fixture();data['max_input_clock'][0]=40
    with pytest.raises(ValueError,match='strictly prior'):observed_returns(data)
    data=fixture();data['features'][2,35]=0
    with pytest.raises(ValueError,match='valid price'):observed_returns(data)
