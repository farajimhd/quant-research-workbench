"""Exact source approval must reject modified extensions and metadata."""
import pytest
from src.backend import backtest_price_risk_certification as cert


@pytest.mark.parametrize('relative', tuple(cert.ADDITIONAL_SOURCE_AST))
def test_each_extension_source_mutation_fails_closed(monkeypatch, relative):
    original = cert._read_local
    def changed(root, path):
        source = original(root, path)
        return source + '\nforeign_unreviewed_rule = True\n' if path == relative else source
    monkeypatch.setattr(cert, '_read_local', changed)
    with pytest.raises(ValueError, match='extension source changed'):
        cert.certify_price_confirmed_original_risk_source()


def test_reviewed_parent_certifier_mutation_fails_closed(monkeypatch):
    original = cert._read_local
    def changed(root, path):
        source = original(root, path)
        return source + '\nforeign_inventory = True\n' if path == cert.BASE_CERTIFIER else source
    monkeypatch.setattr(cert, '_read_local', changed)
    with pytest.raises(ValueError, match='reviewed parent certifier changed'):
        cert.certify_price_confirmed_original_risk_source()


@pytest.mark.parametrize('name,bad', [('BASE_AST', 'a'*64),
    ('ADDITIONAL_SOURCE_AST', {}), ('APPROVED_SELF_AST', 'b'*64)])
def test_loaded_metadata_cannot_replace_fresh_sealed_declarations(monkeypatch, name, bad):
    monkeypatch.setattr(cert, name, bad)
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        cert.certify_price_confirmed_original_risk_source()


def test_foreign_certifier_envelope_fails_closed(monkeypatch):
    original = cert._read_local
    def changed(root, path):
        source = original(root, path)
        return source + '\nforeign_bypass = True\n' if path.endswith('backtest_price_risk_certification.py') else source
    monkeypatch.setattr(cert, '_read_local', changed)
    with pytest.raises(ValueError, match='certifier envelope differs'):
        cert.certify_price_confirmed_original_risk_source()
