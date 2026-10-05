"""Fail closed before review; every declared authority leaf rejects mutation."""
import ast
from hashlib import sha256
from pathlib import Path
import pytest
from src.backend import backtest_strategy_fifty_two_certification as cert


def test_unreviewed_source_is_not_admitted(monkeypatch):
    assert set(cert.STRATEGY52_SOURCE_AST) == set(cert.REQUIRED_SOURCE_FILES)
    monkeypatch.setattr(cert, 'STRATEGY52_SOURCE_AST', {})
    with pytest.raises(ValueError, match='review is not sealed'):
        cert.certify_strategy_fifty_two_source()


@pytest.mark.parametrize('relative', cert.REQUIRED_SOURCE_FILES)
def test_mutated_declared_authority_leaf_rejected_by_exact_ast(monkeypatch, relative, tmp_path):
    root = Path(__file__).parents[1]
    # Exercise the reviewed approval map, rather than certifying current files in the test.
    assert len(cert.certify_strategy_fifty_two_source()) == 64
    tree = ast.parse((root / relative).read_text(encoding='utf-8'))
    tree.body.append(ast.Assign(targets=[ast.Name(id='unreviewed_authority', ctx=ast.Store())], value=ast.Constant(True)))
    mutant = tmp_path / 'mutant.py'
    mutant.write_text(ast.unparse(ast.fix_missing_locations(tree)), encoding='utf-8')
    with pytest.raises(ValueError, match='pinned source changed'):
        cert.certify_strategy_fifty_two_source(source_overrides={relative: mutant})
