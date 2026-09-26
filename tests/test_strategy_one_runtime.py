"""Strategy 1 never falls through the legacy event/frame executor."""
import asyncio

import pytest

from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)
from src.trading_runtime.strategy_one_runtime import AssignedStrategyOne


def _assignment(*, account="DU1", revision=1):
    return StrategyAssignment(
        "A1", "early-squeeze-strategy", revision, account, "AAA", 123,
        AssignmentStatus.WATCHING, StrategyPermissions(enter=True), {})


def test_fixed_runtime_holds_numbered_assignments_without_legacy_evaluation():
    assignment = _assignment()
    strategy = AssignedStrategyOne([assignment])
    assert strategy.assignments() == (assignment,)
    assert strategy.automatic is True
    with pytest.raises(RuntimeError, match="raw events"):
        asyncio.run(strategy.on_event(object(), "DU1"))
    with pytest.raises(RuntimeError, match="legacy strategy frames"):
        asyncio.run(strategy.on_observation(object(), "DU1"))


def test_fixed_runtime_rejects_unpinned_or_duplicate_assignments():
    with pytest.raises(ValueError, match="numbered assignments"):
        AssignedStrategyOne([])
    with pytest.raises(ValueError, match="numbered assignments"):
        AssignedStrategyOne([_assignment(revision=2)])
    with pytest.raises(ValueError, match="duplicated"):
        AssignedStrategyOne([_assignment(), _assignment()])
