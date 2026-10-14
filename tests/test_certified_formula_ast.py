"""Cross-interpreter formula seals retain their reviewed arithmetic authority."""
import ast

import pytest

from pipelines.certified_research import fundamentals


def test_current_formula_arithmetic_keeps_existing_reviewed_seal():
    result = fundamentals.app_contract()
    assert result['source_arithmetic_hash'] == fundamentals.APP_ARITHMETIC_BASELINE
    assert len(result['app']) == 19
    # Empty fields must remain explicit even on Python 3.13 and newer.
    node = ast.parse('def example():\n    pass\n').body[0]
    assert 'decorator_list=[]' in fundamentals._formula_ast(node)
    assert 'kw_defaults=[]' in fundamentals._formula_ast(node)


def test_actual_arithmetic_change_still_fails_closed(monkeypatch):
    original = ast.parse

    def changed_source(*args, **kwargs):
        tree = original(*args, **kwargs)
        target = next(node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'safe_ratio')
        target.body.insert(0, ast.Return(value=ast.Constant(value=123)))
        return tree

    monkeypatch.setattr(fundamentals.ast, 'parse', changed_source)
    with pytest.raises(ValueError, match='formula arithmetic changed'):
        fundamentals.app_contract()
