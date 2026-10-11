"""Exact whole-manager restoration without changing inherited authority."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_native_manager_compatibility import PARENT_MODULE,restore_native_manager_parent


def source():
    return (Path(__file__).parents[1]/'src/backend/backtest_strategy_one_management.py').read_text(encoding='utf-8')


def test_restores_complete_manager_parent():
    restored=restore_native_manager_parent(source())
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest()==PARENT_MODULE
    assert restore_native_manager_parent(restored)==restored


@pytest.mark.parametrize('method',['__init__','on_management','stage_current_native_channel_management'])
def test_rejects_changes_to_inherited_or_declared_management(method):
    tree=ast.parse(source())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='StrategyOneManagementRunner')
    node=next(n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==method)
    node.body.append(ast.Pass())
    with pytest.raises(ValueError,match='exact reviewed source delta'):
        restore_native_manager_parent(ast.unparse(tree))
