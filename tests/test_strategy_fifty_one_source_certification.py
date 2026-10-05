"""Every declared execution and cold authority leaf remains immutable."""
import ast
from pathlib import Path

import pytest

from src.backend.backtest_strategy_fifty_one_certification import (
    REQUIRED_SOURCE_FILES, STRATEGY51_SOURCE_AST,
    certify_strategy_fifty_one_source,
)


def test_complete_installed_source_seal():
    assert set(STRATEGY51_SOURCE_AST) == set(REQUIRED_SOURCE_FILES)
    assert len(certify_strategy_fifty_one_source()) == 64


@pytest.mark.parametrize('relative', REQUIRED_SOURCE_FILES)
def test_modified_authority_leaf_rejected(relative, tmp_path):
    root = Path(__file__).parents[1]
    tree = ast.parse((root / relative).read_text(encoding='utf-8'))
    tree.body.append(ast.Assign(targets=[ast.Name(id='unreviewed_authority', ctx=ast.Store())],
                                value=ast.Constant(value=True)))
    mutant = tmp_path / 'mutant.py'
    mutant.write_text(ast.unparse(ast.fix_missing_locations(tree)), encoding='utf-8')
    with pytest.raises(ValueError, match='pinned source changed'):
        certify_strategy_fifty_one_source(source_overrides={relative: mutant})


def test_foreign_override_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='outside reviewed authority'):
        certify_strategy_fifty_one_source(source_overrides={'unapproved.py': tmp_path / 'absent'})
