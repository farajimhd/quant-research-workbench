"""Prepared selected-exit source compatibility never grants unreviewed admission."""

import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_fixed_structural_lot_compatibility_v19 import (
    REVIEWED_PARENT_DELTAS,
    restore_reviewed_parent_source,
)
from src.backend.backtest_fixed_structural_lot_certification_v19 import (
    REQUIRED_SOURCE_FILES,
    certify_fixed_structural_lot_source,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative", tuple(REVIEWED_PARENT_DELTAS))
def test_reviewed_shared_source_restores_exact_baseline(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    restored = restore_reviewed_parent_source(source, relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() == (
        REVIEWED_PARENT_DELTAS[relative][1]
    )


@pytest.mark.parametrize("relative", tuple(REVIEWED_PARENT_DELTAS))
def test_unknown_source_mutation_remains_visible(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    altered = source + "\nunknown_selected_exit_edit = True\n"
    assert restore_reviewed_parent_source(altered, relative) == altered


def test_prepared_inventory_cannot_issue_source_authority():
    assert len(REQUIRED_SOURCE_FILES) == len(set(REQUIRED_SOURCE_FILES))
    assert all((ROOT / relative).is_file() for relative in REQUIRED_SOURCE_FILES)
    with pytest.raises(ValueError, match="source seal is unapproved"):
        certify_fixed_structural_lot_source()
