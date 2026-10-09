import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.backtest_fixed_structural_lot_certification_v16 import (
    certify_fixed_structural_lot_source, REQUIRED_SOURCE_FILES,
)
from src.backend.backtest_fixed_structural_lot_compatibility_v16 import (
    restore_reviewed_parent_source, REVIEWED_PARENT_DELTAS,
)


def test_new_source_certificate_is_explicitly_unapproved():
    with pytest.raises(ValueError, match='unapproved; admission remains closed'):
        certify_fixed_structural_lot_source()
    assert len(set(REQUIRED_SOURCE_FILES)) == len(REQUIRED_SOURCE_FILES)
    assert 'src/trading_runtime/owned_scalar_row_snapshots.py' in REQUIRED_SOURCE_FILES
    assert 'src/trading_runtime/declared_owned_scalar_snapshot_reuse.py' in REQUIRED_SOURCE_FILES
    assert 'src/backend/backtest_fixed_structural_lot_native_v16.py' in REQUIRED_SOURCE_FILES


@pytest.mark.parametrize('relative', tuple(REVIEWED_PARENT_DELTAS))
def test_exact_shared_module_restoration_and_foreign_source_rejection(relative):
    root = Path(__file__).resolve().parents[1]
    current = (root / relative).read_text(encoding='utf-8')
    restored = restore_reviewed_parent_source(current, relative)
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() == REVIEWED_PARENT_DELTAS[relative][1]
    changed = current + '\nforeign_source_change = True\n'
    assert restore_reviewed_parent_source(changed, relative) == changed
