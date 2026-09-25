"""Proof that release identity alone cannot recover runtime configuration."""

import hashlib
import json

from src.backend.trading_configuration_service import _runtime_account_binding


def test_same_versioned_binding_resolves_different_runtime_account(monkeypatch):
    binding = {
        "account_key": "paper",
        "source_account_env": "TEST_BACKTEST_SOURCE_ACCOUNT",
        "source_account_id": "",
    }
    release_hash = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    monkeypatch.setenv("TEST_BACKTEST_SOURCE_ACCOUNT", "DU111")
    first = _runtime_account_binding(binding)
    monkeypatch.setenv("TEST_BACKTEST_SOURCE_ACCOUNT", "DU222")
    second = _runtime_account_binding(binding)
    assert first["source_account_id"] == "DU111"
    assert second["source_account_id"] == "DU222"
    assert first != second
    assert release_hash == hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
