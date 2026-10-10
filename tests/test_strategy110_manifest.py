"""Unpublished whole-tree comparison with controlled certified-parent transport."""
from copy import deepcopy
from dataclasses import fields

import pytest

from tests.test_strategy108_full_manifest import manifest
from src.trading_runtime.strategy_one_hundred_nine_contract import strategy_one_hundred_nine_contract
from src.trading_runtime.strategy_one_hundred_ten_contract import strategy_one_hundred_ten_contract
from src.trading_runtime.strategy_one_hundred_nine_release import derive_strategy_one_hundred_nine_configuration
from src.trading_runtime.strategy_one_hundred_ten_release import (
    derive_strategy_one_hundred_ten_configuration,
    verify_prepared_strategy_one_hundred_ten_configuration,
)


def test_every_typed_economic_and_execution_field_is_inherited():
    prior = strategy_one_hundred_nine_contract()
    candidate = strategy_one_hundred_ten_contract()
    for field in fields(prior):
        if field.name not in {'strategy_number', 'release'}:
            assert getattr(candidate, field.name) == getattr(prior, field.name), field.name
    assert candidate.release.input_contracts == prior.release.input_contracts
    assert candidate.release.rule_set_contracts == prior.release.rule_set_contracts
    assert candidate.release.approved_digest != prior.release.approved_digest


def test_unapproved_complete_source_seal_remains_closed(tmp_path):
    import ast
    import importlib.util
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_certification_v29 as sealed
    assert 'src/trading_runtime/numbered_session_exit.py' in sealed.REQUIRED_SOURCE_FILES
    assert 'src/backend/backtest_native_complete_projection_reuse.py' in sealed.REQUIRED_SOURCE_FILES
    tree = ast.parse(Path(sealed.__file__).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == 'REVIEWED_SOURCE_AST':
                node.value = ast.Dict(keys=[], values=[])
            elif node.targets[0].id in {'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST'}:
                node.value = ast.Constant('')
    unapproved = tmp_path / 'src/backend/backtest_fixed_structural_lot_certification_v29.py'
    unapproved.parent.mkdir(parents=True)
    unapproved.write_text(ast.unparse(tree) + '\n', encoding='utf-8')
    spec = importlib.util.spec_from_file_location('unapproved_source_seal_test', unapproved)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match='unapproved; admission remains closed'):
        module.certify_fixed_structural_lot_source()


def test_catalog_installs_exact_factory_and_source_owner_without_publication():
    from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
    from src.trading_runtime.declared_native_manifest import registered_manifest_authority
    release = strategy_one_hundred_ten_contract().release
    assert numbered_strategy(release.number) == release
    registration = fixed_strategy_executor(release.executor_strategy_id, release.executor_revision)
    assert registration.contract_factory() == strategy_one_hundred_ten_contract()
    authority = registered_manifest_authority(release.number)
    assert authority.parent_number == 42
    assert authority.derive is derive_strategy_one_hundred_ten_configuration
    assert authority.verify_manifest is verify_prepared_strategy_one_hundred_ten_configuration
    from src.backend import backtest_fixed_structural_lot_certification_v29 as sealed
    assert authority.certify_source is sealed.certify_fixed_structural_lot_source
    assert tuple(sealed.REVIEWED_SOURCE_AST) == sealed.REQUIRED_SOURCE_FILES
    assert len(sealed.REQUIRED_SOURCE_FILES) == 573
    assert len(sealed.APPROVED_METADATA_ANCHOR) == len(sealed.APPROVED_SELF_AST) == 64


def test_complete_inherited_tree_differs_only_in_declared_identity(manifest):
    parent, approval, _ = manifest
    prior = derive_strategy_one_hundred_nine_configuration(parent, **approval)
    candidate = derive_strategy_one_hundred_ten_configuration(parent, **approval)
    assert verify_prepared_strategy_one_hundred_ten_configuration(parent, candidate['payload']) == candidate
    normalized = deepcopy(candidate['payload'])
    previous = prior['payload']
    identity = {
        'strategy': ('strategy_number', 'revision', 'profile_id', 'profile_revision', 'name'),
        'strategy_profile': ('profile_id', 'revision', 'definition_revision', 'name', 'description'),
        'run_plan': ('profile_id', 'name', 'description'),
    }
    for section, names in identity.items():
        for name in names:
            normalized[section][name] = deepcopy(previous[section][name])
    for name in ('contract', 'approved_digest', 'manifest_hash'):
        normalized['strategy']['numbered_release'][name] = deepcopy(previous['strategy']['numbered_release'][name])
    assert normalized == previous
    assert candidate['source_candidate_id'] == prior['source_candidate_id']
    assert candidate['source_candidate_hash'] == prior['source_candidate_hash']


@pytest.mark.parametrize('change', ['currency', 'lot_count', 'source_binding', 'unknown_parameter'])
def test_changed_economics_or_source_binding_are_rejected(manifest, change):
    parent, approval, _ = manifest
    candidate = derive_strategy_one_hundred_ten_configuration(parent, **approval)
    payload = deepcopy(candidate['payload'])
    if change == 'currency':
        payload['accounts']['currency'] = 'CAD'
    elif change == 'lot_count':
        payload['strategy']['parameters']['fixed_structural_lot_policy']['count'] += 1
    elif change == 'source_binding':
        payload['strategy']['numbered_release']['source_payload_hash'] = '0' * 64
    else:
        payload['strategy']['parameters']['unreviewed_parameter'] = True
    with pytest.raises(ValueError, match='complete inherited tree'):
        verify_prepared_strategy_one_hundred_ten_configuration(parent, payload)
