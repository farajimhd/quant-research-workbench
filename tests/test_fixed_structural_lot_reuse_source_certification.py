"""Exact successor source coverage and retained parent checks."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend import backtest_fixed_structural_lot_certification_v14 as certificate
from src.backend import backtest_fixed_structural_lot_compatibility_v14 as compatibility


ROOT = Path(certificate.__file__).resolve().parents[2]


@pytest.mark.parametrize('relative', tuple(compatibility.REVIEWED_PARENT_DELTAS))
def test_reviewed_delta_restores_complete_parent_ast(relative):
    source = (ROOT / relative).read_text(encoding='utf-8')
    current, baseline, _ = compatibility.REVIEWED_PARENT_DELTAS[relative]
    assert sha256(ast.unparse(ast.parse(source)).encode()).hexdigest() == current
    restored = compatibility.restore_reviewed_parent_source(source, relative)
    assert restored != source
    assert sha256(ast.unparse(ast.parse(restored)).encode()).hexdigest() == baseline


@pytest.mark.parametrize('relative', tuple(compatibility.REVIEWED_PARENT_DELTAS))
def test_unreviewed_edit_cannot_receive_parent_restoration(relative):
    changed = (ROOT / relative).read_text(encoding='utf-8') + '\npass\n'
    assert compatibility.restore_reviewed_parent_source(changed, relative) == changed


def test_source_certificate_inventory_contains_selected_new_authorities():
    required = certificate.REQUIRED_SOURCE_FILES
    assert len(required) == len(set(required))
    for relative in (
        'src/backend/backtest_fixed_structural_lot_native_v14.py',
        'src/backend/backtest_fixed_structural_lot_source_v14.py',
        'src/backend/backtest_fixed_structural_lot_empty_v14.py',
        'src/backend/backtest_fixed_structural_lot_execution_v14.py',
        'src/backend/backtest_fixed_structural_lot_compatibility_v14.py',
        'src/trading_runtime/fixed_structural_lot_release_v14.py',
        'src/trading_runtime/strategy_ninety_three_contract.py',
        'src/trading_runtime/strategy_ninety_three_release.py',
        'src/trading_runtime/declared_packet_validation_reuse.py',
        'src/trading_runtime/exact_scalar_packet_validation_cache.py',
        'src/trading_runtime/packet_validation_reuse_policy.py',
    ):
        assert relative in required
    assert all((ROOT / relative).is_file() for relative in required)


def test_loaded_source_map_cannot_override_fresh_certificate(monkeypatch):
    monkeypatch.setattr(certificate, 'REVIEWED_SOURCE_AST',
                        dict.fromkeys(certificate.REQUIRED_SOURCE_FILES, '0' * 64))
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        certificate.certify_fixed_structural_lot_source()


@pytest.mark.parametrize('extra', ['\npass\n', '\nimport os\n'])
def test_added_certifier_code_rejects_before_source_approval(monkeypatch, extra):
    original = Path.read_text
    own = Path(certificate.__file__).resolve()

    def altered(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        return source + extra if path.resolve() == own else source

    monkeypatch.setattr(Path, 'read_text', altered)
    with pytest.raises(ValueError, match='module envelope differs'):
        certificate.certify_fixed_structural_lot_source()


def test_real_complete_source_certificate_passes_without_overrides():
    assert tuple(certificate.REVIEWED_SOURCE_AST) == certificate.REQUIRED_SOURCE_FILES
    assert len(certificate.certify_fixed_structural_lot_source()) == 64


@pytest.mark.parametrize('relative', certificate.REQUIRED_SOURCE_FILES[-12:])
def test_each_new_source_leaf_rejects_semantic_tampering(monkeypatch, relative):
    target = ROOT / relative
    original = Path.read_text

    def altered(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        return source + '\npass\n' if path.resolve() == target.resolve() else source

    monkeypatch.setattr(Path, 'read_text', altered)
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        certificate.certify_fixed_structural_lot_source()


def test_changed_certificate_function_rejects(monkeypatch):
    target = Path(certificate.__file__).resolve()
    original = Path.read_text

    def altered(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.resolve() == target:
            source = source.replace('    root = Path(__file__).resolve().parents[2]',
                                    '    root = Path(__file__).resolve().parents[1]', 1)
        return source

    monkeypatch.setattr(Path, 'read_text', altered)
    with pytest.raises(ValueError, match='certifier function source differs'):
        certificate.certify_fixed_structural_lot_source()


def test_resealed_loaded_and_fresh_map_still_requires_metadata_anchor(monkeypatch):
    target = Path(certificate.__file__).resolve()
    original = Path.read_text
    pins = dict(certificate.REVIEWED_SOURCE_AST)
    first = certificate.REQUIRED_SOURCE_FILES[0]
    old = pins[first]
    pins[first] = '0' * 64
    monkeypatch.setattr(certificate, 'REVIEWED_SOURCE_AST', pins)

    def altered(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        return source.replace(old, '0' * 64, 1) if path.resolve() == target else source

    monkeypatch.setattr(Path, 'read_text', altered)
    with pytest.raises(ValueError, match='source metadata anchor differs'):
        certificate.certify_fixed_structural_lot_source()
