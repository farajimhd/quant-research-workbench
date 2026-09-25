"""Inactive read-only market-day certificate audit for future fixed Backtest.

The V3 Keeper proof is issued only after a producer-side canonical source
replay. Fixed Backtest verifies that proof and the persisted products without
replaying market events or reading the producer's disk ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import json
import re
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
    verify_canonical_source_plan_at_publication, verify_source_plan_storage,
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
        client, MarketDayColdAudit(certificate, proof, True, source_plan,
                                   source_authority_verified=True))


def verify_source_plan_table_placement(client: Any,
                                       audit: MarketDayColdAudit) -> MarketDayColdAudit:
    """Check exact named source-plan table layouts and physical SSD placement."""
    verify_source_plan_storage(client)
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
    """Optional producer diagnostic; fixed Backtest never calls this replay."""
    verify_canonical_source_plan_at_publication(source_client, audit.source_plan)
    return replace(audit, source_authority_verified=True)


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


def certified_market_day_plan_from_cold_audit(client: Any,
                                               audit: MarketDayColdAudit, *,
                                               sessions: tuple[str, ...],
                                               tickers: tuple[str, ...],
                                               configuration: Mapping[str, Any]) -> Any:
    """Build the existing fixed Backtest plan token from rechecked typed facts.

    This is an inactive constructor, not permission to replace the active
    SQLite/manifest preflight. Product evidence is rechecked for this exact
    interval and resolution set, even if the audit checked another set.
    """
    from src.backend.backtest_market_data import (
        CertifiedMarketDayPlan, MarketDayUnit, _stable_hash,
        compile_required_resolutions, effective_execution_interval,
        verify_market_day_plan,
    )

    if not (audit.certificate_parts_on_ssd and audit.source_storage_verified
            and audit.source_authority_verified):
        raise RuntimeError("Market-day cold plan lacks attested canonical source parity")
    interval = effective_execution_interval(configuration)
    if interval.kind != "fixed":
        raise ValueError("Event execution does not use the fixed market-day catalogue")
    resolutions = compile_required_resolutions(configuration, interval)
    verify_attested_market_products(client, audit, execution_interval=interval,
                                    required_resolutions_ms=resolutions)
    days = tuple(str(day) for day in sessions)
    if not days or len(set(days)) != len(days):
        raise ValueError("Cold market-day plan needs distinct requested sessions")
    requested = set(audit.source_plan["requested"])
    if not set(days).issubset(requested):
        raise ValueError("Backtest sessions are outside attested source population")
    population = {(day, ticker) for day, ticker in audit.certificate.scopes
                  if day in days}
    selected = ({(day, ticker) for day in days for ticker in tickers}
                if tickers else population)
    if not selected or not selected.issubset(population) or any(
            not any(scope_day == day for scope_day, _ in selected) for day in days):
        raise ValueError("Cold market-day scopes are empty or outside attested population")
    stage_rows = _read(client, "market_day_stage_certificate_v1",
                       audit.certificate.build_id)
    observed = tuple(sorted((r["session_date"], r["ticker"], r["stage"],
                             r["attempt_id"], int(r["output_rows"]),
                             r["output_hash"]) for r in stage_rows))
    if observed != audit.certificate.stages:
        raise RuntimeError("Market-day stage facts changed after Keeper audit")
    units = tuple(MarketDayUnit(r["build_id"], r["session_date"], r["ticker"],
                                r["stage"], r["attempt_id"], r["source_hash"],
                                int(r["output_rows"]), r["output_hash"])
                  for r in sorted(stage_rows,
                                  key=lambda r: (r["session_date"], r["ticker"], r["stage"]))
                  if (r["session_date"], r["ticker"]) in selected)
    if len(units) != 3 * len(selected) or any(
            {unit.stage for unit in units if (unit.session_date, unit.ticker) == scope}
            != {"bars", "technical", "broker_100ms"} for scope in selected):
        raise RuntimeError("Cold market-day plan has incomplete typed stage scope")
    ordered_tickers = tuple(sorted({ticker for _, ticker in selected}))
    payload = {"build_id": audit.certificate.build_id,
               "definition_hash": audit.certificate.definition_hash,
               "sessions": days, "tickers": ordered_tickers,
               "resolutions": resolutions,
               "units": [[unit.build_id, unit.session_date, unit.ticker,
                          unit.stage, unit.attempt_id, unit.source_hash,
                          unit.output_rows, unit.output_hash] for unit in units]}
    plan = CertifiedMarketDayPlan(interval, audit.certificate.build_id,
        audit.certificate.definition_hash, days, ordered_tickers, units,
        resolutions, token=_stable_hash(payload))
    verify_market_day_plan(plan, client)
    return plan


def cold_certified_market_day_plan(certificate_client: Any,
                                   keeper: Any, build_id: str, *,
                                   sessions: tuple[str, ...],
                                   tickers: tuple[str, ...],
                                   configuration: Mapping[str, Any]) -> Any:
    """Inactive SELECT-only replacement candidate for the SQLite plan read.

    Canonical replay is a producer-side prerequisite of the V3 Keeper proof;
    Backtest performs no source-event query. This does not authorize the
    fixed Backtest launch path or grant any writes.
    """
    audit = audit_attested_market_day_certificate(
        certificate_client, keeper, build_id, sessions=sessions)
    return certified_market_day_plan_from_cold_audit(
        certificate_client, audit, sessions=sessions, tickers=tickers,
        configuration=configuration)


def discover_cold_certified_market_day_plan(certificate_client: Any,
                                             keeper: Any, *,
                                             sessions: tuple[str, ...],
                                             tickers: tuple[str, ...],
                                             configuration: Mapping[str, Any]) -> Any:
    """Select one attested build from arte, never a producer disk manifest.

    An explicit build pin resolves overlapping certified builds. Without one,
    ambiguity is a preflight error rather than an arbitrary newest-build guess.
    """
    pinned = str(configuration.get("market_day_build_id") or "").strip()
    if pinned and not re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", pinned):
        raise ValueError("Invalid pinned market-day build identity")
    query = ("SELECT build_id FROM arte.market_day_build_fence_v1 "
             + (f"WHERE build_id='{pinned}' " if pinned else "")
             + "FORMAT JSONEachRow")
    rows = [json.loads(line) for line in certificate_client.execute(query).splitlines()
            if line.strip()]
    ids = [row["build_id"] for row in rows if set(row) == {"build_id"}
           and isinstance(row["build_id"], str)
           and re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", row["build_id"])]
    if len(ids) != len(rows) or len(ids) != len(set(ids)):
        raise RuntimeError("Market-day fence catalogue has invalid or duplicate identities")
    if pinned and ids != [pinned]:
        raise RuntimeError("Pinned market-day build has no unique attested fence")
    if not ids:
        raise RuntimeError("No arte market-day certificate fence is published")
    compatible = []
    errors = []
    for build_id in ids:
        try:
            compatible.append(cold_certified_market_day_plan(
                certificate_client, keeper, build_id, sessions=sessions,
                tickers=tickers, configuration=configuration))
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"{build_id}: {exc}")
    if len(compatible) != 1:
        if compatible:
            raise RuntimeError("Multiple compatible attested market-day builds; pin market_day_build_id")
        raise RuntimeError("No compatible attested market-day build: " + "; ".join(errors[:3]))
    return compatible[0]
