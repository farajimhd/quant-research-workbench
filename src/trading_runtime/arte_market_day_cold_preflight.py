"""Inactive read-only market-day certificate audit for future fixed Backtest.

This proves typed certificate inventory and historical Keeper attestation only.
Canonical source pins and actual market products require separate verification;
the active Backtest ledger is deliberately not replaced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.trading_runtime.arte_market_day_certification import (
    MarketDayCertificate, TABLES, verify_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import (
    BuildAttestation, require_attested_inventory,
)
from src.trading_runtime.arte_market_day_publisher import _placement, _read


@dataclass(frozen=True)
class MarketDayColdAudit:
    certificate: MarketDayCertificate
    attestation: BuildAttestation
    certificate_parts_on_ssd: bool
    source_authority_verified: bool = False
    market_products_verified: bool = False

    @property
    def fixed_backtest_ready(self) -> bool:
        # No caller may treat internal certificate consistency as full source
        # or market-product authority while those verifiers remain unwired.
        return (self.source_authority_verified and self.market_products_verified
                and self.certificate_parts_on_ssd)


def audit_attested_market_day_certificate(client: Any, keeper: Any,
                                           build_id: str, *, sessions: tuple[str, ...]
                                           ) -> MarketDayColdAudit:
    """Fail closed on absent, partial, duplicate, unplaced or unattested facts."""
    _placement(client)
    certificate = verify_market_day_certificate(client, build_id, sessions=sessions)
    fence_name = TABLES[-1].name
    fences = _read(client, fence_name, build_id)
    if len(fences) != 1:
        raise RuntimeError("Market-day cold audit lacks one final fence")
    proof = keeper.load(build_id)
    require_attested_inventory(proof, fences[0])
    _placement(client)
    return MarketDayColdAudit(certificate, proof, True)
