"""Prepared seals reject semantic changes without granting installed admission."""
from pathlib import Path
import pytest

from src.backend.backtest_strategy_liquidity_fade_certification import (
    LIQUIDITY_FADE_SOURCE_AST, certify_prepared_liquidity_fade_source,
)


def test_prepared_source_proof_is_deterministic_and_fully_pinned():
    proof = certify_prepared_liquidity_fade_source()
    assert len(proof) == 64 and proof == certify_prepared_liquidity_fade_source()
    assert len(LIQUIDITY_FADE_SOURCE_AST) == 57
    assert {
        'src/trading_runtime/strategy_half_risk_liquidity_fade.py',
        'src/backend/backtest_strategy_half_risk_liquidity_fade.py',
    }.issubset(LIQUIDITY_FADE_SOURCE_AST)


@pytest.mark.parametrize('relative', tuple(LIQUIDITY_FADE_SOURCE_AST))
def test_each_prepared_authority_rejects_modified_semantics(relative, tmp_path):
    root = Path(__file__).parents[1]
    changed = tmp_path / 'altered.py'
    source = (root / relative).read_text(encoding='utf-8')
    if relative.endswith('strategy_liquidity_fade_failure.py'):
        source = source.replace('4 * recent > prior', '2 * recent > prior')
    elif relative.endswith('backtest_strategy_liquidity_fade.py'):
        source = source.replace('4 * pl.col("recent_10s_trade_count")', '2 * pl.col("recent_10s_trade_count")')
    else:
        source += '\nunreviewed_authority_change = True\n'
    assert source != (root / relative).read_text(encoding='utf-8')
    changed.write_text(source, encoding='utf-8')
    with pytest.raises(ValueError, match='source changed'):
        certify_prepared_liquidity_fade_source(source_overrides={relative: changed})


def test_unknown_authority_cannot_be_substituted(tmp_path):
    with pytest.raises(ValueError, match='outside prepared authority'):
        certify_prepared_liquidity_fade_source(source_overrides={'elsewhere.py': tmp_path / 'missing.py'})
