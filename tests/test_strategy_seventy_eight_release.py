"""Registered declaration and exact inherited capabilities; source remains unsealed."""
from copy import deepcopy
from dataclasses import replace
import pytest
from src.trading_runtime.strategy_seventy_eight_release import release_contract, verify_exact_parent
from src.trading_runtime.strategy_seventy_eight_contract import strategy_seventy_eight_contract
from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
from src.trading_runtime.strategy_registry import numbered_strategy_parent
from src.trading_runtime.prior_position_high_reentry import PRIOR_POSITION_HIGH_REENTRY_RULE, PRIOR_POSITION_HIGH_REENTRY_INPUT
from test_strategy_sixty_six_release import source_fixture


def test_registered78_is_exact42_plus_only_causal_reentry():
    parent,child=numbered_fixed_strategy(42),numbered_fixed_strategy(78)
    assert numbered_strategy_parent(78)==42
    release=release_contract()
    from src.trading_runtime.strategy_forty_two_release import release_contract as parent_release
    assert release.rule_set_contracts==parent_release().rule_set_contracts+(PRIOR_POSITION_HIGH_REENTRY_RULE,)
    assert release.rule_set_contracts[-1]==PRIOR_POSITION_HIGH_REENTRY_RULE
    assert release.input_contracts[-1]==PRIOR_POSITION_HIGH_REENTRY_INPUT
    assert child.prior_position_high_reentry_policy is not None
    assert child.all_held_original_risk_policy is None
    assert child.confirmed_original_risk_policy is None
    assert child.premarket_confirmed_original_risk_policy is None
    for name in ('allows_session_exit','allows_adds','allows_completed_30s_trailing','allows_target_escalation',
                 'caps_entry_at_reference_ask','allows_followthrough_failure_exit','early_original_risk_policy',
                 'armed_profit_floor_policy','entry_spread_risk_policy'):
        assert getattr(child,name)==getattr(parent,name)
    for boundary in (0,100,19499900,19500000,19740000,19800000,43200000,43200100,57000000,57300000,57600000):
        for method in ('entry_allowed','acquisition_cutoff','liquidation_due'):
            assert getattr(child,method)(boundary)==getattr(parent,method)(boundary)


def test_prepared_component_parent_cannot_substitute_exact_published42():
    # Existing synthetic fixture is not the pinned current normalized graph.
    with pytest.raises(ValueError): verify_exact_parent(source_fixture())


def test_supplemental_source_has_exact_declared_scope():
    from src.backend.backtest_strategy_seventy_eight_certification import certify_strategy_seventy_eight_source
    assert len(certify_strategy_seventy_eight_source())==64
    # Full composed admission is separately exercised without overrides in the runtime log.
    from src.backend.backtest_strategy_seventy_eight_certification import REQUIRED_SOURCE_FILES
    assert len(REQUIRED_SOURCE_FILES) == 111


@pytest.mark.parametrize("mutation", ["foreign_type", "foreign_policy"])
def test_native_dispatch_rejects_foreign_registered_factory(monkeypatch, mutation):
    from src.backend import backtest_fixed_v4_certification as core
    from src.trading_runtime import strategy_registry as registry
    from src.trading_runtime.numbered_fixed_strategy import NumberedFixedStrategyContract
    real = core.certify_numbered_fixed_v4_projection
    # Only the already independently exercised parent proof is a component seam.
    monkeypatch.setattr(core, "certify_numbered_fixed_v4_projection",
                        lambda number: "a" * 64 if number == 42 else real(number))
    lookup = registry.fixed_strategy_executor
    canonical = strategy_seventy_eight_contract()
    if mutation == "foreign_type":
        foreign = NumberedFixedStrategyContract(78)
    else:
        foreign = deepcopy(canonical)
        object.__setattr__(foreign, "policy_json", canonical.policy_json + " ")
    registration = replace(lookup(canonical.strategy_id, 78), contract_factory=lambda: foreign)
    monkeypatch.setattr(registry, "fixed_strategy_executor",
                        lambda sid, number: registration if number == 78 else lookup(sid, number))
    with pytest.raises((TypeError, ValueError)):
        real(78)
