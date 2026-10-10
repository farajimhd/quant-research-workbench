"""Actual local source closure, without market or financial qualification."""
import pytest
from pathlib import Path

from src.backend import backtest_fixed_structural_lot_certification_v31 as seal


def test_actual_complete_source_and_retained_parent_certify():
    assert len(seal.REQUIRED_SOURCE_FILES) == 586
    assert 'src/trading_runtime/strategy_one_hundred_twelve_contract.py' in seal.REQUIRED_SOURCE_FILES
    assert 'src/trading_runtime/fixed_structural_lot_release_v31.py' in seal.REQUIRED_SOURCE_FILES
    assert 'src/backend/backtest_fixed_structural_lot_certification_v30.py' in seal.REQUIRED_SOURCE_FILES
    digest = seal.certify_fixed_structural_lot_source()
    assert len(digest) == 64
    assert all(char in '0123456789abcdef' for char in digest)


def test_loaded_metadata_mutation_cannot_issue_authority(monkeypatch):
    monkeypatch.setattr(seal, 'APPROVED_METADATA_ANCHOR', '0'*64)
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        seal.certify_fixed_structural_lot_source()


def test_changed_capacity_source_cannot_issue_authority(monkeypatch):
    original = Path.read_text
    def changed(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path.name == 'strategy_one_hundred_twelve_release.py':
            assert 'max_contexts=128' in text
            return text.replace('max_contexts=128', 'max_contexts=129')
        return text
    monkeypatch.setattr(Path, 'read_text', changed)
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        seal.certify_fixed_structural_lot_source()
