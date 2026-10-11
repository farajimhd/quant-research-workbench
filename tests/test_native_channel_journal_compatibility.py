"""Exact native delta restoration preserves legacy source authority."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_native_channel_journal_compatibility import (
    PARENT_CLASS, restore_native_channel_journal_class,
)


def source():
    return (Path(__file__).parents[1]/'src/backend/backtest_journal_memory.py').read_text(encoding='utf-8')


def test_restores_entire_reviewed_parent_class_and_accepts_exact_parent():
    restored=restore_native_channel_journal_class(source())
    assert sha256(ast.unparse(restored).encode()).hexdigest()==PARENT_CLASS
    parent=ast.unparse(restored)
    assert ast.unparse(restore_native_channel_journal_class(parent))==parent


@pytest.mark.parametrize('symbol',['append_native_channel_exit','mark_fenced','__init__'])
def test_rejects_changes_to_native_or_inherited_behavior(symbol):
    tree=ast.parse(source())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BacktestMemoryJournal')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==symbol)
    method.body.append(ast.Pass())
    with pytest.raises(ValueError,match='current source differs'):
        restore_native_channel_journal_class(ast.unparse(tree))
