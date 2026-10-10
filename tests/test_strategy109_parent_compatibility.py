"""Exact protected-parent pins; no execution authority is issued here."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend import backtest_fixed_structural_lot_compatibility_v28 as compatibility

ROOT = Path(__file__).resolve().parents[1]


def parent_pins():
    tree = ast.parse((ROOT / 'src/backend/backtest_fixed_structural_lot_certification_v27.py').read_text())
    return next(ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == 'REVIEWED_SOURCE_AST' for target in node.targets))


def ast_hash(source):
    return sha256(ast.unparse(ast.parse(source)).encode()).hexdigest()


def test_every_protected_parent_leaf_matches_its_immutable_ast():
    pins = parent_pins()
    for relative, expected in pins.items():
        source = (ROOT / relative).read_text(encoding='utf-8')
        restored = compatibility.restore_reviewed_parent_source(source, relative)
        assert ast_hash(restored) == expected, relative
    assert set(compatibility.REVIEWED_EDITS) == {
        'backend/backtest_fixed_lot_initial_recovery_reuse.py',
        'backend/backtest_fixed_structural_lot_configuration.py',
        'trading_runtime/fixed_structural_lot_selected_exit_contract.py',
        'trading_runtime/strategy_registry.py',
        'backend/backtest_fixed_v4_certification.py',
    }
    print('protected108_parent_leaves_verified=' + str(len(pins)))


def test_unknown_delta_cannot_restore_to_a_protected_pin():
    relative = 'src/backend/backtest_fixed_lot_initial_recovery_reuse.py'
    source = (ROOT / relative).read_text(encoding='utf-8')
    changed = source + '\n_unreviewed_delta = True\n'
    restored = compatibility.restore_reviewed_parent_source(changed, relative)
    assert restored == changed
    assert ast_hash(restored) != parent_pins()[relative]


@pytest.mark.parametrize('field', ('REVIEWED_EDITS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST'))
def test_loaded_compatibility_metadata_mutation_is_rejected(monkeypatch, field):
    monkeypatch.setattr(compatibility, field, {} if field == 'REVIEWED_EDITS' else '0' * 64)
    with pytest.raises(ValueError, match='loaded and fresh metadata differs'):
        compatibility.restore_reviewed_parent_source('pass\n', 'src/unrelated.py')
