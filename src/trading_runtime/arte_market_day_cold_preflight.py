"""Inactive read-only market-day certificate audit for future fixed Backtest.

This proves typed certificate inventory and historical Keeper attestation only.
Canonical source pins and actual market products require separate verification;
the active Backtest ledger is deliberately not replaced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import date
import json
from types import SimpleNamespace
from typing import Any, Mapping

from src.trading_runtime.arte_market_day_certification import (
    MarketDayCertificate, TABLES, verify_market_day_certificate,
)
from src.trading_runtime.arte_market_day_keeper import (
    BuildAttestation, require_attested_inventory,
)
from src.trading_runtime.arte_market_day_publisher import _placement, _read
from src.trading_runtime.arte_market_day_source_plan import (
    TABLES as SOURCE_TABLES, recover_source_plan,
)


@dataclass(frozen=True)
class MarketDayColdAudit:
    certificate: MarketDayCertificate
    attestation: BuildAttestation
    certificate_parts_on_ssd: bool
    source_plan: Mapping[str, Any]
    source_authority_verified: bool = False
    source_storage_verified: bool = False
    market_products_verified: bool = False
    active_cutover_authorized: bool = False

    @property
    def fixed_backtest_ready(self) -> bool:
        # No caller may treat internal certificate consistency as full source
        # or market-product authority while those verifiers remain unwired.
        return (self.active_cutover_authorized
                and self.source_authority_verified and self.source_storage_verified
                and self.market_products_verified
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
    source_rows = {table.name: tuple(_read(client, table.name, build_id))
                   for table in SOURCE_TABLES}
    source_plan = recover_source_plan(source_rows, build_id,
                                      expected_hash=proof.source_plan_hash)
    _placement(client)
    return verify_source_plan_table_placement(
        client, MarketDayColdAudit(certificate, proof, True, source_plan))


def verify_source_plan_table_placement(client: Any,
                                       audit: MarketDayColdAudit) -> MarketDayColdAudit:
    """Check exact named source-plan table layouts and physical SSD placement."""
    def rows(sql: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]

    policies = rows("SELECT disks FROM system.storage_policies "
                    "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if policies != [{"disks": ["live_market_ssd"]}]:
        raise RuntimeError("Source-plan policy is not SSD-only")
    names = ",".join(f"'{table.name}'" for table in SOURCE_TABLES)
    actual = rows("SELECT name,engine,storage_policy,partition_key,sorting_key "
                  "FROM system.tables WHERE database='arte' "
                  f"AND name IN ({names}) FORMAT JSONEachRow")
    by_name = {row.get("name"): row for row in actual}
    if len(actual) != len(SOURCE_TABLES) or set(by_name) != {
            table.name for table in SOURCE_TABLES}:
        raise RuntimeError("Source-plan tables are missing or duplicate")
    for table in SOURCE_TABLES:
        row = by_name[table.name]
        if (row.get("engine"), row.get("storage_policy"),
            row.get("partition_key"), row.get("sorting_key")) != (
                "MergeTree", "live_market_ssd", table.partition, table.order):
            raise RuntimeError(f"Source-plan table layout differs: {table.name}")
    actual_columns = rows("SELECT table,name,type FROM system.columns "
                          "WHERE database='arte' "
                          f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for table in SOURCE_TABLES:
        columns = tuple((row.get("name"), row.get("type")) for row in actual_columns
                        if row.get("table") == table.name)
        if columns != table.columns:
            raise RuntimeError(f"Source-plan table columns differ: {table.name}")
    if len(actual_columns) != sum(len(table.columns) for table in SOURCE_TABLES):
        raise RuntimeError("Source-plan column inventory has unexpected rows")
    misplaced = rows("SELECT table,disk_name FROM system.parts "
                     "WHERE database='arte' AND active "
                     f"AND table IN ({names}) AND disk_name!='live_market_ssd' "
                     "LIMIT 1 FORMAT JSONEachRow")
    if misplaced:
        raise RuntimeError("Source-plan active part is outside live_market_ssd")
    return replace(audit, source_storage_verified=True)


def verify_canonical_source_plan_parity(audit: MarketDayColdAudit,
                                         current_plan: Mapping[str, Any]
                                         ) -> MarketDayColdAudit:
    """Compare a fresh read-only canonical source-plan replay to sealed rows.

    Callers must obtain ``current_plan`` from the V5 producer's read-only
    ``source_plan`` query path, never from a disk manifest or mutable cache.
    """
    if dict(current_plan) != audit.source_plan:
        raise RuntimeError("Canonical market-day source plan differs from attested typed rows")
    return replace(audit, source_authority_verified=True)


def replay_canonical_source_plan_parity(source_client: Any,
                                        audit: MarketDayColdAudit) -> MarketDayColdAudit:
    """Replay the producer's SELECT-only V5 source plan, not a disk manifest."""
    from scripts.build_market_day import source_plan

    pinned = audit.source_plan
    sessions = list(pinned["sessions"])
    excluded = list(pinned["excluded_calendar_dates"])
    days = [date.fromisoformat(value) for value in (*sessions, *excluded)]
    if not days:
        raise RuntimeError("Attested market-day source plan has no dated range")
    populations = list(pinned["population"])
    explicit = any(row["excluded_canonical_tickers"] is None for row in populations)
    if explicit != all(row["excluded_canonical_tickers"] is None for row in populations):
        raise RuntimeError("Attested market-day population mixes explicit and full-universe scope")
    symbols = tuple(sorted({row["ticker"] for row in pinned["units"]})) if explicit else ()
    args = SimpleNamespace(start=min(days), end=max(days), symbols=symbols,
        allow_carried_forward_universe=any(
            row["certificate"]["status"] == "carried_forward" for row in populations),
        max_plan_units=max(1, len(pinned["units"])))
    fresh = source_plan(source_client, args)
    return verify_canonical_source_plan_parity(audit, fresh)


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
