"""Actual complete reviewed source restoration, without execution admission."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend import backtest_fixed_structural_lot_compatibility_v30 as compatibility


@pytest.mark.parametrize('relative', tuple(compatibility.REVIEWED_EDITS))
def test_current_complete_source_restores_exact_retained_parent_ast(relative):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'src' / relative).read_text(encoding='utf-8')
    restored = compatibility.restore_reviewed_parent_source(source, relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() == compatibility.REVIEWED_EDITS[relative]['parent_ast']
    assert source == (root / 'src' / relative).read_text(encoding='utf-8')


@pytest.mark.parametrize('relative', tuple(compatibility.REVIEWED_EDITS))
def test_unknown_source_change_cannot_restore_approved_parent(relative):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'src' / relative).read_text(encoding='utf-8') + '\nunknown_authority_change = True\n'
    restored = compatibility.restore_reviewed_parent_source(source, relative)
    assert restored == source
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() != compatibility.REVIEWED_EDITS[relative]['parent_ast']


def test_loaded_metadata_mutation_rejects(monkeypatch):
    monkeypatch.setattr(compatibility, 'REVIEWED_EDITS', {})
    with pytest.raises(ValueError, match='loaded and fresh metadata differs'):
        compatibility.restore_reviewed_parent_source('pass', 'unrelated.py')
