"""Inactive read-only market-day certificate audit for future fixed Backtest.

This proves typed certificate inventory and historical Keeper attestation only.
Canonical source pins and actual market products require separate verification;
the active Backtest ledger is deliberately not replaced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import json
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


def verify_attested_market_products(client: Any, audit: MarketDayColdAudit, *,
                                     execution_interval: Any,
                                     required_resolutions_ms: tuple[int, ...]
                                     ) -> MarketDayColdAudit:
    """Recheck pinned V1 products and their disks; source proof remains absent."""
    from src.backend.backtest_market_data import (
        CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
        verify_market_day_plan,
    )

    interval = ExecutionInterval.parse(execution_interval)
    if (interval.kind != "fixed" or not required_resolutions_ms
            or any(type(value) is not int or value < 100 or value % 100
                   for value in required_resolutions_ms)):
        raise ValueError("Market-product audit requires fixed interval and exact resolutions")
    names = ("bars_v1", "indicators_v1", "liquidity_100ms_v1")
    quoted = ",".join(f"'{name}'" for name in names)
    tables = [json.loads(line) for line in client.execute(
        "SELECT name,storage_policy FROM system.tables WHERE database='arte' "
        f"AND name IN ({quoted}) FORMAT JSONEachRow").splitlines() if line.strip()]
    if (len(tables) != len(names) or {row.get("name") for row in tables} != set(names)
            or any(row.get("storage_policy") != "live_market_ssd" for row in tables)):
        raise RuntimeError("Market-day product table policy is not live_market_ssd")
    parts = [json.loads(line) for line in client.execute(
        "SELECT table,disk_name FROM system.parts WHERE active AND database='arte' "
        f"AND table IN ({quoted}) FORMAT JSONEachRow").splitlines() if line.strip()]
    if any(row.get("table") not in names or row.get("disk_name") != "live_market_ssd"
           for row in parts):
        raise RuntimeError("Market-day product part is outside live_market_ssd")
    rows = _read(client, "market_day_stage_certificate_v1", audit.certificate.build_id)
    observed = sorted((r["session_date"], r["ticker"], r["stage"], r["attempt_id"],
                       int(r["output_rows"]), r["output_hash"]) for r in rows)
    if tuple(observed) != audit.certificate.stages:
        raise RuntimeError("Market-day stage facts changed after Keeper audit")
    units = tuple(MarketDayUnit(r["build_id"], r["session_date"], r["ticker"],
                                r["stage"], r["attempt_id"], r["source_hash"],
                                int(r["output_rows"]), r["output_hash"]) for r in rows)
    plan = CertifiedMarketDayPlan(interval, audit.certificate.build_id,
        audit.certificate.definition_hash,
        tuple(sorted({day for day, _ in audit.certificate.scopes})),
        tuple(sorted({ticker for _, ticker in audit.certificate.scopes})),
        units, required_resolutions_ms, token="unadmitted-certificate-audit")
    verify_market_day_plan(plan, client)
    return replace(audit, market_products_verified=True)
