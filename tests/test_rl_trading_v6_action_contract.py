import pytest

from research.rl_trading.v6.action_contract import ActionAxes, ACTION_NAMES


def test_wait_and_hold_have_distinct_identity_axes_without_order_offset_changes():
    axes = ActionAxes(1000, 2)
    assert axes.width == 1009
    assert ACTION_NAMES[axes.action_class(0)] == 'wait'
    for slot in range(2):
        hold = axes.hold_base + slot
        assert ACTION_NAMES[axes.action_class(hold)] == 'hold'
        assert axes.held_slot(hold) == slot
        assert axes.parameter_kind(hold) == 0
        assert axes.execution_action(hold) == 0
        for action in range(2, 5):
            token = 1001 + (action - 2) * 2 + slot
            assert axes.action_class(token) == action
            assert axes.execution_action(token) == action
            assert axes.held_slot(token) == slot


def test_flat_account_has_wait_and_entries_but_no_hold_tokens():
    axes = ActionAxes(3, 0)
    assert axes.width == 4
    assert [axes.action_class(t) for t in range(4)] == [0, 1, 1, 1]
    with pytest.raises(ValueError):
        axes.action_class(4)
    with pytest.raises(ValueError):
        axes.held_slot(0)


@pytest.mark.parametrize('n,h', [(0, 0), (2, 3), (2, -1), (True, 0)])
def test_invalid_axes_fail_closed(n, h):
    with pytest.raises(ValueError):
        ActionAxes(n, h)
