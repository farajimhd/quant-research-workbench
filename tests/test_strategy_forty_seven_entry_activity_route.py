"""Exercise independent native source-type selection at the real entry sealer."""
from dataclasses import replace

import pytest

from test_arte_episode_activity_v4 import graph37, seal
from src.backend.backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
from src.trading_runtime.arte_entry_activity_v4 import seal_certified_entry_activity_rows


@pytest.mark.parametrize('number', [46, 47])
def test_successor_entry_activity_seals_with_exact_episode_authority(number):
    args = graph37(number)
    sealed = seal(*args)
    assert len(sealed) == 1
    assert sealed[0]['strategy_number'] == number
    assert sealed[0]['activity_source_token'] == args[4].gate.token
    assert seal(sealed[0], *args[1:]) == sealed


@pytest.mark.parametrize('number', [46, 47])
def test_successor_source_cannot_use_legacy_entry_activity_authority(number):
    row, entry, intent, event, source = graph37(number)
    wrong = EntryActivityReadbackAuthority('activity-run', source.plan)
    with pytest.raises(ValueError, match='independent certified'):
        seal(row, entry, intent, event, wrong)


@pytest.mark.parametrize('number,foreign', [(46, 47), (47, 46)])
def test_episode_authority_is_bound_to_exact_successor_number(number, foreign):
    row, entry, intent, event, source = graph37(number)
    with pytest.raises(ValueError, match='another numbered entry'):
        seal(row, entry, intent, event, replace(source, strategy_number=foreign))


def test_mixed_successor_entries_cannot_share_one_numbered_authority():
    first = graph37(46)
    second = graph37(47)
    with pytest.raises(ValueError, match='independent certified'):
        seal_certified_entry_activity_rows(
            (first[0], second[0]), (first[1], second[1]),
            (first[2], second[2]), (first[3], second[3]),
            run_id='activity-run', source=first[4])
