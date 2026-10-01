from dataclasses import replace
import pytest
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from src.trading_runtime.strategy_one_position import ProtectionState
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
from src.trading_runtime.strategy_profit_giveback import profit_giveback
from src.trading_runtime.strategy_profit_giveback_source import validate_profit_giveback_state
from test_strategy_profit_giveback import sample
from test_strategy_profit_giveback_exit import financial


def fixture():
    held = financial()
    key = (held.account_id, held.assignment_id, held.ticker)
    source = StrategyOneEntryProposal(held.assignment_id, held.account_id, held.ticker,
        900, 0, 10., 9., 12., 'R3', .5, 800, 'S1', 31)
    state = StrategyOneManagementState(9900, ((key, source),),
        ((key, ProtectionState(9900, 9., 12.)),), (), ((key, 110000),), (), ((key, 1000),))
    return profit_giveback(sample()), state, held


def test_scalar_state_binds_original_risk_prior_high_and_exact_identity():
    witness, state, held = fixture()
    assert validate_profit_giveback_state(witness, state, held) == state.submitted[0][1]


@pytest.mark.parametrize('field,value', [('reference_ask',10.01),('initial_stop',8.9),
                                       ('strategy_number',30),('boundary_ms',1000),('ticker','OTHER')])
def test_different_original_entry_is_rejected(field,value):
    witness,state,held=fixture();key,source=state.submitted[0]
    with pytest.raises(ValueError):
        validate_profit_giveback_state(witness,replace(state,submitted=((key,replace(source,**{field:value})),)),held)


@pytest.mark.parametrize('family',['submitted','positions','position_highs','first_held_boundaries'])
def test_missing_or_duplicate_position_family_is_rejected(family):
    witness,state,held=fixture()
    for changed in ((),getattr(state,family)*2):
        with pytest.raises(ValueError):validate_profit_giveback_state(witness,replace(state,**{family:changed}),held)


def test_changed_high_clock_or_account_cannot_bind():
    witness,state,held=fixture();key=state.position_highs[0][0]
    for changed in (replace(state,boundary_ms=9800),replace(state,position_highs=((key,111000),)),
                    replace(state,first_held_boundaries=((key,1100),))):
        with pytest.raises(ValueError):validate_profit_giveback_state(witness,changed,held)
    with pytest.raises(ValueError):validate_profit_giveback_state(witness,state,replace(held,account_id='other'))
