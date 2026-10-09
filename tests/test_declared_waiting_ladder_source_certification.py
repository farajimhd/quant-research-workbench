"""Source closure proves exact reviewed code, never native publication."""
from pathlib import Path

import pytest

from src.backend import backtest_declared_waiting_ladder_certification as certificate


def test_current_separate_source_review_passes_without_publication():
    from src.trading_runtime.strategy_registry import installed_numbered_fixed_strategy_numbers
    before = installed_numbered_fixed_strategy_numbers()
    first = certificate.certify_declared_waiting_ladder_source()
    assert len(first) == 64
    assert certificate.certify_declared_waiting_ladder_source() == first
    assert installed_numbered_fixed_strategy_numbers() == before
    assert 97 not in before
    assert not certificate.PENDING_SOURCE_REVIEWS
    assert 'src/trading_runtime/strategy_ninety_seven_contract.py' in certificate.REQUIRED_SOURCE_FILES
    assert 'src/trading_runtime/strategy_ninety_seven_release.py' in certificate.REQUIRED_SOURCE_FILES


def test_loaded_inventory_cannot_remove_a_required_source(monkeypatch):
    monkeypatch.setattr(certificate, 'REQUIRED_SOURCE_FILES', certificate.REQUIRED_SOURCE_FILES[:-1])
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        certificate.certify_declared_waiting_ladder_source()


def test_caller_cannot_supply_source_hashes_or_paths():
    with pytest.raises(TypeError):
        certificate.certify_declared_waiting_ladder_source(source_overrides={})


def test_reviewed_contract_drift_rejected(monkeypatch):
    original = Path.read_text

    def read(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.name == 'strategy_ninety_seven_contract.py':
            return source.replace('allows_adds = False', 'allows_adds = True')
        return source

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError, match='reviewed source changed:.*strategy_ninety_seven_contract'):
        certificate.certify_declared_waiting_ladder_source()


def test_unreviewed_certifier_top_level_code_rejected(monkeypatch):
    original = Path.read_text

    def read(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.name == 'backtest_declared_waiting_ladder_certification.py':
            return source + '\nunreviewed_side_effect = True\n'
        return source

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError, match='envelope source differs'):
        certificate.certify_declared_waiting_ladder_source()


def test_source_change_during_review_rejected(monkeypatch):
    original = Path.read_text
    reads = 0

    def read(path, *args, **kwargs):
        nonlocal reads
        source = original(path, *args, **kwargs)
        if path.name == 'strategy_ninety_seven_contract.py':
            reads += 1
            if reads > 1:
                return source + '\n# changed after first read\n'
        return source

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError, match='source changed during certification'):
        certificate.certify_declared_waiting_ladder_source()
