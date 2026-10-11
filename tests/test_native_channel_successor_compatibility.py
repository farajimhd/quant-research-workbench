import ast
from hashlib import sha256
from pathlib import Path
import pytest
from src.backend.backtest_native_channel_successor_compatibility import (
    RESTORATIONS,restore_native_channel_successor_parent_source,
)


@pytest.mark.parametrize('relative',tuple(RESTORATIONS))
def test_complete_parent_identity_and_unknown_delta_rejection(relative):
    source=(Path(__file__).parents[1]/relative).read_text(encoding='utf-8')
    restored=restore_native_channel_successor_parent_source(source,relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest()==RESTORATIONS[relative]['parent']
    assert restore_native_channel_successor_parent_source(restored,relative)==restored
    with pytest.raises(ValueError,match='exact reviewed source'):
        restore_native_channel_successor_parent_source(source+'\nUNKNOWN_SOURCE_DELTA = True\n',relative)
