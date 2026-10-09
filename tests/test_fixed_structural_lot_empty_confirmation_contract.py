"""Prepared exact factory and source-gate isolation; no installed approval."""
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from src.trading_runtime.strategy_ninety_five_contract import strategy_ninety_five_contract
from src.trading_runtime.fixed_structural_lot_empty_confirmation_contract import FixedStructuralLotEmptyConfirmationStrategyContract
from src.trading_runtime.fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
from src.trading_runtime.empty_protection_confirmation_policy import (
    INPUT, RULE, EmptyProtectionConfirmationPolicy, installed_empty_protection_confirmation_policy,
)


def prepared():
    base = strategy_ninety_five_contract()
    draft = replace(base.release, number=900001, executor_revision=900001,
        input_contracts=(*base.release.input_contracts, INPUT),
        rule_set_contracts=(*base.release.rule_set_contracts, RULE), approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    values = {f.name: getattr(base, f.name) for f in fields(base)}
    values.update(strategy_number=release.number, release=release,
                  empty_confirmation_policy=EmptyProtectionConfirmationPolicy(1))
    return FixedStructuralLotEmptyConfirmationStrategyContract(**values)


def test_prepared_factory_retains_full_owned_and_economic_contract():
    base = strategy_ninety_five_contract()
    selected = prepared()
    assert require_declared_fixed_structural_lot_contract(selected, selected.release) is selected
    for field in fields(base):
        if field.name not in ('strategy_number', 'release'):
            assert getattr(selected, field.name) == getattr(base, field.name)
    assert require_declared_fixed_structural_lot_contract(base, base.release) is base


def test_old_factory_cannot_satisfy_new_selected_release_or_missing_policy():
    selected = prepared()
    with pytest.raises(ValueError):
        require_declared_fixed_structural_lot_contract(strategy_ninety_five_contract(), selected.release)
    with pytest.raises(ValueError):
        replace(selected, empty_confirmation_policy=None)


def test_unselected_metadata_cannot_enable_skipping_and_forged_claim_rejected():
    source = SimpleNamespace(installed_json='{}', installed_payload={'strategy': {
        'parameters': {}, 'numbered_release': {'contract': {'input_contracts': [], 'rule_set_contracts': []}}}})
    assert installed_empty_protection_confirmation_policy(source) is None
    source.installed_payload['strategy']['parameters']['empty_protection_confirmation_policy'] = EmptyProtectionConfirmationPolicy(1).payload()
    with pytest.raises(ValueError):
        installed_empty_protection_confirmation_policy(source)
