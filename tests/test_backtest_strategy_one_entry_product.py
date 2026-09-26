"""No opaque V7 snapshot is needed to seal Strategy 1 entry decisions."""
from dataclasses import replace

import pytest

from src.backend.backtest_strategy_one_bos import BosSnapshot
from src.backend.backtest_strategy_one_entry_product import (
    content_hash, project_activation, project_candidate,
)
from src.backend.backtest_strategy_one_evidence import StrategyOneEntryEvidence
from src.backend.backtest_strategy_one_market import StrategyOneDecisionCandidate
from src.backend.backtest_strategy_one_preparation import StrategyOneEntryCursor
from src.trading_runtime.strategy_one_activation_state import FrozenActivation
from src.trading_runtime.strategy_one_bos import BosBreak, BosSupport, ConfirmedPivot
from src.trading_runtime.strategy_one_position import (
    ProtectionState, ProtectionTransition,
)


def evidence(*, with_protection=True):
    activation = FrozenActivation("AAA", 30_000, 100_000, .5, ("R1", "R2"))
    candidate = StrategyOneDecisionCandidate(
        {"session_date": "2026-08-18", "ticker": "AAA",
         "boundary_ms": 31_000, "resolution_ms": 100},
        StrategyOneEntryCursor(31_000, "AAA", 5, 30_000,
                               (31_000, 30_000, 30_000, 30_000),
                               30_000, 99_000))
    pivot = ConfirmedPivot("P1", "high", 100_000, 27_000, 28_000)
    bos = BosSnapshot("AAA", 31_000, BosBreak(30_000, pivot, 101_000),
                      (pivot,))
    protection = (ProtectionTransition(
        ProtectionState(31_000, 9.89, 12.0),
        {"source": "completed_30s_bar_low", "boundary_ms": 30_000,
         "low_int": 99_000, "price": 9.89},
        {"price": 12.0, "ordinal": 3,
         "level": {"unified_level_id": "R3"}})
        if with_protection else None)
    return StrategyOneEntryEvidence(candidate, activation, bos,
                                    BosSupport("v7_resistance", "R3", "P1"),
                                    protection)


def test_normalized_entry_evidence_and_activation_children_have_stable_seal():
    value = evidence()
    activation = project_activation(value.activation)
    candidate = project_candidate(value)
    assert activation.row() == {
        "episode_start_ms": 30_000, "price_int": 100_000,
        "average_gap": .5, "resistance_count": 2}
    assert activation.resistance_rows() == (
        {"episode_start_ms": 30_000, "ordinal": 1, "level_id": "R1"},
        {"episode_start_ms": 30_000, "ordinal": 2, "level_id": "R2"})
    assert candidate.row()["target_level_id"] == "R3"
    assert candidate.row()["target_ordinal"] == 3
    seal = content_hash((activation,), (candidate,))
    assert len(seal) == 64
    assert seal == content_hash((activation,), (candidate,))
    assert seal != content_hash((activation,),
                                (replace(candidate, bos_support_level_id="R4"),))


def test_absent_protection_is_explicit_nullable_and_not_a_fabricated_target():
    value = evidence(with_protection=False)
    candidate = project_candidate(value)
    assert candidate.row()["protection_valid"] == 0
    assert candidate.stop_price is None
    assert candidate.target_price is None
    assert candidate.target_level_id == ""
    assert candidate.target_ordinal is None
    assert content_hash((project_activation(value.activation),), (candidate,))


def test_identity_and_completed_source_mismatch_fail_before_publication():
    value = evidence()
    with pytest.raises(ValueError, match="identities differ"):
        project_candidate(replace(value, activation=replace(
            value.activation, boundary_ms=29_000)))
    with pytest.raises(ValueError, match="completed sources"):
        project_candidate(replace(value, protection=replace(
            value.protection, stop_amendment={
                **value.protection.stop_amendment, "low_int": 98_000})))
    activation = project_activation(value.activation)
    candidate = project_candidate(value)
    with pytest.raises(ValueError, match="incomplete or unordered"):
        content_hash((activation,), (replace(candidate, episode_start_ms=29_000),))
