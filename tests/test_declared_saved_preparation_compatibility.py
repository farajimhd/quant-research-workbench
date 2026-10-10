"""Complete parent source retention; no installed execution authority."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend import backtest_fixed_structural_lot_compatibility_v29 as compatibility

ROOT = Path(__file__).resolve().parents[1]


def ast_hash(source):
    return sha256(ast.unparse(ast.parse(source)).encode()).hexdigest()


def parent_pins():
    tree = ast.parse((ROOT / 'src/backend/backtest_fixed_structural_lot_certification_v28.py').read_text())
    return next(ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == 'REVIEWED_SOURCE_AST' for target in node.targets))


def test_all_protected_parent_files_match_exact_immutable_ast():
    pins = parent_pins()
    for relative, expected in pins.items():
        source = (ROOT / relative).read_text(encoding='utf-8')
        restored = compatibility.restore_reviewed_parent_source(source, relative)
        assert ast_hash(restored) == expected, relative
    assert set(compatibility.REVIEWED_EDITS) == {
        'backend/backtest_fixed_v4_certification.py',
        'backend/backtest_v4_saved_review.py',
        'backend/historical_runtime_versions.py',
        'backend/backtest_fixed_structural_lot_native_v20.py',
        'trading_runtime/strategy_registry.py',
    }
    print('protected_parent_files_verified=' + str(len(pins)))


def test_unreviewed_guard_change_is_not_erased():
    relative = 'src/backend/backtest_v4_saved_review.py'
    source = (ROOT / relative).read_text(encoding='utf-8')
    guard = "declared_fixed_rule(release.strategy_number, 'strategy-thirty-six-completed-entry-activity-fade-v1')"
    assert source.count(guard) == 1
    changed = source.replace(guard, 'False', 1)
    restored = compatibility.restore_reviewed_parent_source(changed, relative)
    assert restored == changed
    assert ast_hash(restored) != parent_pins()[relative]


def test_unreviewed_callback_code_guard_change_is_not_erased():
    relative = 'src/backend/historical_runtime_versions.py'
    source = (ROOT / relative).read_text(encoding='utf-8')
    guard = "getattr(certificate_fn, '__code__', None) is not implementation_code"
    assert source.count(guard) == 3
    changed = source.replace(guard, 'False', 1)
    assert compatibility.restore_reviewed_parent_source(changed, relative) == changed
    assert ast_hash(changed) != parent_pins()[relative]


@pytest.mark.parametrize('field', ['REVIEWED_EDITS', 'APPROVED_METADATA_ANCHOR', 'APPROVED_SELF_AST'])
def test_mutated_loaded_compatibility_metadata_is_rejected(monkeypatch, field):
    monkeypatch.setattr(compatibility, field, {} if field == 'REVIEWED_EDITS' else '0' * 64)
    with pytest.raises(ValueError, match='loaded and fresh metadata differs'):
        compatibility.restore_reviewed_parent_source('pass\n', 'src/unrelated.py')
