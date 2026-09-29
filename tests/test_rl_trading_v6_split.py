from datetime import date

import pytest

from research.rl_trading.v6.split import CONTEXT_ONLY, DEVELOPMENT, SEALED_TEST, TRAIN, role


def test_forward_split_excludes_missing_warmup_day():
    assert len(TRAIN) == 16
    assert len(set(CONTEXT_ONLY + TRAIN + DEVELOPMENT + SEALED_TEST)) == 20
    assert role(date(2026, 7, 30)) == 'context_only'
    assert role(date(2026, 7, 31)) == 'train'
    assert role(date(2026, 8, 26)) == 'sealed_test'
    with pytest.raises(ValueError):
        role(date(2026, 7, 29))
