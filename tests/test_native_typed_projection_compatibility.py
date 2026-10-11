"""Exact projection restoration rejects native and inherited source changes."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_native_typed_projection_compatibility import (
    PARENT_MODULE,restore_native_typed_projection_parent,
)


def source():
    return (Path(__file__).parents[1]/'src/backend/backtest_typed_projection.py').read_text(encoding='utf-8')


def test_restores_complete_parent_projection():
    restored=restore_native_typed_projection_parent(source())
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest()==PARENT_MODULE
    assert restore_native_typed_projection_parent(restored)==restored


@pytest.mark.parametrize('mutation',[
    ('native_channel_source is not None','native_channel_source is None'),
    ('base.first_sequence != sequence','base.first_sequence == sequence'),
])
def test_rejects_native_or_inherited_projection_changes(mutation):
    current,replacement=mutation
    text=source()
    assert current in text
    with pytest.raises(ValueError,match='exact reviewed source delta'):
        restore_native_typed_projection_parent(text.replace(current,replacement,1))
