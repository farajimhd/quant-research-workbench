import numpy as np
import pytest
from research.rl_trading.v6.run_tree_bias_control import summary_features,branch_weights


def fixture():
    features=np.zeros((4,147),np.float32)
    features[1:,0]=[2,4,8];features[1:,3]=np.log([100,101,102])
    windows=np.zeros((2,120),np.int32);windows[0,-3:]=[1,2,3]
    return dict(features=features,windows=windows,held=np.zeros((2,11),np.float32),
        market=np.zeros((2,147),np.float32),action=np.array([0,1]))


def test_masked_summaries_use_only_referenced_prior_prices():
    data=fixture();value=summary_features(data,batch_size=1)
    assert value.shape==(2,1336)
    assert value[0,0]==8 and value[0,147]==6
    assert value[0,294]==pytest.approx(14/3)
    assert value[0,441]==pytest.approx(np.std([2,4,8]))
    assert value[0,3]==0 and value[0,150]==pytest.approx(10*np.log(102/100),abs=1e-5)
    np.testing.assert_array_equal(value[1],np.zeros(1336))
    data['features']=np.concatenate((data['features'],np.ones((1,147),np.float32)*999))
    np.testing.assert_array_equal(summary_features(data),value)


def test_feature_summary_is_target_independent_and_state_guarded():
    data=fixture();before=summary_features(data);data['action']=np.array([1,0])
    np.testing.assert_array_equal(summary_features(data),before)
    data['action'][0]=3
    with pytest.raises(ValueError,match='branch'):summary_features(data)
    data=fixture();data['windows'][0,-1]=99
    with pytest.raises(ValueError,match='escaped'):summary_features(data)


def test_branch_balance_preserves_sample_mass_without_double_balance():
    actions=np.array([0,1,1,1]);weight=np.array([2.,1.,3.,6.])
    result=branch_weights(actions,weight,balanced=True)
    assert result.mean()==pytest.approx(1)
    assert result[0]==pytest.approx(result[1:].sum())
    assert result[3]/result[1]==pytest.approx(6)
    np.testing.assert_allclose(branch_weights(actions,weight,balanced=False),weight/weight.mean())
    with pytest.raises(ValueError,match='supported'):branch_weights(np.array([1,1]),np.ones(2),balanced=True)
