"""Draft declaration checks; these grant no installed or native admission."""
from dataclasses import fields

import pytest

from src.trading_runtime.strategy_one_hundred_eight_contract import strategy_one_hundred_eight_contract
from src.trading_runtime.strategy_one_hundred_nine_contract import strategy_one_hundred_nine_contract
from src.trading_runtime import first_inventory_source_reuse_policy as policy


def test_draft_preserves_every_inherited_economic_and_execution_field():
    prior = strategy_one_hundred_eight_contract()
    candidate = strategy_one_hundred_nine_contract()
    for field in fields(prior):
        if field.name not in {'strategy_number', 'release', policy.PARAMETER}:
            assert getattr(candidate, field.name) == getattr(prior, field.name), field.name
    assert getattr(prior, policy.PARAMETER) is None
    assert getattr(candidate, policy.PARAMETER).payload() == dict(
        max_contexts=32, initial_held=True, proposal=True)


def test_draft_appends_only_its_paired_declared_capability():
    prior = strategy_one_hundred_eight_contract().release
    candidate = strategy_one_hundred_nine_contract().release
    assert candidate.input_contracts == (*prior.input_contracts, policy.INPUT)
    assert candidate.rule_set_contracts == (*prior.rule_set_contracts, policy.RULE)
    assert candidate.evaluation_interval == prior.evaluation_interval
    assert candidate.executor_strategy_id == prior.executor_strategy_id
    assert candidate.number != prior.number


@pytest.mark.parametrize('field', ('REVIEWED_SOURCE_AST', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST'))
def test_source_closure_rejects_loaded_metadata_mutation(monkeypatch, field):
    from src.backend import backtest_fixed_structural_lot_certification_v28 as source
    monkeypatch.setattr(source, field, {} if field == 'REVIEWED_SOURCE_AST' else '0' * 64)
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        source.certify_fixed_structural_lot_source()


def test_registered_successor_uses_exact_manifest_authority():
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.trading_runtime.strategy_one_hundred_nine_release import (
        derive_strategy_one_hundred_nine_configuration,
        verify_prepared_strategy_one_hundred_nine_configuration,
    )
    from src.backend.backtest_fixed_structural_lot_certification_v28 import certify_fixed_structural_lot_source

    release = numbered_strategy(109)
    registration = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    assert release == strategy_one_hundred_nine_contract().release
    assert registration.contract_factory is strategy_one_hundred_nine_contract
    authority = registration.manifest_authority
    assert authority.parent_number == 42
    assert authority.derive is derive_strategy_one_hundred_nine_configuration
    assert authority.verify_manifest is verify_prepared_strategy_one_hundred_nine_configuration
    assert authority.certify_source is certify_fixed_structural_lot_source
