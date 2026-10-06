"""The error-only helper remains inside the complete inherited source seal."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend import backtest_strategy_liquidity_fade_certification as leaf
from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection

ROOT = Path(__file__).parents[1]
SOURCE = "src/trading_runtime/arte_journal_projection.py"
NUMBERS = (1, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)


@pytest.mark.parametrize("number", NUMBERS)
def test_existing_complete_native_proof(number):
    assert len(certify_numbered_fixed_v4_projection(number)) == 64


@pytest.mark.parametrize("number", (42, 50, 61))
def test_private_diagnostic_mutation_is_rejected_by_native_cold_closure(tmp_path, monkeypatch, number):
    source = (ROOT / SOURCE).read_text(encoding="utf8")
    tree = ast.parse(source)
    helper = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_decimal_failure_diagnostic")
    helper.body = [ast.Return(value=ast.Constant(value="foreign diagnostic"))]
    changed = tmp_path / "arte_journal_projection.py"
    changed.write_text(ast.unparse(tree), encoding="utf8")
    assert sha256(ast.unparse(tree).encode()).hexdigest() != leaf.LIQUIDITY_FADE_SOURCE_AST[SOURCE]
    original = leaf.certify_prepared_liquidity_fade_source
    monkeypatch.setattr(leaf, "certify_prepared_liquidity_fade_source",
                        lambda: original(source_overrides={SOURCE: changed}))
    with pytest.raises(ValueError, match="Prepared liquidity source changed: " + SOURCE):
        certify_numbered_fixed_v4_projection(number)


def test_unknown_override_still_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="outside prepared authority"):
        leaf.certify_prepared_liquidity_fade_source(
            source_overrides={"src/unselected_diagnostic.py": tmp_path / "unused"})
