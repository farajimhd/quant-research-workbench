"""Whole-module restoration and fail-closed mutation checks."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_native_session_execution_compatibility import (
    PARENT_MODULE, restore_native_session_execution_parent,
)


def source():
    return (Path(__file__).parents[1]/'src/backend/backtest_strategy_one_execution.py').read_text(encoding='utf-8')


def test_restores_complete_parent_and_preserves_idempotence():
    restored=restore_native_session_execution_parent(source())
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest()==PARENT_MODULE
    assert restore_native_session_execution_parent(restored)==restored


@pytest.mark.parametrize('mutation',[
    ('installed_session=selected_session','installed_session=None'),
    ('policy=manager.contract.confirmed_original_risk_policy','policy=None'),
    ('max_workers=max_workers','max_workers=1'),
])
def test_rejects_changed_binding_or_inherited_execution(mutation):
    current,replacement=mutation
    text=source()
    assert current in text
    with pytest.raises(ValueError,match='exact reviewed delta'):
        restore_native_session_execution_parent(text.replace(current,replacement,1))
