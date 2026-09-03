from __future__ import annotations

import argparse
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
from scripts.audit_reference_ibkr_conids import (  # noqa: E402
    load_current_universe,
    sha256_json,
    unique_provider_rows,
    write_json,
    write_jsonl,
)
from services.reference_gateway.ibkr_contract_identity import (  # noqa: E402
    expected_ibkr_listing_exchange,
    ibkr_search_symbols,
    normalize_equity_symbol,
    positive_conids,
    published_contract_matches,
    resolve_massive_ibkr_contract,
)
from services.reference_gateway.providers import IbkrReferenceClient, MassiveReferenceClient  # noqa: E402
from services.reference_gateway.canonical_graph_writer import GraphWriteIssue  # noqa: E402
from services.reference_gateway.issue_writer import write_graph_mapping_issues  # noqa: E402
from services.reference_gateway.publication_rebuild import rebuild_tradable_publications  # noqa: E402
from services.gateway_policy import active_collection_window  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed audit of every ticker in the current tradable universe against the current "
            "Massive listing and IBKR primary stock-contract inventory. This command is read-only."
        )
    )
    parser.add_argument("--database", default="q_live")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("D:/TradingML/runtimes/reference_gateway/tradable_conid_audits"),
    )
    parser.add_argument("--ibkr-timeout-seconds", type=int, default=60)
    parser.add_argument("--quarantine-unsafe", action="store_true")
    parser.add_argument("--rebuild-publications", action="store_true")
    parser.add_argument("--force-active-window", action="store_true")
    parser.add_argument("--maintenance-reason", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rebuild_publications and not args.quarantine_unsafe:
        raise SystemExit("--rebuild-publications requires --quarantine-unsafe")
    if args.quarantine_unsafe and not args.maintenance_reason.strip():
        raise SystemExit("--maintenance-reason is required with --quarantine-unsafe")
    if (
        args.quarantine_unsafe
        and active_collection_window(datetime.now(UTC), service_prefix="REFERENCE")
        and not args.force_active_window
    ):
        raise SystemExit("Reference active collection window is open; use --force-active-window with an auditable reason")
    load_env_files(discover_clickhouse_env_files())
    checked_at = datetime.now(UTC)
    run_id = "tradable_ibkr_conid_audit_" + checked_at.strftime("%Y%m%d_%H%M%S")
    run_root = args.output_root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    rows_path = run_root / "ticker_audit.jsonl"
    summary_path = run_root / "summary.json"

    print("Tradable Massive-to-IBKR conid audit", flush=True)
    print("mode=read-only fail-closed scope=current tradable universe", flush=True)
    massive = MassiveReferenceClient(
        base_url=os.environ.get("MASSIVE_BASE_URL", "https://api.massive.com"),
        api_key=os.environ.get("MASSIVE_API_KEY", ""),
        page_limit=1_000,
        max_pages=1_000,
    )
    print("stage=massive_inventory status=active", flush=True)
    provider_result = massive.fetch_active_us_stock_tickers()
    if provider_result.saturated:
        raise RuntimeError("Massive active ticker inventory saturated its configured page bound")
    provider_by_ticker, provider_case_conflicts = unique_provider_rows(provider_result.tickers)
    provider_by_symbol_key: dict[str, dict[str, Any]] = {}
    duplicate_provider_symbol_keys: set[str] = set()
    for provider in provider_by_ticker.values():
        symbol_key = normalize_equity_symbol(provider.get("ticker"))
        if symbol_key in provider_by_symbol_key:
            duplicate_provider_symbol_keys.add(symbol_key)
        else:
            provider_by_symbol_key[symbol_key] = provider
    print(
        f"stage=massive_inventory status=completed tickers={len(provider_by_ticker):,} "
        f"case_collisions={len(provider_case_conflicts):,}",
        flush=True,
    )

    clickhouse = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    universe_date, universe_rows = load_current_universe(clickhouse, args.database)
    tradable_rows = [row for row in universe_rows if int(row.get("is_tradable") or 0) == 1]
    rows_by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tradable_rows:
        rows_by_ticker[str(row.get("ticker") or "").strip().upper()].append(row)
    target_tickers = sorted(rows_by_ticker)
    print(
        f"stage=current_universe status=completed universe_date={universe_date} "
        f"tradable_rows={len(tradable_rows):,} tradable_tickers={len(target_tickers):,}",
        flush=True,
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
            expected_ibkr_listing_exchange(provider_by_symbol_key[normalize_equity_symbol(ticker)].get("primary_exchange"))
            for ticker in target_tickers
            if normalize_equity_symbol(ticker) in provider_by_symbol_key
            and normalize_equity_symbol(ticker) not in duplicate_provider_symbol_keys
            and expected_ibkr_listing_exchange(
                provider_by_symbol_key[normalize_equity_symbol(ticker)].get("primary_exchange")
            )
        }
    )
    conids_by_exchange_symbol: dict[tuple[str, str], set[int]] = defaultdict(set)
    exchange_row_counts: dict[str, int] = {}
    print(f"stage=ibkr_exchange_inventory status=active exchanges={len(target_exchanges):,}", flush=True)
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
        print(f"stage=ibkr_exchange_inventory status=progress exchange={exchange} rows={len(rows):,}", flush=True)

    candidate_conids_by_ticker: dict[str, tuple[int, ...]] = {}
    stored_conids_by_ticker: dict[str, tuple[int, ...]] = {}
    all_conids: set[int] = set()
    for ticker in target_tickers:
        provider = provider_by_symbol_key.get(normalize_equity_symbol(ticker), {})
        exchange = expected_ibkr_listing_exchange(provider.get("primary_exchange"))
        candidates = tuple(sorted(conids_by_exchange_symbol.get((exchange, normalize_equity_symbol(ticker)), set())))
        stored = positive_stored_conids(rows_by_ticker[ticker])
        candidate_conids_by_ticker[ticker] = candidates
        stored_conids_by_ticker[ticker] = stored
        all_conids.update(candidates)
        all_conids.update(stored)

    ordered_conids = sorted(all_conids)
    definitions_by_conid: dict[int, dict[str, Any]] = {}
    print(f"stage=ibkr_contract_definitions status=active conids={len(ordered_conids):,}", flush=True)
    for offset in range(0, len(ordered_conids), 200):
        batch = ordered_conids[offset : offset + 200]
        for row in ibkr.fetch_security_definitions(batch):
            try:
                conid = int(row.get("conid") or row.get("con_id") or 0)
            except (TypeError, ValueError):
                continue
            if conid > 0:
                definitions_by_conid[conid] = row
        print(
            f"stage=ibkr_contract_definitions status=progress "
            f"completed={min(offset + len(batch), len(ordered_conids)):,}/{len(ordered_conids):,}",
            flush=True,
        )

    fallback_conids_by_ticker: dict[str, set[int]] = defaultdict(set)
    for ticker in target_tickers:
        symbol_key = normalize_equity_symbol(ticker)
        provider = provider_by_symbol_key.get(symbol_key)
        if provider is None or symbol_key in duplicate_provider_symbol_keys:
            continue
        candidates = candidate_conids_by_ticker[ticker]
        preliminary = resolve_massive_ibkr_contract(
            massive_ticker=str(provider.get("ticker") or ticker),
            massive_name=str(provider.get("name") or ""),
            massive_exchange=str(provider.get("primary_exchange") or ""),
            definitions=[definitions_by_conid[conid] for conid in candidates if conid in definitions_by_conid],
        )
        if preliminary.accepted:
            continue
        search_rows: list[dict[str, Any]] = []
        for search_symbol in ibkr_search_symbols(str(provider.get("ticker") or ticker)):
            search_rows.extend(ibkr.search_stock_contracts(search_symbol))
        fallback_conids_by_ticker[ticker].update(positive_conids(search_rows))
    fallback_conids = sorted(
        {conid for values in fallback_conids_by_ticker.values() for conid in values if conid not in definitions_by_conid}
    )
    if fallback_conids:
        print(f"stage=ibkr_search_fallback status=active conids={len(fallback_conids):,}", flush=True)
        for offset in range(0, len(fallback_conids), 200):
            for row in ibkr.fetch_security_definitions(fallback_conids[offset : offset + 200]):
                try:
                    conid = int(row.get("conid") or row.get("con_id") or 0)
                except (TypeError, ValueError):
                    continue
                if conid > 0:
                    definitions_by_conid[conid] = row
        print(f"stage=ibkr_search_fallback status=completed definitions={len(fallback_conids):,}", flush=True)
    for ticker, values in fallback_conids_by_ticker.items():
        candidate_conids_by_ticker[ticker] = tuple(sorted(set(candidate_conids_by_ticker[ticker]) | values))

    results: list[dict[str, Any]] = []
    outcomes: Counter[str] = Counter()
    for ticker in target_tickers:
        current_rows = rows_by_ticker[ticker]
        symbol_key = normalize_equity_symbol(ticker)
        provider = provider_by_symbol_key.get(symbol_key)
        stored_conids = stored_conids_by_ticker[ticker]
        resolution = None
        if symbol_key in duplicate_provider_symbol_keys:
            outcome = "unsafe_ambiguous_massive_symbol_notation"
        elif provider is None:
            if len(stored_conids) != 1 or stored_conids[0] not in definitions_by_conid:
                outcome = "unsafe_missing_massive_and_unique_ibkr_definition"
            else:
                matches, reason = published_contract_matches(ticker, definitions_by_conid[stored_conids[0]])
                outcome = "verified_ibkr_conid_massive_inactive" if matches else "unsafe_" + reason
        elif len(stored_conids) != 1:
            outcome = "unsafe_missing_or_multiple_published_conids"
        else:
            candidate_conids = candidate_conids_by_ticker[ticker]
            resolution = resolve_massive_ibkr_contract(
                massive_ticker=str(provider.get("ticker") or ticker),
                massive_name=str(provider.get("name") or ""),
                massive_exchange=str(provider.get("primary_exchange") or ""),
                definitions=[
                    definitions_by_conid[conid]
                    for conid in candidate_conids
                    if conid in definitions_by_conid
                ],
            )
            if not resolution.accepted:
                outcome = "unsafe_" + resolution.reason
            elif resolution.conid != stored_conids[0]:
                outcome = "unsafe_published_conid_mismatch"
            elif stored_conids[0] not in definitions_by_conid:
                outcome = "unsafe_published_conid_definition_missing"
            else:
                outcome = "verified_exact_ibkr_conid"
        outcomes[outcome] += 1
        record = {
            "ticker": ticker,
            "outcome": outcome,
            "massive": provider or {},
            "published": {
                "rows": len(current_rows),
                "conids": list(stored_conids),
                "listing_ids": sorted({str(row.get("listing_id") or "") for row in current_rows}),
                "symbol_ids": sorted({str(row.get("symbol_id") or "") for row in current_rows}),
                "exchange_codes": sorted({str(row.get("exchange_code") or "") for row in current_rows}),
            },
            "ibkr": asdict(resolution) if resolution is not None else None,
        }
        record["evidence_sha256"] = sha256_json(record)
        results.append(record)

    write_jsonl(rows_path, results)
    unsafe = sum(count for outcome, count in outcomes.items() if outcome.startswith("unsafe_"))
    quarantine: dict[str, Any] = {"status": "not_requested"}
    if args.quarantine_unsafe and unsafe:
        unsafe_results = [result for result in results if result["outcome"].startswith("unsafe_")]
        config = SimpleNamespace(
            clickhouse_url=default_clickhouse_url(),
            clickhouse_user=default_clickhouse_user(),
            clickhouse_read_database=args.database,
            clickhouse_write_database=args.database,
            execute=True,
            test_write_mode=False,
            rebuild_tradable_in_test_mode=False,
        )
        issues = [
            GraphWriteIssue(
                ticker=result["ticker"],
                issue_type="ibkr_conid_audit_" + result["outcome"].removeprefix("unsafe_"),
                message=(
                    f"Ticker {result['ticker']} is quarantined because its published IBKR conid did not pass "
                    f"the current broker-contract identity audit: {result['outcome']}."
                ),
                evidence=result,
            )
            for result in unsafe_results
        ]
        print(f"stage=unsafe_quarantine status=active tickers={len(issues):,}", flush=True)
        issue_result = write_graph_mapping_issues(config, issues)
        rebuild_result: dict[str, Any] = {"status": "not_requested"}
        if args.rebuild_publications:
            rebuild_result = asdict(
                rebuild_tradable_publications(
                    config,
                    reason="tradable_ibkr_conid_audit: " + args.maintenance_reason.strip(),
                )
            )
        quarantine = {
            "status": "completed",
            "maintenance_reason": args.maintenance_reason.strip(),
            "issues": asdict(issue_result),
            "publication_rebuild": rebuild_result,
        }
        print(f"stage=unsafe_quarantine status=completed written={issue_result.written:,}", flush=True)

    summary = {
        "contract_version": "tradable_ibkr_conid_audit_v1",
        "run_id": run_id,
        "checked_at_utc": checked_at.isoformat(),
        "database": args.database,
        "universe_date": universe_date,
        "tradable_rows": len(tradable_rows),
        "tradable_tickers": len(target_tickers),
        "verified_tickers": outcomes.get("verified_exact_ibkr_conid", 0),
        "unsafe_tickers": unsafe,
        "outcomes": dict(sorted(outcomes.items())),
        "massive_case_collisions": provider_case_conflicts,
        "ibkr_exchange_rows": exchange_row_counts,
        "ibkr_contract_definitions": len(definitions_by_conid),
        "quarantine": quarantine,
        "ticker_audit_path": str(rows_path),
    }
    summary["evidence_sha256"] = sha256_json(summary)
    write_json(summary_path, summary)
    print(
        "stage=ticker_validation status=completed "
        + " ".join(f"{key}={value:,}" for key, value in sorted(outcomes.items())),
        flush=True,
    )
    print(f"result={summary_path}", flush=True)
    if unsafe and not args.quarantine_unsafe:
        raise SystemExit(2)


def positive_stored_conids(rows: list[dict[str, Any]]) -> tuple[int, ...]:
    values: set[int] = set()
    for row in rows:
        try:
            conid = int(row.get("ibkr_conid") or 0)
        except (TypeError, ValueError):
            continue
        if conid > 0:
            values.add(conid)
    return tuple(sorted(values))


if __name__ == "__main__":
    main()
