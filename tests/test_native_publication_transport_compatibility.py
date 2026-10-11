"""Complete publisher/controller/runtime restoration and mutation rejection."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_native_publisher_compatibility import (
    PARENT_MODULE,restore_native_publisher_parent,
)
from src.backend.backtest_native_execution_transport_compatibility import (
    RESTORATIONS,restore_native_execution_transport_parent,
)


@pytest.mark.parametrize('relative',[
    'backend/backtest_typed_publisher.py','backend/replay_run_service.py','trading_runtime/runtime.py',
    'trading_runtime/arte_journal_commit_v4.py',
    'trading_runtime/arte_journal_writer.py',
    'trading_runtime/numbered_fixed_strategy.py',
])
def test_complete_parent_restoration_and_fail_closed_mutation(relative):
    source=(Path(__file__).parents[1]/'src'/relative).read_text(encoding='utf-8')
    if relative=='backend/backtest_typed_publisher.py':
        restore=restore_native_publisher_parent
        expected=PARENT_MODULE
    else:
        restore=lambda text:restore_native_execution_transport_parent(text,relative)
        expected=RESTORATIONS[relative]['parent']
    restored=restore(source)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest()==expected
    assert restore(restored)==restored
    with pytest.raises(ValueError,match='exact reviewed source delta'):
        restore(source+'\nunknown_transport_behavior = True\n')
