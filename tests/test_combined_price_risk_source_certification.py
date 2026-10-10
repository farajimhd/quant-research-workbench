"""Exact source approval must reject modified extensions and metadata."""
import pytest
from src.backend import backtest_combined_price_risk_certification as cert


@pytest.mark.parametrize('relative', tuple(cert.ADDITIONAL_SOURCE_AST))
def test_each_extension_source_mutation_fails_closed(monkeypatch, relative):
    original = cert._read_local
    def changed(root, path):
        source = original(root, path)
        return source + '\nforeign_unreviewed_rule = True\n' if path == relative else source
    monkeypatch.setattr(cert, '_read_local', changed)
    with pytest.raises(ValueError, match='extension source changed'):
        cert.certify_combined_price_risk_source()


def test_reviewed_parent_certifier_mutation_fails_closed(monkeypatch):
    original = cert._read_local
    def changed(root, path):
        source = original(root, path)
        return source + '\nforeign_inventory = True\n' if path == cert.BASE_CERTIFIER else source
    monkeypatch.setattr(cert, '_read_local', changed)
    with pytest.raises(ValueError, match='reviewed parent certifier changed'):
        cert.certify_combined_price_risk_source()


@pytest.mark.parametrize('name,bad', [('BASE_AST', 'a'*64),
    ('ADDITIONAL_SOURCE_AST', {}), ('APPROVED_SELF_AST', 'b'*64)])
def test_loaded_metadata_cannot_replace_fresh_sealed_declarations(monkeypatch, name, bad):
    monkeypatch.setattr(cert, name, bad)
    with pytest.raises(ValueError, match='loaded and fresh declarations differ'):
        cert.certify_combined_price_risk_source()


def test_foreign_certifier_envelope_fails_closed(monkeypatch):
    original = cert._read_local
    def changed(root, path):
        source = original(root, path)
        return source + '\nforeign_bypass = True\n' if path.endswith('backtest_combined_price_risk_certification.py') else source
    monkeypatch.setattr(cert, '_read_local', changed)
    with pytest.raises(ValueError, match='certifier envelope differs'):
        cert.certify_combined_price_risk_source()


def test_full_native_projection_uses_sealed_historical_parent():
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies, numbered_strategy
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    initialize_numbered_fixed_strategies()
    assert numbered_strategy(115).number == 115
    assert len(certify_numbered_fixed_v4_projection(115)) == 64


def test_retained_strategy113_certificate_still_rejects_changed_source():
    from src.backend.backtest_price_risk_certification import certify_price_confirmed_original_risk_source
    with pytest.raises(ValueError, match='current source differs|retained parent source changed|exact source delta differs'):
        certify_price_confirmed_original_risk_source()


from src.backend import backtest_combined_price_risk_compatibility as compatibility


@pytest.mark.parametrize('relative', tuple(compatibility.RESTORATIONS))
def test_each_shared_source_rejects_unreviewed_mutation(relative):
    from pathlib import Path
    root = Path(compatibility.__file__).resolve().parents[2]
    source = (root / relative).read_text(encoding='utf-8')
    restored = compatibility.restore_combined_price_risk_parent_source(source, relative)
    assert cert._digest(restored) == compatibility.RESTORATIONS[relative]['parent_ast']
    with pytest.raises(ValueError, match='current source differs'):
        compatibility.restore_combined_price_risk_parent_source(
            source + '\nforeign_unreviewed_change = True\n', relative)


def test_retained_strategy114_source_certificate_is_not_reapproved():
    from src.backend.backtest_consecutive_price_risk_certification import certify_consecutive_price_risk_source
    with pytest.raises(ValueError, match='extension source changed|current source differs'):
        certify_consecutive_price_risk_source()


def test_only_journal_capability_changes_financial_policies():
    from src.trading_runtime import strategy_one_hundred_fourteen_release as parent
    from src.trading_runtime import strategy_one_hundred_fifteen_release as child
    from src.trading_runtime.consecutive_price_confirmed_risk import ENTRY_COST_CAPABILITY_RULE, POLICY_KEY
    old=parent.declared_policies()
    new=child.declared_policies()
    assert new[POLICY_KEY].pop('entry_cost_capability')==ENTRY_COST_CAPABILITY_RULE
    assert new==old
    assert child.release_contract().input_contracts==parent.release_contract().input_contracts
    assert child.release_contract().rule_set_contracts==parent.release_contract().rule_set_contracts+(ENTRY_COST_CAPABILITY_RULE,)
