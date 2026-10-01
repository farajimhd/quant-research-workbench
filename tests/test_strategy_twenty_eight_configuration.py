"""The operational repair preserves every inherited trading policy."""
from copy import deepcopy
from dataclasses import replace

import pytest

from pipelines.strategy_one.strategy_twenty_seven_configuration import compile_strategy_twenty_seven_configuration
from pipelines.strategy_one.strategy_twenty_eight_configuration import compile_strategy_twenty_eight_configuration
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.strategy_twenty_eight_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, RULES,
    verify_strategy_twenty_eight_manifest,
)
from test_strategy_twenty_seven_configuration import parent as twenty_seventh_parent


def parent():
    value = compile_strategy_twenty_seven_configuration(twenty_seventh_parent(),
        approved_code_commit="d" * 40, approved_code_fingerprint="e" * 64,
        approval_reference="test-only-parent")
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(":")[1],
        PARENT_PAYLOAD_HASH, value["node_hash"], value["source_candidate_id"],
        value["source_candidate_hash"], "test-only", value["payload"])


def test_exact_parent_and_all_trading_policies_are_preserved():
    source = parent()
    before = deepcopy(source.payload)
    result = compile_strategy_twenty_eight_configuration(source,
        approved_code_commit="d" * 40, approved_code_fingerprint="e" * 64,
        approval_reference="test-only-selected-v7-inventory")
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    _verified_numbered_envelope(result)
    manifest = verify_strategy_twenty_eight_manifest(result["payload"]["strategy"])
    assert source.payload == before
    assert result["payload"]["strategy"]["parameters"] == before["strategy"]["parameters"]
    for key in before.keys() - {"strategy", "strategy_profile", "run_plan"}:
        assert result["payload"][key] == before[key]
    previous = before["strategy"]["numbered_release"]
    for key in previous:
        if key.endswith("_policy"):
            assert manifest[key] == previous[key]
    from src.trading_runtime.strategy_twenty_seven_release import RULES as parent_rules
    from src.trading_runtime.strategy_registry import numbered_strategy_parent, numbered_strategy
    assert RULES == parent_rules
    assert numbered_strategy_parent(28) == 27
    assert numbered_strategy(28).canonical_payload() == manifest["contract"]


def test_foreign_parent_is_rejected():
    source = replace(parent(), payload_hash="f" * 64)
    with pytest.raises(ValueError, match="exact pinned certified Strategy 27"):
        compile_strategy_twenty_eight_configuration(source,
            approved_code_commit="d" * 40, approved_code_fingerprint="e" * 64,
            approval_reference="test-only")
