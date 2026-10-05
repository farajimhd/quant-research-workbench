"""Fresh source approval remains exact and does not admit withdrawn numbers."""
from pathlib import Path

import pytest

from src.backend.backtest_strategy_forty_six_certification import (
    STRATEGY46_SOURCE_AST, certify_strategy_forty_six_source,
)
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_registry import numbered_strategy_parent


@pytest.mark.parametrize('relative', tuple(STRATEGY46_SOURCE_AST))
def test_each_declared_source_change_invalidates_46_approval(tmp_path, relative):
    source = Path(__file__).parents[1] / relative
    changed = tmp_path / source.name
    changed.write_text(source.read_text(encoding='utf-8') + '\nUNREVIEWED_RULE_CHANGE = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='pinned release source changed'):
        certify_strategy_forty_six_source(source_overrides={relative: changed})


@pytest.mark.parametrize('number', [43, 44, 45])
def test_withdrawn_numbers_are_not_restored(number):
    with pytest.raises(ValueError):
        numbered_fixed_strategy(number)


def test_46_inherits_exact_42_capabilities_with_one_declared_extension():
    current, parent = numbered_fixed_strategy(46), numbered_fixed_strategy(42)
    assert numbered_strategy_parent(46) == 42
    for name in ('allows_session_exit', 'allows_adds', 'allows_completed_30s_trailing',
                 'allows_target_escalation', 'caps_entry_at_reference_ask', 'allows_followthrough_failure_exit'):
        assert getattr(current, name) == getattr(parent, name)
    assert parent.early_original_risk_policy is None
    assert current.early_original_risk_policy.afterhours_fraction == (1, 4)
