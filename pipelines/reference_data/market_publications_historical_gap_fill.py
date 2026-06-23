from __future__ import annotations

import argparse
import calendar
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
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
USER_AGENT = "quant-reference-gateway/1.0 contact: local-research"
IMPLEMENTED_SOURCES = {"finra_short_volume", "sec_fails_to_deliver"}
WORKSTATION_COMPUTER_NAME = "DESKTOP-SAAI85T"
WORKSTATION_DATA_ROOT_WIN = Path("D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")


@dataclass(frozen=True, slots=True)
class SymbolRef:
    symbol_id: str
    listing_id: str
    security_id: str
    ticker: str


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
    parser.add_argument("--sources", default="finra_short_volume,sec_fails_to_deliver", help="Comma separated: finra_short_volume,sec_fails_to_deliver.")
    parser.add_argument("--finra-venues", default="CNMS", help="Comma separated FINRA short-volume source files. Default CNMS consolidated NMS.")
    parser.add_argument("--resume-from-coverage", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true", help="Write rows and coverage. Without this, only reports planned gaps.")
    parser.add_argument("--request-timeout-seconds", type=int, default=60)
    parser.add_argument("--request-min-interval-seconds", type=float, default=0.12)
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
    content = fetch_bytes(url, timeout=args.request_timeout_seconds)
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
    links = fetch_sec_ftd_links(args)
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
            if args.execute:
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


def fetch_sec_ftd_links(args: argparse.Namespace) -> list[dict[str, Any]]:
    html = fetch_bytes(SEC_FTD_PAGE, timeout=args.request_timeout_seconds).decode("utf-8", errors="replace")
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


def fetch_sec_ftd_file(
    args: argparse.Namespace,
    item: dict[str, Any],
    start_date: date,
    end_date: date,
    symbols: dict[str, SymbolRef],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    content = fetch_bytes(str(item["url"]), timeout=args.request_timeout_seconds)
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


def load_symbol_refs(client: ClickHouseHttpClient, database: str) -> dict[str, SymbolRef]:
    sql = f"""
    SELECT
        upper(s.ticker) AS ticker,
        s.symbol_id AS symbol_id,
        l.listing_id AS listing_id,
        l.security_id AS security_id
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
        refs[str(row["ticker"]).upper()] = SymbolRef(str(row["symbol_id"]), str(row["listing_id"]), str(row["security_id"]), str(row["ticker"]).upper())
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


def fetch_bytes(url: str, *, timeout: int) -> bytes:
    req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except error.HTTPError as exc:
        if exc.code == 404:
            raise FileNotFoundError(f"{url} returned HTTP 404") from exc
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {body[:500]}") from exc


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


def stable_id(prefix: str, key: str) -> str:
    return f"{prefix}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
    print("secret_status=" + json.dumps(secret_status(["REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD", "MASSIVE_API_KEY"]), sort_keys=True), flush=True)
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
