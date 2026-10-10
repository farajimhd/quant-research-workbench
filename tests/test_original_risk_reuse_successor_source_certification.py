"""Full source proof and immutable-parent restoration rejection controls."""
from pathlib import Path

import pytest

from src.backend import backtest_original_risk_reuse_successor_certification as cert
from src.backend import backtest_original_risk_reuse_successor_compatibility as compatibility


@pytest.mark.parametrize('relative', tuple(cert.ADDITIONAL_SOURCE_AST))
def test_every_extension_is_pinned(monkeypatch, relative):
    read = cert._read_local
    def changed(root, path):
        original = read(root, path)
        return original + '\nforeign_rule = True\n' if path == relative else original
    monkeypatch.setattr(cert, '_read_local', changed)
    with pytest.raises(ValueError, match='extension source changed'):
        cert.certify_original_risk_reuse_successor_source()


@pytest.mark.parametrize('relative', tuple(compatibility.RESTORATIONS))
def test_shared_restoration_is_exact_and_rejects_foreign_changes(relative):
    root = Path(compatibility.__file__).resolve().parents[2]
    source = (root / relative).read_text(encoding='utf-8')
    restored = compatibility.restore_original_risk_reuse_successor_parent_source(source, relative)
    assert cert._digest(restored) == compatibility.RESTORATIONS[relative]['parent_ast']
    with pytest.raises(ValueError, match='current source differs'):
        compatibility.restore_original_risk_reuse_successor_parent_source(source + '\nforeign_rule = True\n', relative)


@pytest.mark.parametrize('name,value', [('APPROVED_SELF_AST', '0'*64), ('ADDITIONAL_SOURCE_AST', {})])
def test_loaded_metadata_cannot_override_fresh_approval(monkeypatch, name, value):
    monkeypatch.setattr(cert, name, value)
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        cert.certify_original_risk_reuse_successor_source()


def test_normal_registry_and_complete_projection_pass():
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies, numbered_strategy
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    initialize_numbered_fixed_strategies()
    assert numbered_strategy(117).number == 117
    assert len(cert.certify_original_risk_reuse_successor_source()) == 64
    assert len(certify_numbered_fixed_v4_projection(117)) == 64


def test_retained_parent_is_not_reapproved_on_changed_source():
    from src.backend.backtest_combined_price_risk_certification import certify_combined_price_risk_source
    with pytest.raises(ValueError, match='retained parent source changed: ') as rejected:
        certify_combined_price_risk_source()
    rejected_path = str(rejected.value).split('retained parent source changed: ', 1)[1]
    assert rejected_path in compatibility.RESTORATIONS

