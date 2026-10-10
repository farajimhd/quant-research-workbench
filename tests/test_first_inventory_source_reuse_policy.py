from types import SimpleNamespace

import pytest

from src.trading_runtime import first_inventory_source_reuse_policy as policy
from src.trading_runtime import initial_held_recovery_reuse_policy as initial
from src.trading_runtime import proposal_decision_inventory_reuse_policy as proposal


def declaration(*, initial_scope=True, proposal_scope=True):
    dependencies = ((initial_scope, initial), (proposal_scope, proposal))
    return SimpleNamespace(
        input_contracts=(policy.INPUT,) + tuple(module.INPUT for selected, module in dependencies if selected),
        rule_set_contracts=(policy.RULE,) + tuple(module.RULE for selected, module in dependencies if selected))


@pytest.mark.parametrize('initial_scope,proposal_scope', ((True, False), (False, True), (True, True)))
def test_explicit_operations_roundtrip(initial_scope, proposal_scope):
    value = dict(max_contexts=8, initial_held=initial_scope, proposal=proposal_scope)
    result = policy.parse_declared_first_inventory_source_reuse(
        declaration(initial_scope=initial_scope, proposal_scope=proposal_scope), value)
    assert result.payload() == value


@pytest.mark.parametrize('field,value', (
    ('max_contexts', True), ('max_contexts', 0), ('max_contexts', 100001),
    ('initial_held', 1), ('proposal', 'true')))
def test_invalid_scope_is_rejected(field, value):
    values = dict(max_contexts=8, initial_held=True, proposal=True)
    values[field] = value
    with pytest.raises(ValueError):
        policy.parse_declared_first_inventory_source_reuse(declaration(), values)


def test_unselected_and_undeclared_policy():
    release = SimpleNamespace(input_contracts=(), rule_set_contracts=())
    assert policy.parse_declared_first_inventory_source_reuse(release, None) is None
    with pytest.raises(ValueError):
        policy.require_declared_first_inventory_source_reuse(
            release, policy.FirstInventorySourceReusePolicy(8, True, False))
    with pytest.raises(ValueError):
        policy.FirstInventorySourceReusePolicy(8, False, False)


@pytest.mark.parametrize('family', ('input_contracts', 'rule_set_contracts'))
@pytest.mark.parametrize('mutation', ('missing', 'duplicate'))
def test_own_declaration_requires_exactly_one_marker(family, mutation):
    release = declaration()
    marker = policy.INPUT if family == 'input_contracts' else policy.RULE
    original = getattr(release, family)
    setattr(release, family, tuple(item for item in original if item != marker)
            if mutation == 'missing' else original + (marker,))
    with pytest.raises(ValueError):
        policy.require_declared_first_inventory_source_reuse(
            release, policy.FirstInventorySourceReusePolicy(8, True, True))


@pytest.mark.parametrize('initial_scope,proposal_scope', ((True, False), (False, True)))
def test_selected_operation_cannot_lack_issued_scope(initial_scope, proposal_scope):
    with pytest.raises(ValueError):
        policy.require_declared_first_inventory_source_reuse(
            declaration(initial_scope=False, proposal_scope=False),
            policy.FirstInventorySourceReusePolicy(8, initial_scope, proposal_scope))


@pytest.mark.parametrize('value', (None, {}, {'max_contexts': 8},
    {'max_contexts': 8, 'initial_held': True, 'proposal': False, 'extra': 1}))
def test_declared_payload_cannot_be_missing_or_noncanonical(value):
    with pytest.raises(ValueError):
        policy.parse_declared_first_inventory_source_reuse(declaration(), value)
