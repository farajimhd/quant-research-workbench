"""Prepared episode veto resets causally and never treats absence as failure."""
import numpy as np
import pytest

from src.trading_runtime.strategy_episode_activity_veto import episode_activity_veto_mask


def masks(groups, parent, activity, faded):
    return episode_activity_veto_mask(np.asarray(groups, dtype=np.int64),
        *(np.asarray(values, dtype=np.bool_) for values in (parent, activity, faded)))


def test_activity_recovery_cannot_reenter_failed_original_episode():
    admitted, veto = masks([0, 0, 0, 1, 1], [True] * 5,
                          [True, False, True, True, True], [False, True, False, False, False])
    assert admitted.tolist() == [True, False, False, True, True]
    assert veto.tolist() == [False, True, True, False, False]


def test_missing_observation_does_not_poison_later_certified_activity():
    admitted, veto = masks([0, 0], [True, True], [False, True], [False, False])
    assert admitted.tolist() == [False, True] and not veto.any()


def test_parent_rejections_and_inactive_history_never_create_a_veto():
    admitted, veto = masks([0, 0, 0], [False, True, True], [True, True, True], [False] * 3)
    assert admitted.tolist() == [False, True, True] and not veto.any()


def test_prefixes_match_scalar_reference_and_appending_future_cannot_change_past():
    rng = np.random.default_rng(3700)
    groups = np.repeat(np.arange(25, dtype=np.int64), 40)
    parent = rng.random(len(groups)) > .2
    missing = rng.random(len(groups)) < .1
    faded = parent & ~missing & (rng.random(len(groups)) < .1)
    activity = ~missing & ~faded
    admitted, veto = episode_activity_veto_mask(groups, parent, activity, faded)
    expected, blocked = [], False
    for index, group in enumerate(groups):
        if index == 0 or group != groups[index - 1]:
            blocked = False
        blocked |= bool(faded[index])
        expected.append(bool(parent[index] and activity[index] and not blocked))
    assert admitted.tolist() == expected
    for stop in (1, 39, 40, 41, 135, 999):
        prefix, prefix_veto = episode_activity_veto_mask(groups[:stop], parent[:stop], activity[:stop], faded[:stop])
        assert np.array_equal(prefix, admitted[:stop])
        assert np.array_equal(prefix_veto, veto[:stop])


@pytest.mark.parametrize('parent,activity', [(False, False), (True, True)])
def test_contradictory_confirmed_evidence_is_rejected(parent, activity):
    with pytest.raises(ValueError, match='contradicts'):
        masks([0], [parent], [activity], [True])


@pytest.mark.parametrize('groups', [[-1], [1, 0], [0, 1, 0]])
def test_group_aliasing_or_reordering_is_rejected(groups):
    with pytest.raises(ValueError, match='ordered Int64'):
        masks(groups, [True] * len(groups), [True] * len(groups), [False] * len(groups))


def test_shapes_and_types_are_exact_and_empty_input_is_bounded():
    with pytest.raises(ValueError):
        episode_activity_veto_mask(np.array([0], dtype=np.int32), np.array([True]), np.array([True]), np.array([False]))
    with pytest.raises(ValueError):
        masks([0, 0], [True], [True, True], [False, False])
    admitted, veto = masks([], [], [], [])
    assert admitted.shape == veto.shape == (0,) and admitted.dtype == veto.dtype == np.bool_


def test_prepared_reducer_does_not_register_successor():
    from src.trading_runtime.strategy_registry import numbered_strategy
    with pytest.raises(ValueError):
        numbered_strategy(37)
