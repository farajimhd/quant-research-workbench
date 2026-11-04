import copy
import numpy as np
import pytest
from research.vectorized_backtest.v66.behavior_selection import session_keys, population_groups, representatives


def metrics():
    m = {k: [[1, 2], [1, 2], [2, 1]] for k in
         ('episode_listings', 'episode_entry_clocks', 'episode_exit_clocks', 'episode_profits')}
    m.update({k: [[], [], []] for k in ('open_episode_listings', 'open_episode_realized')})
    m.update({k: [3., 3., 3.] for k in ('net_pnl', 'drawdown', 'closed_positions',
             'winning_positions', 'capital_dollar_seconds', 'turnover', 'terminal_valid')})
    m['admission_requests'] = [2, 2, 2]
    return m


def test_equal_totals_do_not_collapse_distinct_episode_paths():
    labels, report = population_groups([metrics()], 3)
    assert labels == [0, 0, 1]
    assert report['largest_group'] == 2
    assert representatives([1, 0, 2], labels) == [1, 2]


@pytest.mark.parametrize('field', ['episode_listings', 'episode_entry_clocks', 'episode_exit_clocks',
                                  'episode_profits', 'open_episode_realized', 'turnover',
                                  'capital_dollar_seconds', 'admission_requests'])
def test_retained_execution_evidence_distinguishes_behavior(field):
    m = metrics()
    if isinstance(m[field][1], list): m[field][1] = [99]
    else: m[field][1] += 1
    assert len(set(session_keys(m))) == 3


def test_cached_keys_and_day_order_are_deterministic_and_do_not_mutate_inputs():
    m = metrics(); before = copy.deepcopy(m)
    expected = population_groups([m, m], 3)[0]
    cached = dict(m, selection_behavior_keys=session_keys(m))
    assert population_groups([cached, cached], 3)[0] == expected
    assert m == before
    changed = copy.deepcopy(m); changed['net_pnl'][1] += .00000001
    assert population_groups([m, changed], 3)[0] == [0, 1, 2]


def test_fail_closed_for_incomplete_or_nonfinite_evidence():
    m = metrics(); m['turnover'][0] = np.nan
    with pytest.raises(ValueError): session_keys(m)
    with pytest.raises(ValueError): population_groups([], 3)


def test_cloning_does_not_multiply_parent_opportunities():
    labels = [0]*100 + [1, 2]
    assert representatives(list(range(102)), labels) == [0, 100, 101]
