from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files  # noqa: E402
from services.reference_gateway.ibkr_contract_identity import (  # noqa: E402
    expected_ibkr_listing_exchange,
    normalize_equity_symbol,
    resolve_massive_ibkr_contract,
)
from services.reference_gateway.active_tickers import ActiveTickerPlan, MissingTickerCandidate  # noqa: E402
from services.reference_gateway.canonical_graph_writer import write_canonical_graph_candidates  # noqa: E402
from services.reference_gateway.issue_resolution import resolve_massive_active_ticker_issues  # noqa: E402
from services.reference_gateway.issue_writer import write_graph_mapping_issues  # noqa: E402
from services.reference_gateway.publication_rebuild import rebuild_tradable_publications  # noqa: E402
from services.reference_gateway.providers import IbkrReferenceClient, MassiveReferenceClient  # noqa: E402
from services.gateway_policy import active_collection_window  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit every active Massive common stock that is missing or excluded from the current "
            "Reference universe against IBKR's primary stock-contract definitions. The default is read-only; "
            "--execute writes only candidates with complete Massive CIK/FIGI identity and one exact IBKR conid."
        )
    )
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--write-database", default="")
    parser.add_argument("--output-root", type=Path, default=Path("D:/TradingML/runtimes/reference_gateway/conid_audits"))
    parser.add_argument("--ibkr-timeout-seconds", type=int, default=60)
    parser.add_argument("--execute", action="store_true", help="Write only fully identified accepted contracts into the canonical graph.")
    parser.add_argument("--rebuild-publications", action="store_true", help="Rebuild tradable/scanner publications after successful graph writes.")
    parser.add_argument("--force-active-window", action="store_true")
    parser.add_argument("--maintenance-reason", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.write_database = args.write_database or args.database
    if args.rebuild_publications and not args.execute:
        raise SystemExit("--rebuild-publications requires --execute")
    if args.execute and not args.maintenance_reason.strip():
        raise SystemExit("--maintenance-reason is required with --execute")
    if args.execute and active_collection_window(datetime.now(UTC), service_prefix="REFERENCE") and not args.force_active_window:
        raise SystemExit("Reference active collection window is open; rerun later or pass --force-active-window with an auditable reason")
    load_env_files(discover_clickhouse_env_files())
    checked_at = datetime.now(UTC)
    run_id = "ibkr_conid_audit_" + checked_at.strftime("%Y%m%d_%H%M%S")
    run_root = args.output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    rows_path = run_root / "ticker_audit.jsonl"
    summary_path = run_root / "summary.json"

    print("IBKR conid identity audit")
    print("scope=active Massive common stocks missing or excluded from current Reference universe")
    print(f"mode={'execute' if args.execute else 'read-only'} fail-closed")

    massive = MassiveReferenceClient(
        base_url=os.environ.get("MASSIVE_BASE_URL", "https://api.massive.com"),
        api_key=os.environ.get("MASSIVE_API_KEY", ""),
        page_limit=1_000,
        max_pages=1_000,
    )
    print("stage=massive_inventory status=active")
    provider_result = massive.fetch_active_us_stock_tickers()
    if provider_result.saturated:
        raise RuntimeError("Massive active ticker inventory saturated its configured page bound")
    provider_by_ticker, provider_case_conflicts = unique_provider_rows(provider_result.tickers)
    print(f"stage=massive_inventory status=completed tickers={len(provider_by_ticker):,} pages={provider_result.pages:,}")

    clickhouse = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    print("stage=current_universe status=active")
    universe_date, universe_rows = load_current_universe(clickhouse, args.database)
    universe_by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in universe_rows:
        universe_by_ticker[str(row.get("ticker") or "").upper()].append(row)
    target_tickers = target_common_stocks(provider_by_ticker, universe_by_ticker)
    print(
        f"stage=current_universe status=completed universe_date={universe_date} "
        f"rows={len(universe_rows):,} target_tickers={len(target_tickers):,}"
    )

    ibkr = IbkrReferenceClient(
        base_url=os.environ.get("IBKR_CPAPI_BASE_URL", "https://localhost:5000/v1/api"),
        timeout_seconds=max(8, args.ibkr_timeout_seconds),
    )
    auth = ibkr.auth_status()
    if not bool(auth.get("authenticated")) or not bool(auth.get("connected")):
        raise RuntimeError(f"IBKR Client Portal is not authenticated and connected: {auth}")

    target_exchanges = sorted(
        {
            expected_ibkr_listing_exchange(provider_by_ticker[ticker].get("primary_exchange"))
            for ticker in target_tickers
            if expected_ibkr_listing_exchange(provider_by_ticker[ticker].get("primary_exchange"))
        }
    )
    print(f"stage=ibkr_exchange_inventory status=active exchanges={len(target_exchanges):,}")
    conids_by_exchange_symbol: dict[tuple[str, str], set[int]] = defaultdict(set)
    exchange_row_counts: dict[str, int] = {}
    for exchange in target_exchanges:
        rows = ibkr.fetch_all_stock_conids(exchange)
        exchange_row_counts[exchange] = len(rows)
        for row in rows:
            try:
                conid = int(row.get("conid") or 0)
            except (TypeError, ValueError):
                continue
            symbol = normalize_equity_symbol(row.get("ticker"))
            if conid > 0 and symbol:
                conids_by_exchange_symbol[(exchange, symbol)].add(conid)
        print(f"stage=ibkr_exchange_inventory status=progress exchange={exchange} rows={len(rows):,}")
    print(f"stage=ibkr_exchange_inventory status=completed rows={sum(exchange_row_counts.values()):,}")

    candidate_conids_by_ticker: dict[str, tuple[int, ...]] = {}
    for ticker in target_tickers:
        exchange = expected_ibkr_listing_exchange(provider_by_ticker[ticker].get("primary_exchange"))
        candidate_conids_by_ticker[ticker] = tuple(
            sorted(conids_by_exchange_symbol.get((exchange, normalize_equity_symbol(ticker)), set()))
        )
    candidate_conids = sorted({conid for values in candidate_conids_by_ticker.values() for conid in values})
    print(f"stage=ibkr_contract_definitions status=active conids={len(candidate_conids):,}")
    definitions_by_conid: dict[int, dict[str, Any]] = {}
    for offset in range(0, len(candidate_conids), 200):
        batch = candidate_conids[offset : offset + 200]
        for row in ibkr.fetch_security_definitions(batch):
            try:
                conid = int(row.get("conid") or row.get("con_id") or 0)
            except (TypeError, ValueError):
                continue
            if conid > 0:
                definitions_by_conid[conid] = row
        print(
            f"stage=ibkr_contract_definitions status=progress "
            f"completed={min(offset + len(batch), len(candidate_conids)):,}/{len(candidate_conids):,}"
        )
    print(f"stage=ibkr_contract_definitions status=completed definitions={len(definitions_by_conid):,}")

    results: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    print(f"stage=ticker_validation status=active queued={len(target_tickers):,}")
    for ticker in target_tickers:
        provider = provider_by_ticker[ticker]
        current_rows = universe_by_ticker.get(ticker, [])
        candidate_conids_for_ticker = candidate_conids_by_ticker[ticker]
        resolution = resolve_massive_ibkr_contract(
            massive_ticker=ticker,
            massive_name=str(provider.get("name") or ""),
            massive_exchange=str(provider.get("primary_exchange") or ""),
            definitions=[definitions_by_conid[conid] for conid in candidate_conids_for_ticker if conid in definitions_by_conid],
        )
        existing_conids = sorted(
            {
                int(row.get("ibkr_conid") or 0)
                for row in current_rows
                if str(row.get("ibkr_conid") or "").isdigit() and int(row.get("ibkr_conid") or 0) > 0
            }
        )
        if not resolution.accepted:
            outcome = "blocked_" + resolution.reason
        elif not current_rows:
            outcome = "accepted_missing_from_canonical_universe"
        elif resolution.conid in existing_conids:
            outcome = "accepted_existing_conid_matches"
        else:
            outcome = "unsafe_existing_conid_mismatch"
        outcome_counts[outcome] += 1
        record = {
            "ticker": ticker,
            "massive": {
                "name": provider.get("name"),
                "primary_exchange": provider.get("primary_exchange"),
                "currency_symbol": provider.get("currency_symbol"),
                "type": provider.get("type"),
                "cik": provider.get("cik"),
                "composite_figi": provider.get("composite_figi"),
                "share_class_figi": provider.get("share_class_figi"),
            },
            "current_universe": {
                "present": bool(current_rows),
                "is_tradable": any(int(row.get("is_tradable") or 0) == 1 for row in current_rows),
                "existing_conids": existing_conids,
                "exclusion_reasons": sorted({str(row.get("exclusion_reason") or "") for row in current_rows if row.get("exclusion_reason")}),
            },
            "ibkr": asdict(resolution),
            "outcome": outcome,
        }
        record["evidence_sha256"] = sha256_json(record)
        results.append(record)
    write_jsonl(rows_path, results)

    repair: dict[str, Any] = {"status": "not_requested"}
    if args.execute:
        repair = execute_repairs(
            args=args,
            checked_at=checked_at,
            provider_by_ticker=provider_by_ticker,
            definitions_by_conid=definitions_by_conid,
            results=results,
            clickhouse=clickhouse,
        )

    summary = {
        "contract_version": "reference_ibkr_conid_audit_v1",
        "run_id": run_id,
        "checked_at_utc": checked_at.isoformat(),
        "mode": "execute" if args.execute else "read_only",
        "database": args.database,
        "universe_date": universe_date,
        "massive_active_unique_tickers": len(provider_by_ticker),
        "massive_case_collisions": provider_case_conflicts,
        "target_common_stocks": len(target_tickers),
        "outcomes": dict(sorted(outcome_counts.items())),
        "ibkr_exchange_rows": exchange_row_counts,
        "candidate_conids": len(candidate_conids),
        "contract_definitions": len(definitions_by_conid),
        "repair": repair,
        "ticker_audit_path": str(rows_path),
    }
    summary["evidence_sha256"] = sha256_json(summary)
    write_json(summary_path, summary)
    print(
        "stage=ticker_validation status=completed "
        + " ".join(f"{key}={value:,}" for key, value in sorted(outcome_counts.items()))
    )
    print(f"result={summary_path}")


def execute_repairs(
    *,
    args: argparse.Namespace,
    checked_at: datetime,
    provider_by_ticker: dict[str, dict[str, Any]],
    definitions_by_conid: dict[int, dict[str, Any]],
    results: list[dict[str, Any]],
    clickhouse: ClickHouseHttpClient,
) -> dict[str, Any]:
    eligible_outcomes = {"accepted_missing_from_canonical_universe", "unsafe_existing_conid_mismatch"}
    candidates: list[MissingTickerCandidate] = []
    skipped_incomplete_identity: list[str] = []
    for result in results:
        if result["outcome"] not in eligible_outcomes:
            continue
        massive = result["massive"]
        conid = int(result["ibkr"].get("conid") or 0)
        figi = str(massive.get("share_class_figi") or massive.get("composite_figi") or "").strip()
        cik = str(massive.get("cik") or "").strip()
        if not cik or not figi or conid not in definitions_by_conid:
            skipped_incomplete_identity.append(result["ticker"])
            continue
        provider = provider_by_ticker[result["ticker"]]
        candidates.append(
            MissingTickerCandidate(
                ticker=result["ticker"],
                name=str(provider.get("name") or ""),
                market=str(provider.get("market") or ""),
                locale=str(provider.get("locale") or ""),
                primary_exchange=str(provider.get("primary_exchange") or ""),
                currency_symbol=str(provider.get("currency_symbol") or provider.get("currency_name") or ""),
                cik=cik,
                composite_figi=str(massive.get("composite_figi") or ""),
                share_class_figi=str(massive.get("share_class_figi") or ""),
                ticker_type=str(provider.get("type") or ""),
                missing_reason="ibkr_conid_identity_audit",
                overview=provider,
                ibkr_candidates=[definitions_by_conid[conid]],
                proposed_action="candidate_ready_for_dry_run_graph_resolution",
            )
        )
    config = SimpleNamespace(
        clickhouse_url=default_clickhouse_url(),
        clickhouse_user=default_clickhouse_user(),
        clickhouse_read_database=args.database,
        clickhouse_write_database=args.write_database,
        execute=True,
        test_write_mode=args.database != args.write_database,
        rebuild_tradable_in_test_mode=True,
    )
    plan = ActiveTickerPlan(
        checked_at_utc=checked_at.isoformat(),
        provider_rows=len(provider_by_ticker),
        provider_pages=0,
        provider_saturated=False,
        known_active_symbols=0,
        missing_tickers=len(candidates),
        overview_fetched=0,
        ibkr_searched=len(candidates),
        candidate_limit=len(candidates),
        candidates=candidates,
        wall_seconds=0.0,
    )
    print(
        f"stage=canonical_repair status=active candidates={len(candidates):,} "
        f"blocked_incomplete_identity={len(skipped_incomplete_identity):,}"
    )
    graph_result = write_canonical_graph_candidates(config, plan)
    issue_result = write_graph_mapping_issues(config, graph_result.issues)
    resolution_result = resolve_massive_active_ticker_issues(
        clickhouse,
        config,
        tickers=graph_result.accepted_tickers,
    )
    rebuild_result: dict[str, Any] = {"status": "not_requested"}
    if args.rebuild_publications and graph_result.accepted_tickers:
        rebuild_result = asdict(
            rebuild_tradable_publications(
                config,
                reason="ibkr_conid_identity_audit: " + args.maintenance_reason.strip(),
            )
        )
    print(
        f"stage=canonical_repair status=completed accepted={graph_result.accepted_candidates:,} "
        f"issues={graph_result.issue_candidates:,} resolved_issue_rows={resolution_result.resolved:,}"
    )
    return {
        "status": "completed",
        "maintenance_reason": args.maintenance_reason.strip(),
        "eligible_candidates": len(candidates),
        "skipped_incomplete_identity": len(skipped_incomplete_identity),
        "skipped_incomplete_identity_tickers": skipped_incomplete_identity,
        "graph": asdict(graph_result),
        "issue_write": asdict(issue_result),
        "issue_resolution": asdict(resolution_result),
        "publication_rebuild": rebuild_result,
    }


def unique_provider_rows(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            grouped[ticker].append(row)
    selected: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for ticker, values in grouped.items():
        signatures = {sha256_json(value) for value in values}
        if len(signatures) == 1:
            selected[ticker] = values[0]
            continue
        common_stocks = [value for value in values if str(value.get("type") or "").upper() == "CS"]
        conflicts.append(
            {
                "normalized_ticker": ticker,
                "raw_tickers": [str(value.get("ticker") or "") for value in values],
                "types": [str(value.get("type") or "") for value in values],
                "selected_common_stock": str(common_stocks[0].get("ticker") or "") if len(common_stocks) == 1 else "",
            }
        )
        if len(common_stocks) != 1:
            raise RuntimeError(f"Massive returned conflicting active common-stock rows for ticker {ticker}")
        selected[ticker] = common_stocks[0]
    return selected, conflicts


def load_current_universe(client: ClickHouseHttpClient, database: str) -> tuple[str, list[dict[str, Any]]]:
    database_name = "`" + database.replace("`", "``") + "`"
    query = f"""
    SELECT
        toString(universe_date) AS universe_date_text,
        upper(ticker) AS ticker,
        is_tradable,
        ifNull(exclusion_reason, '') AS exclusion_reason,
        ifNull(ibkr_conid, '') AS ibkr_conid,
        exchange_code,
        currency_code,
        product_type,
        listing_id,
        symbol_id
    FROM {database_name}.feature_tradable_universe_v1 FINAL
    WHERE universe_date = (SELECT max(universe_date) FROM {database_name}.feature_tradable_universe_v1)
    FORMAT JSONEachRow
    """
    rows = [json.loads(line) for line in client.execute(query).splitlines() if line.strip()]
    dates = {str(row.get("universe_date_text") or "") for row in rows}
    if len(dates) != 1:
        raise RuntimeError(f"Expected one current universe date, found {sorted(dates)}")
    return next(iter(dates)), rows


def target_common_stocks(
    provider_by_ticker: dict[str, dict[str, Any]],
    universe_by_ticker: dict[str, list[dict[str, Any]]],
) -> list[str]:
    target: list[str] = []
    for ticker, provider in provider_by_ticker.items():
        if str(provider.get("type") or "").strip().upper() != "CS":
            continue
        current = universe_by_ticker.get(ticker, [])
        if current and any(int(row.get("is_tradable") or 0) == 1 for row in current):
            continue
        target.append(ticker)
    return sorted(target)


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str) for row in rows)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
