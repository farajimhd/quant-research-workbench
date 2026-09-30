import numpy as np
import pytest

from research.rl_trading.v6.account_observation import observe_account
from research.rl_trading.v6.causal_order_queue import CausalOrderQueue
from research.rl_trading.v6.oms import BracketAccount, Quote


def test_observation_binds_confirmed_holding_and_armed_children():
    account = BracketAccount()
    queue = CausalOrderQueue(account)
    quote = Quote(1_100_000, 1_050_000, 9.99, 10., 500., 500., True)
    assert account.enter_long('ABC', decision_us=1_000_000,
                              decision_close=10., budget=1_000.,
                              quote=quote) > 0
    snapshot = observe_account(account, ('ZZZ', 'ABC'),
        {'ABC': (10.5, 2_000_000)}, close_us=2_000_000,
        queue=queue)
    assert snapshot.held_index.tolist() == [1]
    assert snapshot.held_features.shape == (1, 11)
    assert snapshot.held_features[0,9:].tolist() == [0.,0.]
    assert snapshot.held_features[0, 6:9].tolist() == [0., 0., 0.]
    assert snapshot.stop_allowed.tolist() == [True]
    assert snapshot.target_allowed.tolist() == [True]
    assert snapshot.account[1] > account.initial_cash
    account.set_stop('ABC', price=9.5, clock_us=2_100_000)
    armed = observe_account(account, ('ZZZ', 'ABC'),
        {'ABC': (10.5, 3_000_000)}, close_us=3_000_000,
        queue=queue)
    assert armed.held_features[0, 6] == 1
    assert armed.stop_allowed.tolist() == [False]
    assert armed.target_allowed.tolist() == [True]
    assert np.isfinite(armed.account).all()


def test_observation_rejects_future_mark_and_missing_identity():
    account = BracketAccount()
    queue = CausalOrderQueue(account)
    account.enter_long('ABC', decision_us=1_000_000, decision_close=10.,
        budget=1_000., quote=Quote(1_100_000, 1_050_000,
                                   9.99, 10., 500., 500., True))
    with pytest.raises(ValueError, match='certified listing'):
        observe_account(account, ('XYZ',), {'ABC': (10., 2_000_000)},
                        close_us=2_000_000, queue=queue)
    with pytest.raises(ValueError, match='causal positive mark'):
        observe_account(account, ('ABC',), {'ABC': (10., 3_000_000)},
                        close_us=2_000_000, queue=queue)
    with pytest.raises(ValueError, match='causal positive mark'):
        observe_account(account, ('ABC',), {}, close_us=2_000_000,
                        queue=queue)


def test_pending_entry_is_visible_without_fabricating_a_holding():
    account = BracketAccount()
    queue = CausalOrderQueue(account)
    queue.submit_entry('ABC', decision_us=1_000_000, order_index=0,
        decision_close=10., desired_budget=1_000.,
        quote=Quote(1_100_000, 1_090_000, 9.99, 10., 100., 100., True))
    snapshot = observe_account(account, ('ABC',), {},
        close_us=1_000_000, queue=queue)
    assert snapshot.held_index.size == 0
    assert snapshot.account.shape == (7,)
    assert snapshot.account[-2:].tolist() == [1_000., 1.]


def test_pending_exit_masks_duplicate_exit_and_child_orders():
    account = BracketAccount()
    queue = CausalOrderQueue(account)
    account.enter_long('ABC', decision_us=1_000_000, decision_close=10.,
        budget=1_000.,
        quote=Quote(1_100_000, 1_090_000, 9.99, 10., 100., 100., True))
    assert not queue.submit_exit('ABC', decision_us=2_000_000,
                                 order_index=0, quotes=iter(()))
    snapshot = observe_account(account, ('ABC',),
        {'ABC': (10., 2_000_000)}, close_us=2_000_000, queue=queue)
    assert not snapshot.exit_allowed.any()
    assert not snapshot.stop_allowed.any()
    assert not snapshot.target_allowed.any()
