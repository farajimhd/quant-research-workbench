from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.providers import IbkrReferenceClient, MassiveReferenceClient


@dataclass(frozen=True, slots=True)
class MissingTickerCandidate:
    ticker: str
    name: str
    market: str
    locale: str
    primary_exchange: str
    currency_symbol: str
    cik: str
    composite_figi: str
    share_class_figi: str
    ticker_type: str
    missing_reason: str
    overview: dict[str, Any] = field(default_factory=dict)
    ibkr_candidates: list[dict[str, Any]] = field(default_factory=list)
    proposed_action: str = "open_mapping_issue"


@dataclass(frozen=True, slots=True)
class ActiveTickerPlan:
    checked_at_utc: str
    provider_rows: int
    provider_pages: int
    provider_saturated: bool
    known_active_symbols: int
    missing_tickers: int
    overview_fetched: int
    ibkr_searched: int
    candidate_limit: int
    candidates: list[MissingTickerCandidate]
    wall_seconds: float

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_active_ticker_plan(
    config: ReferenceGatewayConfig,
    *,
    on_progress: Callable[[str, int | None], None] | None = None,
) -> ActiveTickerPlan:
    started = time.perf_counter()
    emit_progress(on_progress, "Fetching Massive active US stock ticker list.", None)
    massive = MassiveReferenceClient(
        base_url=config.massive_base_url,
        api_key=_massive_api_key(),
        page_limit=config.active_ticker_page_limit,
        max_pages=config.active_ticker_max_pages,
    )
    provider = massive.fetch_active_us_stock_tickers()
    emit_progress(on_progress, f"Massive returned {len(provider.tickers):,} active ticker row(s) across {provider.pages:,} page(s).", len(provider.tickers))
    emit_progress(on_progress, "Loading current canonical active symbols from ClickHouse.", None)
    current = load_current_active_symbols(config)
    known = {row["ticker"].upper() for row in current}
    missing = [normalize_massive_ticker(row) for row in provider.tickers]
    missing = [row for row in missing if row.get("ticker") and row["ticker"].upper() not in known]
    candidate_rows = missing[: config.active_ticker_new_candidate_limit]
    emit_progress(
        on_progress,
        f"Found {len(missing):,} provider ticker(s) not in canonical symbols; resolving first {len(candidate_rows):,}.",
        len(missing),
    )
    ibkr = IbkrReferenceClient(base_url=config.ibkr_base_url)
    candidates: list[MissingTickerCandidate] = []
    overview_fetched = 0
    ibkr_searched = 0
    for index, row in enumerate(candidate_rows, start=1):
        ticker = row["ticker"]
        overview: dict[str, Any] = {}
        ibkr_rows: list[dict[str, Any]] = []
        try:
            overview = compact_overview(massive.fetch_ticker_overview(ticker))
            overview_fetched += 1
        except Exception as exc:  # noqa: BLE001
            overview = {"error": repr(exc)}
        if ibkr is not None:
            try:
                ibkr_rows = compact_ibkr_candidates(ibkr.search_stock_contracts(ticker), ticker)
                ibkr_searched += 1
            except Exception as exc:  # noqa: BLE001
                ibkr_rows = [{"error": repr(exc)}]
        if index == 1 or index == len(candidate_rows) or index % 10 == 0:
            emit_progress(
                on_progress,
                f"Resolved {index:,}/{len(candidate_rows):,} new ticker candidate(s); latest={ticker}.",
                index,
            )
        candidates.append(
            MissingTickerCandidate(
                ticker=ticker,
                name=row.get("name", ""),
                market=row.get("market", ""),
                locale=row.get("locale", ""),
                primary_exchange=row.get("primary_exchange", ""),
                currency_symbol=row.get("currency_symbol", ""),
                cik=row.get("cik", ""),
                composite_figi=row.get("composite_figi", ""),
                share_class_figi=row.get("share_class_figi", ""),
                ticker_type=row.get("type", ""),
                missing_reason="massive_active_ticker_not_in_id_symbol_v1",
                overview=overview,
                ibkr_candidates=ibkr_rows,
                proposed_action=proposed_action(overview, ibkr_rows),
            )
        )
    return ActiveTickerPlan(
        checked_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        provider_rows=len(provider.tickers),
        provider_pages=provider.pages,
        provider_saturated=provider.saturated,
        known_active_symbols=len(known),
        missing_tickers=len(missing),
        overview_fetched=overview_fetched,
        ibkr_searched=ibkr_searched,
        candidate_limit=config.active_ticker_new_candidate_limit,
        candidates=candidates,
        wall_seconds=time.perf_counter() - started,
    )


def emit_progress(callback: Callable[[str, int | None], None] | None, message: str, rows: int | None) -> None:
    if callback is not None:
        callback(message, rows)


def write_active_ticker_plan(plan: ActiveTickerPlan, root: Path) -> Path:
    run_id = datetime.now(UTC).strftime("active_ticker_plan_%Y%m%d_%H%M%S")
    run_root = root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    path = run_root / "active_ticker_reconciliation.json"
    path.write_text(json.dumps(plan.public_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_current_active_symbols(config: ReferenceGatewayConfig) -> list[dict[str, str]]:
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, _clickhouse_password())
    rows = query_json_each_row(
        client,
        f"""
        SELECT
            upper(s.ticker) AS ticker,
            s.symbol_id AS symbol_id,
            l.listing_id AS listing_id,
            l.exchange_code AS exchange_code,
            l.currency_code AS currency_code,
            l.ibkr_conid AS ibkr_conid
        FROM {table(config.clickhouse_read_database, 'id_symbol_v1')} s FINAL
        INNER JOIN {table(config.clickhouse_read_database, 'id_listing_v1')} l FINAL ON l.listing_id = s.listing_id
        WHERE s.status = 'active'
          AND s.primary_symbol_flag = 1
          AND l.listing_status = 'active'
        """,
    )
    return [{key: str(value or "") for key, value in row.items()} for row in rows]


def normalize_massive_ticker(row: dict[str, Any]) -> dict[str, str]:
    return {
        "ticker": str(row.get("ticker") or "").strip().upper(),
        "name": str(row.get("name") or "").strip(),
        "market": str(row.get("market") or "").strip(),
        "locale": str(row.get("locale") or "").strip(),
        "primary_exchange": str(row.get("primary_exchange") or "").strip(),
        "currency_symbol": str(row.get("currency_symbol") or "").strip().upper(),
        "cik": normalize_cik(row.get("cik")),
        "composite_figi": str(row.get("composite_figi") or "").strip(),
        "share_class_figi": str(row.get("share_class_figi") or "").strip(),
        "type": str(row.get("type") or "").strip(),
    }


def compact_overview(row: dict[str, Any]) -> dict[str, Any]:
    branding = row.get("branding") if isinstance(row.get("branding"), dict) else {}
    return {
        "ticker": str(row.get("ticker") or ""),
        "name": str(row.get("name") or ""),
        "active": bool(row.get("active")) if row.get("active") is not None else None,
        "market": str(row.get("market") or ""),
        "locale": str(row.get("locale") or ""),
        "primary_exchange": str(row.get("primary_exchange") or ""),
        "currency_name": str(row.get("currency_name") or ""),
        "cik": normalize_cik(row.get("cik")),
        "composite_figi": str(row.get("composite_figi") or ""),
        "share_class_figi": str(row.get("share_class_figi") or ""),
        "sic_code": str(row.get("sic_code") or ""),
        "sic_description": str(row.get("sic_description") or ""),
        "homepage_url_present": bool(row.get("homepage_url")),
        "logo_url_present": bool(branding.get("logo_url")),
        "icon_url_present": bool(branding.get("icon_url")),
        "list_date": str(row.get("list_date") or ""),
        "market_cap": row.get("market_cap"),
        "weighted_shares_outstanding": row.get("weighted_shares_outstanding"),
        "share_class_shares_outstanding": row.get("share_class_shares_outstanding"),
    }


def compact_ibkr_candidates(rows: list[dict[str, Any]], ticker: str) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    ticker_upper = ticker.upper()
    for row in rows[:25]:
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        compact.append(
            {
                "symbol": symbol,
                "conid": str(row.get("conid") or row.get("con_id") or ""),
                "sec_type": str(row.get("secType") or row.get("assetClass") or ""),
                "exchange": str(row.get("exchange") or ""),
                "listing_exchange": str(row.get("listingExchange") or ""),
                "currency": str(row.get("currency") or ""),
                "company_name": str(row.get("companyName") or row.get("description") or ""),
                "exact_symbol": symbol == ticker_upper if symbol else None,
            }
        )
    return compact


def proposed_action(overview: dict[str, Any], ibkr_rows: list[dict[str, Any]]) -> str:
    if overview.get("error"):
        return "open_mapping_issue_missing_massive_overview"
    if ibkr_rows and any("error" in row for row in ibkr_rows):
        return "open_mapping_issue_ibkr_lookup_failed"
    exact_ibkr = [row for row in ibkr_rows if row.get("exact_symbol") and str(row.get("conid") or "").isdigit()]
    if ibkr_rows and len(exact_ibkr) == 1:
        return "candidate_ready_for_dry_run_graph_resolution"
    if ibkr_rows and len(exact_ibkr) > 1:
        return "open_mapping_issue_ambiguous_ibkr_contract"
    return "open_mapping_issue_missing_unique_ibkr_conid"


def normalize_cik(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(10) if digits else ""


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.rstrip(";") + " FORMAT JSONEachRow").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def _massive_api_key() -> str:
    import os

    return os.environ.get("MASSIVE_API_KEY", "").strip()


def _clickhouse_password() -> str:
    from research.mlops.clickhouse import default_clickhouse_password

    return default_clickhouse_password()
