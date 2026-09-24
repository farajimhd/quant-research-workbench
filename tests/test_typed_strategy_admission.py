from dataclasses import replace

import pytest

from src.trading_runtime.strategy_engine import resolve_long_momentum_parameters
from src.trading_runtime.strategy_registry import (
    installed_strategy_executors, register_strategy_executor, strategy_executor,
    typed_persistence_executor,
)
from src.trading_runtime.typed_strategy_admission import resolve_typed_persistence_parameters


STRATEGY = "long-momentum-campaign"


def test_all_builtin_revisions_have_closed_typed_admission_identity() -> None:
    registrations = installed_strategy_executors()
    assert [(item.strategy_id, item.revision) for item in registrations] == [
        (STRATEGY, revision) for revision in range(26, 48)
    ]
    assert all(typed_persistence_executor(item.strategy_id, item.revision) is item
               for item in registrations)


def test_typed_mode_rejects_unknown_nested_keys_but_legacy_retains_them() -> None:
    extra = {"execution": {"unknown_limit": 1}}
    assert resolve_long_momentum_parameters(extra, revision=47)["execution"]["unknown_limit"] == 1
    with pytest.raises(ValueError, match=r"parameters.execution.unknown_limit"):
        resolve_typed_persistence_parameters(STRATEGY, 47, extra)
    assert resolve_typed_persistence_parameters(
        STRATEGY, 47, {"execution": {"tick_size": 0.01}},
    )["execution"]["tick_size"] == 0.01


def test_typed_mode_rejects_uncataloged_and_replaced_executor() -> None:
    builtin = strategy_executor(STRATEGY, 47)
    assert typed_persistence_executor(STRATEGY, 47) is builtin
    replacement = replace(builtin, implementation="test.UncatalogedExecutor")
    register_strategy_executor(replacement, replace=True)
    try:
        assert strategy_executor(STRATEGY, 47) is replacement
        with pytest.raises(ValueError, match="No typed persistence catalog"):
            resolve_typed_persistence_parameters(STRATEGY, 47, {})
    finally:
        register_strategy_executor(builtin, replace=True)


def test_typed_mode_rejects_lists_without_item_catalog() -> None:
    with pytest.raises(ValueError, match="versioned list catalog"):
        resolve_typed_persistence_parameters(
            STRATEGY, 47, {"exit_routes": [{"route_id": "new-route"}]},
        )
