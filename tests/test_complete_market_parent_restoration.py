"""Exact whole-module restoration; arbitrary additions cannot be masked."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_fixed_structural_lot_compatibility_v18 import (
    REVIEWED_PARENT_DELTAS, restore_reviewed_parent_source,
)


@pytest.mark.parametrize('relative', tuple(REVIEWED_PARENT_DELTAS))
def test_whole_parent_module_restoration_and_foreign_change_rejection(relative):
    root = Path(__file__).resolve().parents[1]
    source = (root / relative).read_text(encoding='utf-8')
    restored = restore_reviewed_parent_source(source, relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode('utf-8')).hexdigest() == REVIEWED_PARENT_DELTAS[relative][1]
    altered = source + '\nforeign_source_change = True\n'
    assert restore_reviewed_parent_source(altered, relative) == altered
