import ast

import pytest

from src.backend.backtest_historical_strategy_projection import historical_strategy_tree
from src.trading_runtime.numbered_fixed_strategy import is_numbered_fixed_strategy
from src.trading_runtime.strategy_forty_five_release import release_contract
from src.trading_runtime.strategy_forty_five_runtime import ensure_installed
from src.trading_runtime import strategy_registry as registry


def test_independent_release_installs_native_executor_without_changing_old_inventory(monkeypatch):
    from src.trading_runtime.strategy_one_configuration_tree import encode_nodes
    assert encode_nodes(release_contract().canonical_payload())
    registry.initialize_numbered_fixed_strategies()
    monkeypatch.setattr(registry, "_NUMBERED_RELEASES", dict(registry._NUMBERED_RELEASES))
    monkeypatch.setattr(registry, "_FIXED_REGISTRY", dict(registry._FIXED_REGISTRY))
    old = registry.numbered_strategy(42)
    assert ensure_installed() == registry.numbered_strategy(45) == release_contract()
    assert registry.numbered_strategy(42) == old
    assert ensure_installed() == release_contract()
    fixed = registry.fixed_strategy_executor("squeeze-grid-strategy", 45)
    fixed.verify()
    assert fixed.contract_factory().decision_interval_ms == 1000
    assert fixed.evaluation_interval == "100ms"
    with pytest.raises(ValueError, match="parent"):
        registry.numbered_strategy_parent(45)
    assert is_numbered_fixed_strategy("squeeze-grid-strategy", 45)
    assert not is_numbered_fixed_strategy("squeeze-grid-strategy", True)
    assert not is_numbered_fixed_strategy("early-squeeze-strategy", 45)


def test_historical_projection_removes_only_the_exact_reviewed_new_identity_prefix():
    source = '''def is_numbered_fixed_strategy(strategy_id, revision):
    if strategy_id == "squeeze-grid-strategy" and type(revision) is int and revision == 45:
        return True
    return strategy_id == STRATEGY_ID and revision in (1, 42)
'''
    tree = ast.parse(source)
    projected = historical_strategy_tree(tree, "src/trading_runtime/numbered_fixed_strategy.py")
    assert len(projected.body[0].body) == 1
    assert len(tree.body[0].body) == 2
    for changed in (source.replace("== 45", ">= 45"), source.replace("return True", "return bool(revision)")):
        with pytest.raises(ValueError, match="reviewed prefix"):
            historical_strategy_tree(ast.parse(changed), "trading_runtime/numbered_fixed_strategy.py")
    changed = source.replace("(1, 42)", "(1, 42, 99)")
    # Historical behavior changes remain present for the original certificate
    # to reject; they are never erased or accepted by this projection.
    result = historical_strategy_tree(ast.parse(changed), "trading_runtime/numbered_fixed_strategy.py")
    assert "99" in ast.unparse(result)


def test_old_release_certificate_rejects_changes_to_new_dispatch_or_old_rules(tmp_path):
    from pathlib import Path
    from src.backend.backtest_strategy_forty_two_certification import certify_strategy_forty_two_source
    original = Path("src/trading_runtime/numbered_fixed_strategy.py").read_text(encoding="utf-8")
    path = tmp_path / "numbered_fixed_strategy.py"
    for mutant in (original.replace('revision == 45:', 'revision >= 45:'),
                   original.replace('strategy_number == 1 or', 'strategy_number == 99 or')):
        assert mutant != original
        path.write_text(mutant, encoding="utf-8")
        with pytest.raises(ValueError):
            certify_strategy_forty_two_source(source_overrides={
                "src/trading_runtime/numbered_fixed_strategy.py": path})
