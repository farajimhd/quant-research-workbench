"""Real certified parents preserve initiality when relaxing first growth."""
from dataclasses import replace

import numpy as np
import pytest

from test_backtest_strategy_initial_momentum import plans
from src.backend.backtest_strategy_initial_momentum_growth import compile_initial_momentum_growth_plan
from src.backend.backtest_strategy_initial_ten_percent import (
    CertifiedInitialTenPercentPlan, compile_initial_ten_percent_plan,
)
from src.trading_runtime.strategy_initial_ten_percent import (
    first_setup_ten_percent_policy_payload, first_setup_ten_percent_entry,
)
from src.backend.backtest_strategy_initial_price_break import stage_initial_price_break_plan


def test_twenty_percent_first_is_admitted_without_changing_older_fifty_percent():
    candidates, entry, momentum, source = plans()
    old = compile_initial_momentum_growth_plan(candidates, entry, momentum)
    relaxed = compile_initial_ten_percent_plan(candidates, entry, momentum)
    assert old.eligible_mask.tolist() == [False, False]
    assert relaxed.eligible_mask.tolist() == [True, True]
    assert relaxed.initial.first_indices.tolist() == [0, 0]
    assert relaxed.candidates is candidates and relaxed.entry is entry
    assert relaxed.momentum is momentum
    anchor = relaxed.lookup('AAA', 41_000)
    assert anchor.first_setup == momentum.lookup('AAA', 31_000)
    assert first_setup_ten_percent_entry(anchor.first_setup)
    selection = relaxed.selection_witness('AAA', 41_000)
    assert selection.initial == anchor and selection.selection_token == relaxed.token
    assert relaxed.token != old.token and relaxed.token != relaxed.initial.token
    assert len(source.queries) == 1  # Both compilers do no additional source I/O.


def test_first_under_ten_cannot_be_replaced_by_later_strong_candidate():
    candidates, entry, momentum, source = plans(weak_first=True)
    relaxed = compile_initial_ten_percent_plan(candidates, entry, momentum)
    assert momentum.eligible_mask(17).tolist() == [False, True]
    assert relaxed.initial.first_indices.tolist() == [0, 0]
    assert relaxed.eligible_mask.tolist() == [False, False]
    with pytest.raises(ValueError, match='outside admitted'):
        relaxed.lookup('AAA', 41_000)
    assert len(source.queries) == 1


def test_invalid_structural_row_does_not_become_first_anchor():
    candidates, entry, momentum, _ = plans(weak_first=True, first_invalid=True)
    relaxed = compile_initial_ten_percent_plan(candidates, entry, momentum)
    assert relaxed.initial.first_indices.tolist() == [1, 1]
    assert relaxed.eligible_mask.tolist() == [False, True]
    assert relaxed.lookup('AAA', 41_000).first_setup == momentum.lookup('AAA', 41_000)


def test_sealed_mask_and_parent_cannot_be_forged_or_mutated():
    candidates, entry, momentum, _ = plans()
    relaxed = compile_initial_ten_percent_plan(candidates, entry, momentum)
    with pytest.raises(ValueError):
        relaxed.eligible_mask.setflags(write=True)
    with pytest.raises(ValueError, match='eligibility differs'):
        replace(relaxed, eligible_mask=np.array([True, False]))
    with pytest.raises(ValueError, match='content seal'):
        replace(relaxed, token='f' * 64)
    with pytest.raises(ValueError, match='exact certified'):
        CertifiedInitialTenPercentPlan(object(), np.array([], dtype=bool), 'f' * 64)


def test_staged_policy_cannot_bypass_installed_price_parent_authority():
    candidates, entry, momentum, _ = plans()
    relaxed = compile_initial_ten_percent_plan(candidates, entry, momentum)
    # The staged type is admitted for native price research, but missing source
    # columns still reject; unregistered successor execution remains closed.
    with pytest.raises(ValueError, match='six aligned native producer columns'):
        stage_initial_price_break_plan(relaxed, ())
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    with pytest.raises(ValueError, match='No installed'):
        numbered_fixed_strategy(30)
    policy = first_setup_ten_percent_policy_payload()
    assert policy['fraction'] == 0.10
    assert policy['changed_session_scope'] == 'premarket_only'
    assert policy['afterhours_first_setup_policy'] == 'unchanged_strict_10pct'
