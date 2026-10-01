from dataclasses import replace
import pytest
from src.trading_runtime.strategy_profit_giveback_arm import profit_arm_candidate
from test_strategy_profit_giveback_source import fixture


@pytest.mark.parametrize('number', [31, 32, 33])
def test_one_checkpoint_candidate_from_completed_manager_high(number):
    _,state,held=fixture(strategy_number=number)
    candidate=profit_arm_candidate(state,held,already_checkpointed=False)
    assert candidate.high_int==110000 and candidate.boundary_ms==9900
    assert profit_arm_candidate(state,held,already_checkpointed=True) is None


def test_unarmed_or_pending_exit_does_not_request_checkpoint():
    _,state,held=fixture();key=state.position_highs[0][0]
    assert profit_arm_candidate(replace(state,position_highs=((key,109999),)),held,already_checkpointed=False) is None
    assert profit_arm_candidate(state,replace(held,pending_exit=True),already_checkpointed=False) is None
    assert profit_arm_candidate(state,replace(held,position_quantity=0.),already_checkpointed=False) is None


@pytest.mark.parametrize('family',['submitted','positions','position_highs','first_held_boundaries'])
def test_missing_capture_family_cannot_arm(family):
    _,state,held=fixture()
    with pytest.raises(ValueError):profit_arm_candidate(replace(state,**{family:()}),held,already_checkpointed=False)


def test_prior_release_position_cannot_request_profit_checkpoint():
    _,state,held=fixture();key,source=state.submitted[0]
    with pytest.raises(ValueError):
        profit_arm_candidate(replace(state,submitted=((key,replace(source,strategy_number=30)),)),held,already_checkpointed=False)


@pytest.mark.parametrize('quantity',[float('nan'),float('inf'),-1.,True])
def test_invalid_financial_quantity_cannot_arm(quantity):
    _,state,held=fixture()
    with pytest.raises(ValueError):
        profit_arm_candidate(state,replace(held,position_quantity=quantity),already_checkpointed=False)
