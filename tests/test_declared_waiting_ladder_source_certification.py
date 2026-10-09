"""Current catalog97 composes new review with unchanged frozen97 approval.

The original certifier remains exact to a2d3 and rejects the changed tree.
The separately reviewed composition proves actual current modules and exact
retained pins. Neither route grants native publication or financial approval.
"""
from pathlib import Path
from hashlib import sha256
import ast

import pytest

from src.backend import backtest_declared_waiting_ladder_certification as certificate
from src.trading_runtime.declared_native_manifest import registered_manifest_authority


def composed_source():
    return registered_manifest_authority(97).certify_source()


def test_original_frozen97_certifier_and_all_literal_pins_remain_unchanged():
    # Exact whole-module AST from approved a2d3; includes all 94 leaf pins,
    # review origins, metadata anchor, function and self-envelope approval.
    source = Path(certificate.__file__).read_text(encoding='utf-8')
    assert sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() == (
        '755fe3b4f6c99b8b89120b306c913432bda2ed45794672d63f5680dd0ac53476')


def test_current_separate_source_review_passes_without_publication():
    from src.trading_runtime.strategy_registry import installed_numbered_fixed_strategy_numbers
    before = installed_numbered_fixed_strategy_numbers()
    first = composed_source()
    assert len(first) == 64
    assert composed_source() == first
    assert installed_numbered_fixed_strategy_numbers() == before
    assert 97 in before  # Catalog installation does not publish normalized configuration.
    assert not certificate.PENDING_SOURCE_REVIEWS
    assert 'src/trading_runtime/strategy_ninety_seven_contract.py' in certificate.REQUIRED_SOURCE_FILES
    assert 'src/trading_runtime/strategy_ninety_seven_release.py' in certificate.REQUIRED_SOURCE_FILES
    with pytest.raises(ValueError, match='reviewed source changed'):
        certificate.certify_declared_waiting_ladder_source()


def test_loaded_inventory_cannot_remove_a_required_source(monkeypatch):
    monkeypatch.setattr(certificate, 'REQUIRED_SOURCE_FILES', certificate.REQUIRED_SOURCE_FILES[:-1])
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        certificate.certify_declared_waiting_ladder_source()


def test_caller_cannot_supply_source_hashes_or_paths():
    with pytest.raises(TypeError):
        certificate.certify_declared_waiting_ladder_source(source_overrides={})
    with pytest.raises(TypeError):
        registered_manifest_authority(97).certify_source(source_overrides={})


def test_missing_current_required_source_rejected(monkeypatch):
    original = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda path:
        False if path.name == 'strategy_ninety_seven_release.py' else original(path))
    with pytest.raises(ValueError, match='required source path is missing or foreign'):
        composed_source()


def test_reviewed_contract_drift_rejected(monkeypatch):
    original = Path.read_text

    def read(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.name == 'strategy_ninety_seven_contract.py':
            return source.replace('allows_adds = False', 'allows_adds = True')
        return source

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError, match='reviewed source.*strategy_ninety_seven_contract'):
        composed_source()


def test_whole_dispatcher_module_rejects_compatibility_helper_drift(monkeypatch):
    relative = 'src/backend/backtest_fixed_v4_certification.py'
    assert type(certificate.REVIEWED_SOURCE_AST[relative]) is str
    original = Path.read_text

    def read(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.name == 'backtest_fixed_v4_certification.py':
            before = "source = restore_waiting(source, 'backend/backtest_strategy_one_configuration.py')"
            assert source.count(before) == 1
            return source.replace(before, 'source = source')
        return source

    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(ValueError, match='reviewed source.*backtest_fixed_v4_certification'):
        composed_source()


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
    with pytest.raises(ValueError, match='source changed during (certification|composition)'):
        composed_source()
