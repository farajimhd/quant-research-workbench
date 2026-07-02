from __future__ import annotations

import argparse
import calendar
import hashlib
import io
import json
import os
import re
import ssl
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from services.reference_gateway.market_publications import (  # noqa: E402
    ensure_market_publication_schema,
    find_publication_gaps,
    insert_publication_coverage,
    table_exists,
)


FINRA_SHORT_VOLUME_SOURCES = {
    "CNMS": "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt",
    "FNSQ": "https://cdn.finra.org/equity/regsho/daily/FNSQshvol{yyyymmdd}.txt",
    "FNYX": "https://cdn.finra.org/equity/regsho/daily/FNYXshvol{yyyymmdd}.txt",
    "FNQC": "https://cdn.finra.org/equity/regsho/daily/FNQCshvol{yyyymmdd}.txt",
    "FORF": "https://cdn.finra.org/equity/regsho/daily/FORFshvol{yyyymmdd}.txt",
    "FADF": "https://cdn.finra.org/equity/regsho/daily/FADFshvol{yyyymmdd}.txt",
}
SEC_FTD_PAGE = "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data"
SEC_FTD_FILE_URL = "https://www.sec.gov/files/data/fails-deliver-data/cnsfails{yyyymm}{half}.zip"
DEFAULT_USER_AGENT = "quant-reference-gateway/1.0 contact: local-research"
RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}
SEC_RATE_LIMIT_TEXT = "Request Rate Threshold Exceeded"
MASSIVE_SOURCE_SPECS = {
    "massive_splits": {
        "endpoint": "/stocks/v1/splits",
        "date_field": "execution_date",
        "coverage_kind": "massive_splits",
        "source_object": "splits",
        "target_table": "market_stock_split_v1",
        "sort": "execution_date.asc",
    },
    "massive_dividends": {
        "endpoint": "/stocks/v1/dividends",
        "date_field": "ex_dividend_date",
        "coverage_kind": "massive_dividends",
        "source_object": "dividends",
        "target_table": "market_cash_dividend_v1",
        "sort": "ex_dividend_date.asc",
    },
    "massive_ipos": {
        "endpoint": "/vX/reference/ipos",
        "date_field": "listing_date",
        "coverage_kind": "massive_ipos",
        "source_object": "ipos",
        "target_table": "market_ipo_v1",
        "sort": "listing_date.asc",
    },
}
IMPLEMENTED_SOURCES = {
    "finra_short_volume",
    "sec_fails_to_deliver",
    "massive_splits",
    "massive_dividends",
    "massive_ipos",
    "massive_ticker_details",
    "ibkr_borrow_availability",
}
WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class SymbolRef:
    symbol_id: str
    listing_id: str
    security_id: str
    ticker: str
    ibkr_conid: str = ""


class MassiveTickerNotFound(Exception):
    def __init__(self, ticker: str, url: str) -> None:
        super().__init__(f"Massive ticker detail not found for {ticker}: {url}")
        self.ticker = ticker
        self.url = url


@dataclass(frozen=True, slots=True)
class SourceResult:
    source: str
    coverage_kind: str
    start_date: date
    end_date: date
    rows_read: int
    rows_written: int
    rows_failed: int
    status: str
    details: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Historical and gap-fill loader for reference market publications. "
            "It uses the same coverage-table model as the gateways: detect uncovered "
            "source windows, fetch normalized rows, write rows, then write coverage."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive YYYY-MM-DD.")
    parser.add_argument("--database", default="", help="Backward-compatible alias that sets both read and write databases when the split flags are omitted.")
    parser.add_argument("--read-database", default="", help="Canonical source database for symbols/listings. Defaults to q_live.")
    parser.add_argument("--write-database", default="", help="Target database for rows and coverage. Set this to a temp DB for full tests.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "")
    parser.add_argument("--output-root-win", default=os.environ.get("REFERENCE_PUBLICATION_OUTPUT_ROOT_WIN") or str(default_output_root()))
    parser.add_argument(
        "--sources",
        default="finra_short_volume,sec_fails_to_deliver,massive_splits,massive_dividends,massive_ipos,massive_ticker_details,ibkr_borrow_availability",
        help=(
            "Comma separated source list. Implemented: finra_short_volume, sec_fails_to_deliver, "
            "massive_splits, massive_dividends, massive_ipos, massive_ticker_details, ibkr_borrow_availability."
        ),
    )
    parser.add_argument("--finra-venues", default="CNMS", help="Comma separated FINRA short-volume source files. Default CNMS consolidated NMS.")
    parser.add_argument("--resume-from-coverage", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true", help="Write rows and coverage. Without this, only reports planned gaps.")
    parser.add_argument("--request-timeout-seconds", type=int, default=60)
    parser.add_argument("--request-min-interval-seconds", type=float, default=0.12)
    parser.add_argument("--request-max-retries", type=int, default=5)
    parser.add_argument("--request-retry-base-seconds", type=float, default=2.0)
    parser.add_argument("--request-retry-max-seconds", type=float, default=120.0)
    parser.add_argument(
        "--sec-ftd-link-mode",
        choices=["direct", "html", "auto"],
        default="direct",
        help=(
            "direct generates SEC FTD zip URLs from the requested dates. "
            "html scrapes the SEC landing page. auto tries html and falls back to direct."
        ),
    )
    parser.add_argument(
        "--user-agent",
        default=default_user_agent(),
        help="HTTP user agent. SEC requests should use a compliant contact-bearing SEC_USER_AGENT value.",
    )
    parser.add_argument("--batch-size", type=int, default=50_000)
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    legacy_database = os.environ.get("REFERENCE_GATEWAY_CLICKHOUSE_DATABASE") or "q_live"
    if not args.read_database:
        args.read_database = args.database or os.environ.get("REFERENCE_CLICKHOUSE_READ_DATABASE") or os.environ.get("REFERENCE_GATEWAY_READ_DATABASE") or legacy_database
    if not args.write_database:
        args.write_database = args.database or os.environ.get("REFERENCE_CLICKHOUSE_WRITE_DATABASE") or os.environ.get("REFERENCE_GATEWAY_WRITE_DATABASE") or legacy_database
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if end_date <= start_date:
        raise SystemExit("--end-date must be after --start-date")
    sources = parse_csv(args.sources)
    unsupported_sources = sorted(set(sources) - IMPLEMENTED_SOURCES)
    if unsupported_sources:
        raise SystemExit(
            "Unsupported reference publication source(s): "
            + ", ".join(unsupported_sources)
            + ". Implemented sources are: "
            + ", ".join(sorted(IMPLEMENTED_SOURCES))
        )
    run_id = f"reference_publications_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    if args.execute:
        ensure_market_publication_schema(
            client,
            database=args.write_database,
            read_database=args.read_database,
            storage_policy=args.storage_policy,
        )
    elif not table_exists(client, args.write_database, "market_reference_publication_coverage_v1"):
        print_header(args, run_id, run_root, loaded_env, symbols=0)
        results = schema_missing_results(args, sources, start_date, end_date)
        write_summary(run_root, args, run_id, results)
        print_summary(results, run_root)
        return
    symbols = load_symbol_refs(client, args.read_database)
    print_header(args, run_id, run_root, loaded_env, len(symbols))
    results: list[SourceResult] = []
    if "finra_short_volume" in sources:
        for venue in parse_csv(args.finra_venues):
            if venue not in FINRA_SHORT_VOLUME_SOURCES:
                raise SystemExit(f"Unsupported FINRA venue {venue}; expected one of {sorted(FINRA_SHORT_VOLUME_SOURCES)}")
            results.extend(
                run_date_source(
                    client=client,
                    args=args,
                    database=args.write_database,
                    run_id=run_id,
                    source_system="finra",
                    source_object=f"daily_short_volume:{venue}",
                    coverage_kind=f"finra_short_volume:{venue}",
                    start_date=start_date,
                    end_date=end_date,
                    worker=lambda day, venue=venue: fetch_finra_short_volume_day(args, day, venue, symbols),
                )
            )
    if "sec_fails_to_deliver" in sources:
        results.extend(run_sec_ftd(client, args, run_id, start_date, end_date, symbols))
    for source in ("massive_splits", "massive_dividends", "massive_ipos"):
        if source in sources:
            results.extend(run_massive_date_source(client, args, run_id, source, start_date, end_date, symbols))
    if "massive_ticker_details" in sources:
        results.extend(run_massive_ticker_details(client, args, run_id, start_date, end_date, symbols))
    if "ibkr_borrow_availability" in sources:
        results.extend(run_ibkr_borrow_availability(client, args, run_id, start_date, end_date, symbols))
    write_summary(run_root, args, run_id, results)
    print_summary(results, run_root)


def run_date_source(
    *,
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    database: str,
    run_id: str,
    source_system: str,
    source_object: str,
    coverage_kind: str,
    start_date: date,
    end_date: date,
    worker: Any,
) -> list[SourceResult]:
    gaps = [PublicationDateGap(start_date, end_date)]
    if args.resume_from_coverage:
        gaps = [
            PublicationDateGap(gap.start_date, gap.end_date)
            for gap in find_publication_gaps(
                client,
                database=database,
                coverage_kind=coverage_kind,
                source_system=source_system,
                start_date=start_date,
                end_date=end_date,
            )
        ]
    results: list[SourceResult] = []
    for gap in gaps:
        day = gap.start_date
        while day < gap.end_date:
            if day.weekday() >= 5 or is_us_equity_market_holiday(day):
                started = datetime.now(UTC)
                finished = datetime.now(UTC)
                status = "covered_empty"
                reason = "non_publication_day_weekend" if day.weekday() >= 5 else "non_publication_day_market_holiday"
                details = {"reason": reason, "target_table": "market_short_volume_v1"}
                result = SourceResult(source_object, coverage_kind, day, day + timedelta(days=1), 0, 0, 0, status, details)
                results.append(result)
                print(f"{coverage_kind} {day.isoformat()} status={status} rows=0 written=0 reason={reason}", flush=True)
                if args.execute:
                    insert_publication_coverage(
                        client,
                        database=database,
                        coverage_id=f"{run_id}:{coverage_kind}:{day.isoformat()}",
                        coverage_kind=coverage_kind,
                        source_system=source_system,
                        source_object=source_object,
                        start_date=day,
                        end_date=day + timedelta(days=1),
                        status=status,
                        rows_read=0,
                        rows_written=0,
                        rows_failed=0,
                        started_at_utc=started,
                        finished_at_utc=finished,
                        details=details,
                        source_run_id=run_id,
                    )
                day += timedelta(days=1)
                continue
            started = datetime.now(UTC)
            try:
                rows, details = worker(day)
                stamp_rows(rows, run_id)
                rows_written = insert_rows(client, database, details["target_table"], rows, args.batch_size) if args.execute else 0
                status = "completed" if rows else "covered_empty"
                rows_failed = 0
            except FileNotFoundError as exc:
                rows = []
                rows_written = 0
                rows_failed = 0
                status = "covered_empty"
                details = {"message": str(exc), "target_table": "market_short_volume_v1", "missing_file": True}
            except Exception as exc:  # noqa: BLE001
                rows = []
                rows_written = 0
                rows_failed = 1
                status = "failed"
                details = {"error": repr(exc), "target_table": "market_short_volume_v1"}
            finished = datetime.now(UTC)
            result = SourceResult(
                source=source_object,
                coverage_kind=coverage_kind,
                start_date=day,
                end_date=day + timedelta(days=1),
                rows_read=len(rows),
                rows_written=rows_written,
                rows_failed=rows_failed,
                status=status,
                details=details,
            )
            results.append(result)
            print(
                f"{coverage_kind} {day.isoformat()} status={status} rows={len(rows):,} written={rows_written:,}",
                flush=True,
            )
            if args.execute:
                insert_publication_coverage(
                    client,
                    database=database,
                    coverage_id=f"{run_id}:{coverage_kind}:{day.isoformat()}",
                    coverage_kind=coverage_kind,
                    source_system=source_system,
                    source_object=source_object,
                    start_date=day,
                    end_date=day + timedelta(days=1),
                    status=status,
                    rows_read=len(rows),
                    rows_written=rows_written,
                    rows_failed=rows_failed,
                    started_at_utc=started,
                    finished_at_utc=finished,
                    details=details,
                    source_run_id=run_id,
                )
            if args.request_min_interval_seconds > 0:
                time.sleep(args.request_min_interval_seconds)
            day += timedelta(days=1)
    return results


@dataclass(frozen=True, slots=True)
class PublicationDateGap:
    start_date: date
    end_date: date


def fetch_finra_short_volume_day(
    args: argparse.Namespace,
    day: date,
    venue: str,
    symbols: dict[str, SymbolRef],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    yyyymmdd = day.strftime("%Y%m%d")
    url = FINRA_SHORT_VOLUME_SOURCES[venue].format(yyyymmdd=yyyymmdd)
    content = fetch_bytes(args, url)
    text = content.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) == 1:
        return [], {"target_table": "market_short_volume_v1", "url": url, "venue": venue, "sha256": sha256_bytes(content)}
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        trade_date = parse_finra_date(parts[0], day)
        ticker = parts[1].strip().upper()
        short_volume = parse_int(parts[2])
        short_exempt = parse_int(parts[3])
        total_volume = parse_int(parts[4])
        ref = symbols.get(ticker)
        event_key = f"{venue}:{trade_date.isoformat()}:{ticker}"
        rows.append(
            {
                "short_volume_id": stable_id("short_volume", event_key),
                "symbol_id": ref.symbol_id if ref else "",
                "listing_id": ref.listing_id if ref else "",
                "security_id": ref.security_id if ref else "",
                "source_system": "finra",
                "source_venue": venue,
                "provider_ticker": ticker,
                "trade_date": trade_date.isoformat(),
                "published_at_utc": None,
                "short_volume": short_volume,
                "short_volume_ratio": (short_volume / total_volume) if short_volume is not None and total_volume else None,
                "total_volume": total_volume,
                "exempt_volume": short_exempt,
                "non_exempt_volume": short_volume - short_exempt if short_volume is not None and short_exempt is not None else None,
                "source_event_key": event_key,
                "source_evidence_ref": url,
                "source_run_id": "",
                "source_content_sha256": sha256_bytes(content),
                "inserted_at": clickhouse_now64(),
            }
        )
    return rows, {"target_table": "market_short_volume_v1", "url": url, "venue": venue, "sha256": sha256_bytes(content)}


def run_sec_ftd(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    start_date: date,
    end_date: date,
    symbols: dict[str, SymbolRef],
) -> list[SourceResult]:
    gaps = find_publication_gaps(
        client,
        database=args.write_database,
        coverage_kind="sec_fails_to_deliver",
        source_system="sec",
        start_date=start_date,
        end_date=end_date,
    ) if args.resume_from_coverage else []
    if not args.resume_from_coverage:
        gaps = [PublicationDateGap(start_date, end_date)]  # type: ignore[list-item]
    links = fetch_sec_ftd_links(args, start_date, end_date)
    latest_published_end = max((item["end_date"] for item in links), default=None)
    results: list[SourceResult] = []
    for gap in gaps:
        gap_start = gap.start_date
        gap_end = gap.end_date
        selected = [item for item in links if not (item["end_date"] <= gap_start or item["start_date"] >= gap_end)]
        if not selected:
            started = datetime.now(UTC)
            finished = datetime.now(UTC)
            published_window = latest_published_end is not None and gap_end <= latest_published_end
            status = "covered_empty" if published_window else "source_not_yet_available"
            details = {
                "reason": "no_sec_file_for_window" if published_window else "sec_file_not_published_yet",
                "latest_published_end": latest_published_end.isoformat() if latest_published_end else "",
            }
            results.append(SourceResult("sec_fails_to_deliver", "sec_fails_to_deliver", gap_start, gap_end, 0, 0, 0, status, details))
            print(f"sec_fails_to_deliver {gap_start.isoformat()}->{gap_end.isoformat()} status={status} rows=0 written=0", flush=True)
            if args.execute and status == "covered_empty":
                insert_publication_coverage(
                    client,
                    database=args.write_database,
                    coverage_id=f"{run_id}:sec_fails_to_deliver:{gap_start.isoformat()}:{gap_end.isoformat()}",
                    coverage_kind="sec_fails_to_deliver",
                    source_system="sec",
                    source_object="fails_to_deliver",
                    start_date=gap_start,
                    end_date=gap_end,
                    status=status,
                    rows_read=0,
                    rows_written=0,
                    rows_failed=0,
                    started_at_utc=started,
                    finished_at_utc=finished,
                    details=details,
                    source_run_id=run_id,
                )
            continue
        for item in selected:
            started = datetime.now(UTC)
            try:
                rows, details = fetch_sec_ftd_file(args, item, gap_start, gap_end, symbols)
                stamp_rows(rows, run_id)
                written = insert_rows(client, args.write_database, "market_fails_to_deliver_v1", rows, args.batch_size) if args.execute else 0
                status = "completed" if rows else "covered_empty"
                failed = 0
            except FileNotFoundError as exc:
                rows = []
                written = 0
                failed = 0
                expected_after = sec_ftd_expected_publish_after(item)
                if datetime.now(UTC).date() <= expected_after:
                    status = "source_not_yet_available"
                    details = {
                        "message": str(exc),
                        "url": item.get("url"),
                        "expected_after": expected_after.isoformat(),
                    }
                else:
                    failed = 1
                    status = "failed"
                    details = {
                        "error": repr(exc),
                        "url": item.get("url"),
                        "expected_after": expected_after.isoformat(),
                    }
            except Exception as exc:  # noqa: BLE001
                rows = []
                written = 0
                failed = 1
                status = "failed"
                details = {"error": repr(exc), "url": item.get("url")}
            finished = datetime.now(UTC)
            cov_start = max(gap_start, item["start_date"])
            cov_end = min(gap_end, item["end_date"])
            result = SourceResult("sec_fails_to_deliver", "sec_fails_to_deliver", cov_start, cov_end, len(rows), written, failed, status, details)
            results.append(result)
            print(f"sec_fails_to_deliver {cov_start.isoformat()}->{cov_end.isoformat()} status={status} rows={len(rows):,} written={written:,}", flush=True)
            if args.execute and status != "source_not_yet_available":
                insert_publication_coverage(
                    client,
                    database=args.write_database,
                    coverage_id=f"{run_id}:sec_fails_to_deliver:{cov_start.isoformat()}:{cov_end.isoformat()}",
                    coverage_kind="sec_fails_to_deliver",
                    source_system="sec",
                    source_object="fails_to_deliver",
                    start_date=cov_start,
                    end_date=cov_end,
                    status=status,
                    rows_read=len(rows),
                    rows_written=written,
                    rows_failed=failed,
                    started_at_utc=started,
                    finished_at_utc=finished,
                    details=details,
                    source_run_id=run_id,
                )
    return results


def fetch_sec_ftd_links(args: argparse.Namespace, start_date: date, end_date: date) -> list[dict[str, Any]]:
    if args.sec_ftd_link_mode == "direct":
        return generate_sec_ftd_links(start_date, end_date)
    if args.sec_ftd_link_mode == "auto":
        try:
            return fetch_sec_ftd_links_from_page(args)
        except Exception as exc:  # noqa: BLE001
            print(
                "sec_fails_to_deliver link_discovery=html_failed "
                f"fallback=direct error={repr(exc)}",
                flush=True,
            )
            return generate_sec_ftd_links(start_date, end_date)
    return fetch_sec_ftd_links_from_page(args)


def fetch_sec_ftd_links_from_page(args: argparse.Namespace) -> list[dict[str, Any]]:
    html = fetch_bytes(args, SEC_FTD_PAGE).decode("utf-8", errors="replace")
    links: list[dict[str, Any]] = []
    for href in re.findall(r"href=[\"']([^\"']+\.zip)[\"']", html, flags=re.IGNORECASE):
        url = parse.urljoin(SEC_FTD_PAGE, href)
        match = re.search(r"cnsfails(\d{6})([ab])", url, flags=re.IGNORECASE)
        if not match:
            continue
        year = int(match.group(1)[:4])
        month = int(match.group(1)[4:6])
        half = match.group(2).lower()
        start = date(year, month, 1 if half == "a" else 16)
        if half == "a":
            end = date(year, month, 16)
        else:
            end = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
        links.append({"url": url, "start_date": start, "end_date": end})
    return links


def generate_sec_ftd_links(start_date: date, end_date: date) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    cursor = date(start_date.year, start_date.month, 1)
    while cursor < end_date:
        year = cursor.year
        month = cursor.month
        first = date(year, month, 1)
        sixteenth = date(year, month, 16)
        month_end = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
        for half, half_start, half_end in (("a", first, sixteenth), ("b", sixteenth, month_end)):
            if half_end > start_date and half_start < end_date:
                links.append(
                    {
                        "url": SEC_FTD_FILE_URL.format(yyyymm=f"{year}{month:02d}", half=half),
                        "start_date": half_start,
                        "end_date": half_end,
                        "link_mode": "direct",
                    }
                )
        cursor = month_end
    return links


def sec_ftd_expected_publish_after(item: dict[str, Any]) -> date:
    start = item["start_date"]
    end = item["end_date"]
    if not isinstance(start, date) or not isinstance(end, date):
        return datetime.now(UTC).date()
    if start.day == 1 and end.day == 16:
        return end_of_month(start)
    return date(end.year, end.month, min(15, calendar.monthrange(end.year, end.month)[1]))


def end_of_month(day: date) -> date:
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def fetch_sec_ftd_file(
    args: argparse.Namespace,
    item: dict[str, Any],
    start_date: date,
    end_date: date,
    symbols: dict[str, SymbolRef],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    content = fetch_bytes(args, str(item["url"]))
    digest = sha256_bytes(content)
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for member in archive.namelist():
            if not member.lower().endswith(".txt"):
                continue
            text = archive.read(member).decode("utf-8", errors="replace")
            for line in text.splitlines()[1:]:
                parts = line.split("|")
                if len(parts) < 6:
                    continue
                settlement = parse_yyyymmdd(parts[0])
                if settlement < start_date or settlement >= end_date:
                    continue
                ticker = parts[2].strip().upper()
                ref = symbols.get(ticker)
                event_key = f"{settlement.isoformat()}:{parts[1].strip()}:{ticker}"
                rows.append(
                    {
                        "ftd_id": stable_id("ftd", event_key),
                        "symbol_id": ref.symbol_id if ref else None,
                        "listing_id": ref.listing_id if ref else None,
                        "security_id": ref.security_id if ref else None,
                        "source_system": "sec",
                        "provider_ticker": ticker,
                        "settlement_date": settlement.isoformat(),
                        "cusip": parts[1].strip() or None,
                        "fails_quantity": parse_int(parts[3]) or 0,
                        "issuer_name": parts[4].strip() or None,
                        "previous_close_price": parse_float(parts[5]),
                        "source_event_key": event_key,
                        "source_evidence_ref": str(item["url"]),
                        "source_run_id": "",
                        "source_content_sha256": digest,
                        "inserted_at": clickhouse_now64(),
                    }
                )
    return rows, {"url": item["url"], "sha256": digest, "target_table": "market_fails_to_deliver_v1"}


def run_massive_date_source(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    source: str,
    start_date: date,
    end_date: date,
    symbols: dict[str, SymbolRef],
) -> list[SourceResult]:
    spec = MASSIVE_SOURCE_SPECS[source]
    coverage_kind = str(spec["coverage_kind"])
    gaps = find_publication_gaps(
        client,
        database=args.write_database,
        coverage_kind=coverage_kind,
        source_system="massive",
        start_date=start_date,
        end_date=end_date,
    ) if args.resume_from_coverage else [PublicationDateGap(start_date, end_date)]  # type: ignore[list-item]
    results: list[SourceResult] = []
    for gap in gaps:
        started = datetime.now(UTC)
        try:
            provider_rows, details = fetch_massive_date_rows(args, spec, gap.start_date, gap.end_date)
            rows = normalize_massive_rows(source, provider_rows, details, symbols)
            stamp_rows(rows, run_id)
            written = insert_rows(client, args.write_database, str(spec["target_table"]), rows, args.batch_size) if args.execute else 0
            status = "completed" if rows else "covered_empty"
            failed = 0
        except Exception as exc:  # noqa: BLE001
            rows = []
            provider_rows = []
            written = 0
            failed = 1
            status = "failed"
            details = {"error": repr(exc), "target_table": spec["target_table"], "source": source}
        finished = datetime.now(UTC)
        result = SourceResult(source, coverage_kind, gap.start_date, gap.end_date, len(provider_rows), written, failed, status, details)
        results.append(result)
        print(
            f"{coverage_kind} {gap.start_date.isoformat()}->{gap.end_date.isoformat()} "
            f"status={status} provider_rows={len(provider_rows):,} written={written:,}",
            flush=True,
        )
        if args.execute:
            insert_publication_coverage(
                client,
                database=args.write_database,
                coverage_id=f"{run_id}:{coverage_kind}:{gap.start_date.isoformat()}:{gap.end_date.isoformat()}",
                coverage_kind=coverage_kind,
                source_system="massive",
                source_object=str(spec["source_object"]),
                start_date=gap.start_date,
                end_date=gap.end_date,
                status=status,
                rows_read=len(provider_rows),
                rows_written=written,
                rows_failed=failed,
                started_at_utc=started,
                finished_at_utc=finished,
                details=details,
                source_run_id=run_id,
            )
    return results


def fetch_massive_date_rows(args: argparse.Namespace, spec: dict[str, str], start_date: date, end_date: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    api_key = massive_api_key()
    date_field = spec["date_field"]
    params = {
        f"{date_field}.gte": start_date.isoformat(),
        f"{date_field}.lt": end_date.isoformat(),
        "limit": "5000",
        "sort": spec["sort"],
        "apiKey": api_key,
    }
    url = "https://api.massive.com" + spec["endpoint"] + "?" + parse.urlencode(params)
    rows, pages, saturated = fetch_massive_paginated(args, url, api_key)
    digest = sha256_bytes(json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
    return rows, {
        "url": safe_url(url),
        "sha256": digest,
        "pages": pages,
        "saturated": saturated,
        "target_table": spec["target_table"],
    }


def normalize_massive_rows(
    source: str,
    provider_rows: list[dict[str, Any]],
    details: dict[str, Any],
    symbols: dict[str, SymbolRef],
) -> list[dict[str, Any]]:
    if source == "massive_splits":
        return [normalize_massive_split(row, details, symbols) for row in provider_rows if parse_optional_date(row.get("execution_date")) is not None]
    if source == "massive_dividends":
        return [normalize_massive_dividend(row, details, symbols) for row in provider_rows if parse_optional_date(row.get("ex_dividend_date")) is not None]
    if source == "massive_ipos":
        return [normalize_massive_ipo(row, details, symbols) for row in provider_rows if parse_optional_date(row.get("listing_date")) is not None]
    raise ValueError(f"unsupported massive source: {source}")


def normalize_massive_split(row: dict[str, Any], details: dict[str, Any], symbols: dict[str, SymbolRef]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").strip().upper()
    execution_date = parse_optional_date(row.get("execution_date"))
    ref = symbols.get(ticker)
    provider_id = str(row.get("id") or "")
    event_key = provider_id or f"{ticker}:{execution_date}:{row.get('split_from')}:{row.get('split_to')}"
    return {
        "stock_split_id": stable_id("massive_split", event_key),
        "symbol_id": ref.symbol_id if ref else "",
        "listing_id": ref.listing_id if ref else "",
        "security_id": ref.security_id if ref else "",
        "source_system": "massive",
        "provider_ticker": ticker,
        "execution_date": execution_date.isoformat() if execution_date else None,
        "split_from": parse_float(row.get("split_from")) or 0.0,
        "split_to": parse_float(row.get("split_to")) or 0.0,
        "source_event_key": event_key,
        "source_evidence_ref": details.get("url", ""),
        "source_run_id": "",
        "source_content_sha256": row_sha256(row),
        "inserted_at": clickhouse_now64(),
    }


def normalize_massive_dividend(row: dict[str, Any], details: dict[str, Any], symbols: dict[str, SymbolRef]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").strip().upper()
    ex_date = parse_optional_date(row.get("ex_dividend_date"))
    ref = symbols.get(ticker)
    provider_id = str(row.get("id") or "")
    event_key = provider_id or f"{ticker}:{ex_date}:{row.get('cash_amount')}:{row.get('currency')}"
    return {
        "cash_dividend_id": stable_id("massive_dividend", event_key),
        "symbol_id": ref.symbol_id if ref else "",
        "listing_id": ref.listing_id if ref else "",
        "security_id": ref.security_id if ref else "",
        "source_system": "massive",
        "provider_ticker": ticker,
        "cash_amount": parse_float(row.get("cash_amount")),
        "currency_code": string_or_none(row.get("currency") or row.get("currency_code")),
        "declaration_date": date_string_or_none(row.get("declaration_date")),
        "dividend_type": string_or_none(row.get("distribution_type")),
        "ex_dividend_date": ex_date.isoformat() if ex_date else None,
        "frequency": string_or_none(row.get("frequency")),
        "pay_date": date_string_or_none(row.get("pay_date")),
        "record_date": date_string_or_none(row.get("record_date")),
        "source_event_key": event_key,
        "source_evidence_ref": details.get("url", ""),
        "source_run_id": "",
        "source_content_sha256": row_sha256(row),
        "inserted_at": clickhouse_now64(),
    }


def normalize_massive_ipo(row: dict[str, Any], details: dict[str, Any], symbols: dict[str, SymbolRef]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "").strip().upper()
    listing_date = parse_optional_date(row.get("listing_date"))
    ref = symbols.get(ticker)
    event_key = str(row.get("id") or "") or f"{ticker}:{listing_date}:{row.get('issuer_name')}:{row.get('last_updated')}"
    return {
        "ipo_event_id": stable_id("massive_ipo", event_key),
        "symbol_id": ref.symbol_id if ref else "",
        "listing_id": ref.listing_id if ref else "",
        "security_id": ref.security_id if ref else "",
        "source_system": "massive",
        "provider_ticker": ticker,
        "issuer_name": string_or_none(row.get("issuer_name")),
        "announced_date": date_string_or_none(row.get("announced_date")),
        "listing_date": listing_date.isoformat() if listing_date else None,
        "issue_start_date": date_string_or_none(row.get("issue_start_date")),
        "issue_end_date": date_string_or_none(row.get("issue_end_date")),
        "last_updated_date": date_string_or_none(row.get("last_updated")),
        "ipo_status": string_or_none(row.get("ipo_status")),
        "currency_code": string_or_none(row.get("currency_code")),
        "final_issue_price": parse_float(row.get("final_issue_price")),
        "highest_offer_price": parse_float(row.get("highest_offer_price")),
        "lowest_offer_price": parse_float(row.get("lowest_offer_price")),
        "min_shares_offered": parse_float(row.get("min_shares_offered")),
        "max_shares_offered": parse_float(row.get("max_shares_offered")),
        "total_offer_size": parse_float(row.get("total_offer_size")),
        "shares_outstanding": parse_float(row.get("shares_outstanding")),
        "primary_exchange": string_or_none(row.get("primary_exchange")),
        "security_type": string_or_none(row.get("security_type")),
        "security_description": string_or_none(row.get("security_description")),
        "us_code": string_or_none(row.get("us_code")),
        "isin": string_or_none(row.get("isin")),
        "source_event_key": event_key,
        "source_evidence_ref": details.get("url", ""),
        "source_run_id": "",
        "source_content_sha256": row_sha256(row),
        "inserted_at": clickhouse_now64(),
    }


def run_massive_ticker_details(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    start_date: date,
    end_date: date,
    symbols: dict[str, SymbolRef],
) -> list[SourceResult]:
    # Ticker details is a current-state endpoint. It can refresh today's snapshot,
    # but it cannot reconstruct historical daily snapshots for old gaps.
    today = datetime.now(UTC).date()
    target_start = max(start_date, today)
    target_end = min(end_date, today + timedelta(days=1))
    if target_start >= target_end:
        return [
            SourceResult(
                "massive_ticker_details",
                "massive_ticker_details",
                start_date,
                end_date,
                0,
                0,
                0,
                "source_not_historical",
                {"message": "Massive ticker details is current-state only; historical snapshots require archived source rows."},
            )
        ]
    gaps = find_publication_gaps(
        client,
        database=args.write_database,
        coverage_kind="massive_ticker_details",
        source_system="massive",
        start_date=target_start,
        end_date=target_end,
    ) if args.resume_from_coverage else [PublicationDateGap(target_start, target_end)]  # type: ignore[list-item]
    if not gaps:
        return []
    started = datetime.now(UTC)
    snapshot_rows: list[dict[str, Any]] = []
    float_rows: list[dict[str, Any]] = []
    failed = 0
    not_found = 0
    observed_at = datetime.now(UTC)
    print(
        "massive_ticker_details api_key_diagnostic="
        + json.dumps(massive_api_key_diagnostic(), sort_keys=True),
        flush=True,
    )
    for index, ref in enumerate(symbols.values(), start=1):
        try:
            overview = fetch_massive_ticker_overview(args, ref.ticker)
            if not overview:
                not_found += 1
                continue
            snapshot, float_row = normalize_massive_ticker_detail(overview, ref, observed_at)
            snapshot_rows.append(snapshot)
            if float_row is not None:
                float_rows.append(float_row)
        except MassiveTickerNotFound:
            not_found += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"massive_ticker_details ticker={ref.ticker} status=failed error={safe_exception_text(exc)}", flush=True)
        if index % 500 == 0:
            print(
                f"massive_ticker_details progress={index:,}/{len(symbols):,} "
                f"snapshot_rows={len(snapshot_rows):,} float_rows={len(float_rows):,} "
                f"not_found={not_found:,} failed={failed:,}",
                flush=True,
            )
    stamp_rows(snapshot_rows, run_id)
    stamp_rows(float_rows, run_id)
    snapshot_written = insert_rows(client, args.write_database, "market_security_market_snapshot_v1", snapshot_rows, args.batch_size) if args.execute else 0
    float_written = insert_rows(client, args.write_database, "market_security_float_v1", float_rows, args.batch_size) if args.execute else 0
    written = snapshot_written + float_written
    status = "completed" if snapshot_rows or float_rows else "covered_empty"
    finished = datetime.now(UTC)
    details = {
        "target_tables": ["market_security_market_snapshot_v1", "market_security_float_v1"],
        "snapshot_rows": len(snapshot_rows),
        "float_rows": len(float_rows),
        "snapshot_written": snapshot_written,
        "float_written": float_written,
        "not_found_tickers": not_found,
        "failed_tickers": failed,
        "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
        "api_key_diagnostic": massive_api_key_diagnostic(),
    }
    results = [SourceResult("massive_ticker_details", "massive_ticker_details", target_start, target_end, len(symbols), written, failed, status, details)]
    print(
        f"massive_ticker_details {target_start.isoformat()} status={status} "
        f"symbols={len(symbols):,} written={written:,} not_found={not_found:,} failed={failed:,}",
        flush=True,
    )
    if args.execute:
        insert_publication_coverage(
            client,
            database=args.write_database,
            coverage_id=f"{run_id}:massive_ticker_details:{target_start.isoformat()}:{target_end.isoformat()}",
            coverage_kind="massive_ticker_details",
            source_system="massive",
            source_object="ticker_details",
            start_date=target_start,
            end_date=target_end,
            status=status,
            rows_read=len(symbols),
            rows_written=written,
            rows_failed=failed,
            started_at_utc=started,
            finished_at_utc=finished,
            details=details,
            source_run_id=run_id,
        )
    return results


def fetch_massive_ticker_overview(args: argparse.Namespace, ticker: str) -> dict[str, Any]:
    url = "https://api.massive.com/v3/reference/tickers/" + parse.quote(ticker, safe="") + "?" + parse.urlencode({"apiKey": massive_api_key()})
    try:
        payload = json.loads(fetch_bytes(args, url).decode("utf-8", errors="replace"))
    except FileNotFoundError as exc:
        raise MassiveTickerNotFound(ticker, safe_url(url)) from exc
    result = payload.get("results") if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else {}


def normalize_massive_ticker_detail(overview: dict[str, Any], ref: SymbolRef, observed_at: datetime) -> tuple[dict[str, Any], dict[str, Any] | None]:
    ticker = ref.ticker
    evidence = f"massive:/v3/reference/tickers/{ticker}"
    content_hash = row_sha256(overview)
    as_of = parse_optional_date(overview.get("last_updated_utc")) or observed_at.date()
    snapshot_key = f"{ticker}:{observed_at.isoformat()}:{overview.get('market_cap')}:{overview.get('weighted_shares_outstanding')}:{overview.get('share_class_shares_outstanding')}"
    snapshot = {
        "security_market_snapshot_id": stable_id("massive_snapshot", snapshot_key),
        "security_id": ref.security_id,
        "listing_id": ref.listing_id,
        "symbol_id": ref.symbol_id,
        "source_system": "massive",
        "provider_ticker": ticker,
        "as_of_date": as_of.isoformat(),
        "observed_at_utc": observed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "market_cap": parse_float(overview.get("market_cap")),
        "round_lot": parse_int(overview.get("round_lot")),
        "share_class_shares_outstanding": parse_int(overview.get("share_class_shares_outstanding")),
        "weighted_shares_outstanding": parse_int(overview.get("weighted_shares_outstanding")),
        "snapshot_evidence_ref": evidence,
        "source_run_id": "",
        "source_content_sha256": content_hash,
        "inserted_at": clickhouse_now64(),
    }
    shares = parse_int(overview.get("share_class_shares_outstanding") or overview.get("weighted_shares_outstanding"))
    if shares is None:
        return snapshot, None
    float_key = f"{ticker}:{as_of.isoformat()}:{shares}:massive_ticker_details"
    float_row = {
        "security_float_id": stable_id("massive_share_supply", float_key),
        "symbol_id": ref.symbol_id,
        "listing_id": ref.listing_id,
        "security_id": ref.security_id,
        "source_system": "massive",
        "provider_ticker": ticker,
        "effective_date": as_of.isoformat(),
        "free_float": None,
        "free_float_percent": None,
        "shares_outstanding": shares,
        "float_source_tag": "massive_ticker_details_shares_outstanding",
        "source_event_key": float_key,
        "source_evidence_ref": evidence,
        "source_run_id": "",
        "source_content_sha256": content_hash,
        "inserted_at": clickhouse_now64(),
    }
    return snapshot, float_row


def fetch_massive_paginated(args: argparse.Namespace, first_url: str, api_key: str) -> tuple[list[dict[str, Any]], int, bool]:
    rows: list[dict[str, Any]] = []
    pages = 0
    next_url: str | None = first_url
    while next_url:
        payload = json.loads(fetch_bytes(args, next_url).decode("utf-8", errors="replace"))
        pages += 1
        for item in payload.get("results") or []:
            if isinstance(item, dict):
                rows.append(item)
        raw_next = payload.get("next_url")
        next_url = append_api_key(str(raw_next), api_key) if raw_next else None
    return rows, pages, False


def append_api_key(url: str, api_key: str) -> str:
    if not url:
        return ""
    return url if "apiKey=" in url else url + ("&" if "?" in url else "?") + parse.urlencode({"apiKey": api_key})


def run_ibkr_borrow_availability(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    run_id: str,
    start_date: date,
    end_date: date,
    symbols: dict[str, SymbolRef],
) -> list[SourceResult]:
    today = datetime.now(UTC).date()
    target_start = max(start_date, today)
    target_end = min(end_date, today + timedelta(days=1))
    if target_start >= target_end:
        return [
            SourceResult(
                "ibkr_borrow_availability",
                "ibkr_borrow_availability",
                start_date,
                end_date,
                0,
                0,
                0,
                "source_not_historical",
                {"message": "IBKR borrow availability is point-in-time only; historical rows require prior persisted snapshots."},
            )
        ]
    gaps = find_publication_gaps(
        client,
        database=args.write_database,
        coverage_kind="ibkr_borrow_availability",
        source_system="ibkr",
        start_date=target_start,
        end_date=target_end,
    ) if args.resume_from_coverage else [PublicationDateGap(target_start, target_end)]  # type: ignore[list-item]
    if not gaps:
        return []
    refs = [ref for ref in symbols.values() if ref.ibkr_conid.strip().isdigit()]
    if not refs:
        return [SourceResult("ibkr_borrow_availability", "ibkr_borrow_availability", target_start, target_end, 0, 0, 0, "covered_empty", {"reason": "no_ibkr_conids"})]
    started = datetime.now(UTC)
    observed_at = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    failed = 0
    try:
        ibkr_prepare_marketdata_session(args)
        for start in range(0, len(refs), 100):
            batch = refs[start : start + 100]
            by_conid = {ref.ibkr_conid: ref for ref in batch}
            try:
                payload = ibkr_marketdata_snapshot(args, sorted(by_conid))
                for item in payload:
                    conid = str(item.get("conid") or item.get("conidEx") or "").split("@", 1)[0]
                    ref = by_conid.get(conid)
                    if ref is None:
                        continue
                    rows.append(normalize_ibkr_borrow_row(item, ref, observed_at))
            except Exception as exc:  # noqa: BLE001
                failed += len(batch)
                print(f"ibkr_borrow_availability batch_start={start:,} status=failed error={repr(exc)}", flush=True)
            if start and start % 5_000 == 0:
                print(f"ibkr_borrow_availability progress={start:,}/{len(refs):,} rows={len(rows):,} failed={failed:,}", flush=True)
    except Exception as exc:  # noqa: BLE001
        failed = max(failed, len(refs))
        details = {"error": repr(exc), "target_table": "market_security_borrow_v1"}
        rows = []
    else:
        details = {
            "target_table": "market_security_borrow_v1",
            "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
            "eligible_conids": len(refs),
            "field_ids": ["7636", "7637", "7644"],
        }
    stamp_rows(rows, run_id)
    written = insert_rows(client, args.write_database, "market_security_borrow_v1", rows, args.batch_size) if args.execute else 0
    status = "completed" if rows else "covered_empty" if failed == 0 else "failed"
    finished = datetime.now(UTC)
    result = SourceResult("ibkr_borrow_availability", "ibkr_borrow_availability", target_start, target_end, len(refs), written, failed, status, details)
    print(f"ibkr_borrow_availability {target_start.isoformat()} status={status} eligible={len(refs):,} written={written:,} failed={failed:,}", flush=True)
    if args.execute:
        insert_publication_coverage(
            client,
            database=args.write_database,
            coverage_id=f"{run_id}:ibkr_borrow_availability:{target_start.isoformat()}:{target_end.isoformat()}",
            coverage_kind="ibkr_borrow_availability",
            source_system="ibkr",
            source_object="marketdata_snapshot_fields_7636_7637_7644",
            start_date=target_start,
            end_date=target_end,
            status=status,
            rows_read=len(refs),
            rows_written=written,
            rows_failed=failed,
            started_at_utc=started,
            finished_at_utc=finished,
            details=details,
            source_run_id=run_id,
        )
    return [result]


def ibkr_prepare_marketdata_session(args: argparse.Namespace) -> None:
    base_url = ibkr_base_url()
    fetch_ibkr_json(args, base_url + "/iserver/accounts")


def ibkr_marketdata_snapshot(args: argparse.Namespace, conids: list[str]) -> list[dict[str, Any]]:
    url = ibkr_base_url() + "/iserver/marketdata/snapshot?" + parse.urlencode(
        {
            "conids": ",".join(conids),
            "fields": "7636,7637,7644",
        }
    )
    payload = fetch_ibkr_json(args, url)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]
    return []


def fetch_ibkr_json(args: argparse.Namespace, url: str) -> Any:
    headers = {"Accept": "application/json", "User-Agent": "quant-reference-gateway-ibkr/1.0"}
    req = request.Request(url, headers=headers)
    context = ssl._create_unverified_context() if url.startswith("https://") else None
    if args.request_min_interval_seconds > 0:
        time.sleep(args.request_min_interval_seconds)
    with request.urlopen(req, timeout=args.request_timeout_seconds, context=context) as response:  # noqa: S310
        text = response.read().decode("utf-8", errors="replace")
    return json.loads(text) if text.strip() else {}


def normalize_ibkr_borrow_row(item: dict[str, Any], ref: SymbolRef, observed_at: datetime) -> dict[str, Any]:
    shortable_shares = parse_int(item.get("7636"))
    fee_rate = parse_percent_float(item.get("7637"))
    raw_status = string_or_none(item.get("7644"))
    borrow_status = raw_status or ("shortable" if shortable_shares and shortable_shares > 0 else "unknown")
    event_key = f"{ref.ibkr_conid}:{observed_at.isoformat()}:{shortable_shares}:{fee_rate}:{borrow_status}"
    return {
        "borrow_id": stable_id("ibkr_borrow", event_key),
        "symbol_id": ref.symbol_id,
        "listing_id": ref.listing_id,
        "security_id": ref.security_id,
        "source_system": "ibkr",
        "broker": "ibkr",
        "provider_ticker": ref.ticker,
        "ibkr_conid": ref.ibkr_conid,
        "observed_at_utc": observed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "borrow_status": borrow_status,
        "shortable_shares": shortable_shares,
        "lender_count": None,
        "indicative_borrow_rate": fee_rate,
        "fee_rate": fee_rate,
        "source_event_key": event_key,
        "source_evidence_ref": "ibkr:/iserver/marketdata/snapshot?fields=7636,7637,7644",
        "source_run_id": "",
        "source_content_sha256": row_sha256(item),
        "inserted_at": clickhouse_now64(),
    }


def load_symbol_refs(client: ClickHouseHttpClient, database: str) -> dict[str, SymbolRef]:
    sql = f"""
    SELECT
        upper(s.ticker) AS ticker,
        s.symbol_id AS symbol_id,
        l.listing_id AS listing_id,
        l.security_id AS security_id,
        ifNull(l.ibkr_conid, '') AS ibkr_conid
    FROM {qtable(database, 'id_symbol_v1')} s FINAL
    INNER JOIN {qtable(database, 'id_listing_v1')} l FINAL ON l.listing_id = s.listing_id
    WHERE s.status = 'active'
      AND s.primary_symbol_flag = 1
      AND l.listing_status = 'active'
    FORMAT JSONEachRow
    """
    text = client.execute(sql).strip()
    refs: dict[str, SymbolRef] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        refs[str(row["ticker"]).upper()] = SymbolRef(
            str(row["symbol_id"]),
            str(row["listing_id"]),
            str(row["security_id"]),
            str(row["ticker"]).upper(),
            str(row.get("ibkr_conid") or ""),
        )
    return refs


def insert_rows(client: ClickHouseHttpClient, database: str, table_name: str, rows: list[dict[str, Any]], batch_size: int) -> int:
    if not rows:
        return 0
    written = 0
    for start in range(0, len(rows), max(1, batch_size)):
        batch = rows[start : start + max(1, batch_size)]
        body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in batch)
        client.execute(f"INSERT INTO {qtable(database, table_name)} FORMAT JSONEachRow\n{body}")
        written += len(batch)
    return written


def stamp_rows(rows: list[dict[str, Any]], run_id: str) -> None:
    inserted_at = clickhouse_now64()
    for row in rows:
        row["source_run_id"] = run_id
        row["inserted_at"] = inserted_at


def fetch_bytes(args: argparse.Namespace, url: str) -> bytes:
    headers = {
        "User-Agent": args.user_agent or DEFAULT_USER_AGENT,
        "Accept": "*/*",
    }
    req = request.Request(url, headers=headers)
    attempts = max(1, int(args.request_max_retries) + 1)
    last_body = ""
    for attempt in range(1, attempts + 1):
        if args.request_min_interval_seconds > 0:
            time.sleep(args.request_min_interval_seconds)
        try:
            with request.urlopen(req, timeout=args.request_timeout_seconds) as response:
                return response.read()
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_body = body[:500]
            if exc.code == 404:
                raise FileNotFoundError(f"{safe_url(url)} returned HTTP 404") from exc
            if not should_retry_http_error(exc, body) or attempt >= attempts:
                raise RuntimeError(f"{safe_url(url)} returned HTTP {exc.code}: {last_body}") from exc
            sleep_seconds = retry_sleep_seconds(args, exc, attempt)
            print(
                f"request_retry url={safe_url(url)} status={exc.code} "
                f"attempt={attempt}/{attempts} sleep={sleep_seconds:.1f}s",
                flush=True,
            )
            time.sleep(sleep_seconds)
        except (TimeoutError, error.URLError) as exc:
            if attempt >= attempts:
                raise RuntimeError(f"{safe_url(url)} request failed after {attempts} attempts: {safe_exception_text(exc)}") from exc
            sleep_seconds = retry_sleep_seconds(args, None, attempt)
            print(
                f"request_retry url={safe_url(url)} status=transport_error "
                f"attempt={attempt}/{attempts} sleep={sleep_seconds:.1f}s error={safe_exception_text(exc)}",
                flush=True,
            )
            time.sleep(sleep_seconds)
    raise RuntimeError(f"{safe_url(url)} request failed: {last_body}")


def should_retry_http_error(exc: error.HTTPError, body: str) -> bool:
    if exc.code in RETRY_HTTP_CODES:
        return True
    return exc.code == 403 and SEC_RATE_LIMIT_TEXT.lower() in body.lower()


def retry_sleep_seconds(args: argparse.Namespace, exc: error.HTTPError | None, attempt: int) -> float:
    if exc is not None:
        retry_after = parse_retry_after_seconds(exc.headers.get("Retry-After", ""))
        if retry_after is not None:
            return min(max(0.0, retry_after), max(1.0, args.request_retry_max_seconds))
    base = max(0.1, float(args.request_retry_base_seconds))
    cap = max(base, float(args.request_retry_max_seconds))
    return min(cap, base * (2 ** (attempt - 1)))


def parse_retry_after_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except Exception:
            return None
        return max(0.0, (parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds())


def safe_url(url: str) -> str:
    parsed = parse.urlsplit(url)
    params = parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [(key, "redacted" if key.lower() in {"apikey", "api_key", "token"} else value) for key, value in params]
    return parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parse.urlencode(redacted), parsed.fragment))


def safe_exception_text(exc: Exception) -> str:
    text = repr(exc)
    return re.sub(r"([?&](?:apiKey|api_key|token)=)[^&'\"\s)]+", r"\1redacted", text, flags=re.IGNORECASE)


def default_user_agent() -> str:
    return (
        os.environ.get("SEC_USER_AGENT")
        or os.environ.get("SEC_EDGAR_USER_AGENT")
        or os.environ.get("NEWS_SEC_USER_AGENT")
        or DEFAULT_USER_AGENT
    )


def parse_finra_date(value: str, fallback: date) -> date:
    text = value.strip()
    if re.fullmatch(r"\d{8}", text):
        return parse_yyyymmdd(text)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return fallback


def parse_yyyymmdd(value: str) -> date:
    text = value.strip()
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def parse_int(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text == ".":
        return None
    return int(float(text))


def parse_float(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text == ".":
        return None
    return float(text)


def parse_percent_float(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text == ".":
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    return float(text)


def stable_id(prefix: str, key: str) -> str:
    return f"{prefix}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def row_sha256(row: dict[str, Any]) -> str:
    return sha256_bytes(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def massive_api_key() -> str:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("MASSIVE_API_KEY is required for Massive publication source sync")
    return key


def massive_api_key_diagnostic() -> dict[str, Any]:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        return {"present": False, "length": 0, "sha256_prefix": ""}
    return {
        "present": True,
        "length": len(key),
        "sha256_prefix": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
    }


def ibkr_base_url() -> str:
    return os.environ.get("IBKR_CPAPI_BASE_URL", "https://localhost:5000/v1/api").rstrip("/")


def parse_optional_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def date_string_or_none(value: Any) -> str | None:
    parsed = parse_optional_date(value)
    return parsed.isoformat() if parsed else None


def string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def is_us_equity_market_holiday(day: date) -> bool:
    """Return true for regular full-day US equity market holidays.

    This is used only to avoid marking known non-publication days as failed
    when FINRA has no daily short-volume file.
    """
    return day in us_equity_market_holidays(day.year)


def us_equity_market_holidays(year: int) -> set[date]:
    holidays = {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, calendar.MONDAY, 3),
        nth_weekday(year, 2, calendar.MONDAY, 3),
        good_friday(year),
        last_weekday(year, 5, calendar.MONDAY),
        observed_fixed_holiday(year, 7, 4),
        nth_weekday(year, 9, calendar.MONDAY, 1),
        nth_weekday(year, 11, calendar.THURSDAY, 4),
        observed_fixed_holiday(year, 12, 25),
    }
    if year >= 2022:
        holidays.add(observed_fixed_holiday(year, 6, 19))
    return holidays


def observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == calendar.SATURDAY:
        return holiday - timedelta(days=1)
    if holiday.weekday() == calendar.SUNDAY:
        return holiday + timedelta(days=1)
    return holiday


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    cursor = date(year, month, last_day)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def good_friday(year: int) -> date:
    return easter_sunday(year) - timedelta(days=2)


def easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def default_output_root() -> Path:
    if is_workstation_host() and path_exists(WORKSTATION_DATA_ROOT_WIN):
        return WORKSTATION_DATA_ROOT_WIN / "prepared" / "reference_market_publications"
    if path_exists(WORKSTATION_SHARE_DATA_ROOT_WIN):
        return WORKSTATION_SHARE_DATA_ROOT_WIN / "prepared" / "reference_market_publications"
    return WORKSTATION_DATA_ROOT_WIN / "prepared" / "reference_market_publications"


def is_workstation_host() -> bool:
    return os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_COMPUTER_NAME


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def schema_missing_results(args: argparse.Namespace, sources: list[str], start_date: date, end_date: date) -> list[SourceResult]:
    results: list[SourceResult] = []
    for source in sources:
        if source == "finra_short_volume":
            for venue in parse_csv(args.finra_venues):
                results.append(
                    SourceResult(
                        source=f"daily_short_volume:{venue}",
                        coverage_kind=f"finra_short_volume:{venue}",
                        start_date=start_date,
                        end_date=end_date,
                        rows_read=0,
                        rows_written=0,
                        rows_failed=0,
                        status="schema_missing",
                        details={
                            "message": "Dry-run did not create tables. Run with --execute or initialize schema first.",
                            "write_database": args.write_database,
                        },
                    )
                )
        elif source == "sec_fails_to_deliver":
            results.append(
                SourceResult(
                    source="sec_fails_to_deliver",
                    coverage_kind="sec_fails_to_deliver",
                    start_date=start_date,
                    end_date=end_date,
                    rows_read=0,
                    rows_written=0,
                    rows_failed=0,
                    status="schema_missing",
                    details={
                        "message": "Dry-run did not create tables. Run with --execute or initialize schema first.",
                        "write_database": args.write_database,
                    },
                )
            )
        elif source in {"massive_splits", "massive_dividends", "massive_ipos", "massive_ticker_details", "ibkr_borrow_availability"}:
            results.append(
                SourceResult(
                    source=source,
                    coverage_kind=source,
                    start_date=start_date,
                    end_date=end_date,
                    rows_read=0,
                    rows_written=0,
                    rows_failed=0,
                    status="schema_missing",
                    details={
                        "message": "Dry-run did not create tables. Run with --execute or initialize schema first.",
                        "write_database": args.write_database,
                    },
                )
            )
    return results


def qtable(database: str, table_name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(table_name)}"


def clickhouse_now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def print_header(args: argparse.Namespace, run_id: str, run_root: Path, loaded_env: list[Path], symbols: int) -> None:
    print("=" * 96, flush=True)
    print("Reference market publications historical gap fill", flush=True)
    print(
        f"run_id={run_id} execute={args.execute} "
        f"read_database={args.read_database} write_database={args.write_database}",
        flush=True,
    )
    print(f"start_date={args.start_date} end_date={args.end_date} sources={args.sources}", flush=True)
    print(f"run_root={run_root} symbols={symbols:,}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print(
        "secret_status="
        + json.dumps(
            secret_status(["REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD", "MASSIVE_API_KEY", "IBKR_CPAPI_BASE_URL"]),
            sort_keys=True,
        ),
        flush=True,
    )
    print("massive_api_key_diagnostic=" + json.dumps(massive_api_key_diagnostic(), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def write_summary(run_root: Path, args: argparse.Namespace, run_id: str, results: list[SourceResult]) -> None:
    payload = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "execute": args.execute,
        "database_alias": args.database,
        "read_database": args.read_database,
        "write_database": args.write_database,
        "results": [asdict(result) for result in results],
    }
    (run_root / "reference_market_publications_summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def print_summary(results: list[SourceResult], run_root: Path) -> None:
    print("=" * 96, flush=True)
    print(f"summary_root={run_root}", flush=True)
    print(
        "totals="
        + json.dumps(
            {
                "windows": len(results),
                "rows_read": sum(item.rows_read for item in results),
                "rows_written": sum(item.rows_written for item in results),
                "rows_failed": sum(item.rows_failed for item in results),
                "failed_windows": sum(1 for item in results if item.status == "failed"),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()
