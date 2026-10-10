"""Actual source closure checks, with fail-closed read mutations only."""
from pathlib import Path

import pytest

from src.backend import backtest_fixed_structural_lot_certification_v30 as seal


def test_complete_source_inventory_and_retained_parent_pass():
    from src.backend import backtest_fixed_structural_lot_certification_v29 as parent
    assert set(parent.REQUIRED_SOURCE_FILES) <= set(seal.REQUIRED_SOURCE_FILES)
    assert len(seal.REQUIRED_SOURCE_FILES) == 581
    assert len(seal.certify_fixed_structural_lot_source()) == 64


@pytest.mark.parametrize('relative', [
    'src/backend/backtest_fixed_lot_publication_reuse.py',
    'src/trading_runtime/single_publication_verification.py',
    'src/trading_runtime/fixed_structural_lot_entry_v4.py',
])
def test_changed_current_source_cannot_issue_seal(monkeypatch, relative):
    original = Path.read_text
    root = Path(seal.__file__).resolve().parents[2]
    target = root / relative
    def changed(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        return source + '\nunreviewed_runtime_change = True\n' if path == target else source
    monkeypatch.setattr(Path, 'read_text', changed)
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        seal.certify_fixed_structural_lot_source()


def test_changed_loaded_metadata_cannot_issue_seal(monkeypatch):
    monkeypatch.setattr(seal, 'REVIEWED_SOURCE_AST', {})
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        seal.certify_fixed_structural_lot_source()
