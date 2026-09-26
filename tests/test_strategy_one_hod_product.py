"""HOD context content hashes are deterministic and reject duplicate keys."""

import pytest

from src.trading_runtime.strategy_one_hod_product import (
    HodContext, context_content_hash,
)


def test_normalized_hod_context_hash_changes_with_gate():
    first = HodContext(100, 100_000, 120_000, True, "")
    second = HodContext(200, 100_000, 120_000, True, "r11")
    assert len(context_content_hash((first, second))) == 64
    assert context_content_hash((first, second)) != context_content_hash((
        first, HodContext(200, 100_000, 120_000, True, "")))
    with pytest.raises(ValueError, match="unordered"):
        context_content_hash((first, first))


def test_incomplete_hod_context_cannot_be_certified():
    with pytest.raises(ValueError, match="invalid"):
        context_content_hash((HodContext(100, 0, 0, False, ""),))
