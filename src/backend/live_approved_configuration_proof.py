"""Read-only, recheckable Strategy 1 release proof for typed live membership.

This is a control-plane input, not an order-admission permit. It never opens
the legacy SQLite runtime and does not persist a copy of the configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration, certify_strategy_one_configuration,
)
from src.backend.live_strategy_one_approval import (
    ApprovalHeadReader, verify_selected_approval,
)


@dataclass(frozen=True, slots=True)
class ApprovedConfigurationProof:
    identity: str
    content_hash: str
    mode: str
    approval_hash: str
    _release: CertifiedStrategyOneConfiguration
    _client: Any
    _approval_reader: ApprovalHeadReader

    def assert_current(self) -> None:
        """Re-read both immutable release and selected Keeper approval."""
        if certify_strategy_one_configuration(self._client) != self._release:
            raise RuntimeError("Strategy 1 configuration release changed")
        selected = verify_selected_approval(
            self._client, self._approval_reader, mode=self.mode,
            release=self._release)
        if selected["content_hash"] != self.approval_hash:
            raise RuntimeError("Strategy 1 selected approval changed")


def cold_approved_configuration_proof(
    client: Any, approval_reader: ApprovalHeadReader, *, mode: str,
) -> ApprovedConfigurationProof:
    """Bind typed membership to the exact approved release under stable heads."""
    if mode not in {"paper", "live"} or not callable(
            getattr(approval_reader, "read_head", None)):
        raise ValueError("Strategy 1 configuration proof needs an approval head")
    release = certify_strategy_one_configuration(client)
    selected = verify_selected_approval(
        client, approval_reader, mode=mode, release=release)
    proof = ApprovedConfigurationProof(
        identity=release.revision()["revision_id"],
        content_hash=release.payload_hash,
        mode=mode,
        approval_hash=selected["content_hash"],
        _release=release,
        _client=client,
        _approval_reader=approval_reader,
    )
    proof.assert_current()
    return proof
