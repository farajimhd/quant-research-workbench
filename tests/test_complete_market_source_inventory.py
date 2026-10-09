"""Prepared full inventory deliberately issues no source approval yet."""
from pathlib import Path

import pytest

from src.backend.backtest_fixed_structural_lot_certification_v18 import (
    REQUIRED_SOURCE_FILES, certify_fixed_structural_lot_source,
)


def test_complete_market_inventory_contains_reader_and_native_owners_and_stays_closed():
    root = Path(__file__).resolve().parents[1]
    assert len(REQUIRED_SOURCE_FILES) == len(set(REQUIRED_SOURCE_FILES))
    assert all((root / relative).is_file() for relative in REQUIRED_SOURCE_FILES)
    assert set((
        'src/backend/backtest_complete_market_response.py',
        'src/backend/backtest_complete_market_window.py',
        'src/backend/backtest_complete_market_source.py',
        'src/backend/backtest_installed_complete_market_source.py',
        'src/backend/backtest_liquidity_price.py',
        'src/backend/backtest_strategy_one_scheduler.py',
        'src/backend/backtest_fixed_structural_lot_native_v18.py',
        'src/trading_runtime/complete_market_window_policy.py',
    )) <= set(REQUIRED_SOURCE_FILES)
    with pytest.raises(ValueError, match='source seal is unapproved'):
        certify_fixed_structural_lot_source()
