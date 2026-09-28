from __future__ import annotations

from dataclasses import replace

import pytest

from src.backend import live_strategy_one_cold_release as cold
from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration,
)
from tests.test_live_strategy_one_v4_principal import _lease


def _release() -> CertifiedStrategyOneConfiguration:
    return CertifiedStrategyOneConfiguration(
        "00000000-0000-0000-0000-000000000001", "a" * 64,
        "b" * 64, "source", "c" * 64, "d" * 64,
        {"strategy": {"strategy_number": 1}},
    )


class Reader:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_cold_release_uses_live_lease_and_rechecks_release(monkeypatch):
    lease = _lease()
    reader = Reader()
    release = _release()
    calls = []
    monkeypatch.setattr(cold, "certify_strategy_one_configuration",
                        lambda client: calls.append(client) or release)
    monkeypatch.setattr(cold, "KeeperApprovalHeadReader",
                        lambda session: session)
    monkeypatch.setattr(cold, "verify_selected_approval",
                        lambda client, keeper, *, mode, release: {
                            "approval_id": "00000000-0000-0000-0000-000000000002",
                            "content_hash": "e" * 64,
                        })
    try:
        proof = cold.cold_approved_strategy_one_release(
            lease, mode="paper", client_factory=lambda *, lease: reader)
        assert proof.release == release
        assert proof.approval_hash == "e" * 64
        assert calls == [reader, reader]
        assert reader.closed
    finally:
        lease.release()


def test_cold_release_rejects_changed_configuration_and_closes_reader(monkeypatch):
    lease = _lease()
    reader = Reader()
    release = _release()
    values = iter((release, replace(release, token="f" * 64)))
    monkeypatch.setattr(cold, "certify_strategy_one_configuration",
                        lambda _client: next(values))
    monkeypatch.setattr(cold, "KeeperApprovalHeadReader",
                        lambda session: session)
    monkeypatch.setattr(cold, "verify_selected_approval",
                        lambda *_args, **_kwargs: {
                            "approval_id": "00000000-0000-0000-0000-000000000002",
                            "content_hash": "e" * 64,
                        })
    try:
        with pytest.raises(RuntimeError, match="changed during live cold read"):
            cold.cold_approved_strategy_one_release(
                lease, mode="live", client_factory=lambda *, lease: reader)
        assert reader.closed
        with pytest.raises(ValueError, match="exact mode"):
            cold.cold_approved_strategy_one_release(lease, mode="backtest")
        with pytest.raises(ValueError, match="exact mode"):
            cold.cold_approved_strategy_one_release(lease, mode=[])
    finally:
        lease.release()
