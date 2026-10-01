"""Installed fixed registration is immutable, real, and never a live executor."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.trading_runtime.strategy_registry import (
    fixed_strategy_executor, initialize_numbered_fixed_strategies,
    installed_strategy_definitions, numbered_strategy, register_fixed_strategy_executor,
    register_numbered_strategy, unregister_strategy_executor,
)
from src.trading_runtime.strategy_engine import StrategyAssignment, StrategyPermissions, AssignmentStatus
from src.trading_runtime.strategy_two_release import release_contract, verify_installed_strategy_two_release


def assignment():
    return StrategyAssignment(assignment_id="strategy2-test", strategy_id="early-squeeze-strategy",
        strategy_revision=2, account_id="SIM", ticker="TEST", conid=1,
        status=AssignmentStatus.WATCHING, permissions=StrategyPermissions(enter=True),
        parameters={}, state={}, source="test",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("number", [2, 6, 7])
def test_installed_number_uses_real_fixed_runtime_not_legacy_callbacks(number):
    initialize_numbered_fixed_strategies()
    initialize_numbered_fixed_strategies()
    release = numbered_strategy(number)
    if number == 2:
        assert release == release_contract()
    registration = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    selected = replace(assignment(), strategy_revision=number)
    runtime = registration.build([selected], mode="backtest")
    assert runtime.revision == number and runtime.contract.strategy_number == number
    assert runtime.contract.allows_target_escalation is (number not in (6, 7))
    assert not any(row["strategy_id"] == release.executor_strategy_id for row in installed_strategy_definitions())
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([selected], mode="live")
    with pytest.raises(ValueError, match="assignments differ"):
        registration.build([replace(assignment(), strategy_revision=1)], mode="backtest")


def test_registered_fixed_executor_and_number_cannot_be_replaced_or_unregistered():
    release = numbered_strategy(2)
    fixed = fixed_strategy_executor(release.executor_strategy_id, 2)
    with pytest.raises(ValueError, match="cannot be replaced"):
        register_fixed_strategy_executor(replace(fixed, strategy_factory=lambda rows: rows))
    with pytest.raises(ValueError, match="cannot be unregistered"):
        unregister_strategy_executor(release.executor_strategy_id, 2)
    changed = replace(release, behavior_specification="foreign behavior")
    with pytest.raises(ValueError, match="immutable"):
        register_numbered_strategy(replace(changed, approved_digest=changed.digest()))
    with pytest.raises(ValueError, match="installed numbered registry seal"):
        verify_installed_strategy_two_release({"contract": release.canonical_payload(), "approved_digest": "0" * 64})


def test_nineteen_inherits_eighteen_execution_capabilities_and_session_authority():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, numbered_session_exit_reason
    from src.trading_runtime.strategy_registry import numbered_strategy_parent
    child = numbered_fixed_strategy(19)
    parent = numbered_fixed_strategy(18)
    assert numbered_strategy_parent(19) == 18
    for name in ('allows_session_exit', 'allows_adds', 'allows_completed_30s_trailing', 'allows_target_escalation',
                 'caps_entry_at_reference_ask', 'allows_followthrough_failure_exit'):
        assert getattr(child, name) == getattr(parent, name)
    assert numbered_session_exit_reason(19) == 'strategy_nineteen_session_exit'
    with pytest.raises(ValueError, match='No installed'):
        numbered_fixed_strategy(25)
