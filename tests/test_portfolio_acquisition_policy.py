from copy import deepcopy
import pytest

from src.trading_runtime.portfolio_acquisition_policy import RULE, PARAMETER, declared_acquisition_limit


def configuration(maximum=1):
    return {'strategy':{'numbered_release':{'contract':{'rule_set_contracts':[RULE]}},
        'parameters':{PARAMETER:{'maximum_accepted_acquisitions_per_ticker_session':maximum,
            'scope':'independent_native_session'}}}}


@pytest.mark.parametrize('maximum',[1,2,7,32])
def test_limit_is_declared_parameter_and_does_not_mutate_configuration(maximum):
    payload=configuration(maximum); before=deepcopy(payload)
    assert declared_acquisition_limit(payload)==maximum
    assert payload==before


def test_undeclared_legacy_release_has_no_quota():
    payload=configuration()
    payload['strategy']['numbered_release']['contract']['rule_set_contracts']=[]
    payload['strategy']['parameters'].clear()
    assert declared_acquisition_limit(payload) is None


def test_null_parameter_still_requires_declared_rule():
    payload=configuration()
    payload['strategy']['numbered_release']['contract']['rule_set_contracts']=[]
    payload['strategy']['parameters'][PARAMETER]=None
    with pytest.raises(ValueError): declared_acquisition_limit(payload)


@pytest.mark.parametrize('maximum',[0,-1,33,True,1.0,None])
def test_invalid_limit_is_rejected(maximum):
    with pytest.raises(ValueError): declared_acquisition_limit(configuration(maximum))


@pytest.mark.parametrize('change',['duplicate','missing','unselected','extra','scope'])
def test_ambiguous_or_incomplete_contract_is_rejected(change):
    payload=configuration(); strategy=payload['strategy']; block=strategy['parameters'][PARAMETER]
    if change=='duplicate': strategy['numbered_release']['contract']['rule_set_contracts'].append(RULE)
    elif change=='missing': strategy['parameters'].clear()
    elif change=='unselected': strategy['numbered_release']['contract']['rule_set_contracts'].clear()
    elif change=='extra': block['strategy_number']=999
    else: block['scope']='current_resume_cursor'
    with pytest.raises(ValueError): declared_acquisition_limit(payload)
