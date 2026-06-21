from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import (  # noqa: E402
    XBRL_COMPANY_FACT_TABLE,
    XBRL_CONCEPT_TABLE,
    XBRL_FRAME_OBSERVATION_TABLE,
    XBRL_FRAME_TABLE,
    SecClickHouseWriter,
    qi,
    sql_string,
)
from pipelines.sec.edgar.sec_pipeline.config import sec_user_agent  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.http import SecHttpClient  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.rate_limit import SecRateLimiter  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.xbrl_live import LiveXbrlRows, SecLiveXbrlExtractor  # noqa: E402
from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_xbrl_companyfacts_catchup")
DEFAULT_DATABASE = "q_live"


@dataclass(frozen=True, slots=True)
class CikWork:
    cik: str
    accessions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CikResult:
    cik: str
    requested_accessions: int
    matched_accessions: int
    matched_facts: int
    concept_rows: int
    company_fact_rows: int
    frame_rows: int
    frame_observation_rows: int
    no_fact_accessions: tuple[str, ...]
    status: str
    error: str = ""
    elapsed_seconds: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Catch up q_live SEC XBRL tables by fetching SEC companyfacts once per CIK "
            "and extracting canonical rows for XBRL-looking filings that are missing facts."
        )
    )
    parser.add_argument("--read-database", default=os.environ.get("SEC_XBRL_READ_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--write-database", default=os.environ.get("SEC_XBRL_WRITE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_XBRL_CATCHUP_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--start-date", default="", help="Inclusive filing/document date. Defaults to latest XBRL filed date in write DB.")
    parser.add_argument("--end-date", default="", help="Exclusive filing/document date. Defaults to tomorrow UTC.")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("SEC_XBRL_CATCHUP_WORKERS", "4")))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SEC_XBRL_CATCHUP_BATCH_SIZE", "10000")))
    parser.add_argument("--limit-ciks", type=int, default=0)
    parser.add_argument("--limit-accessions", type=int, default=0)
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_MIN_INTERVAL_SECONDS", "0.12")))
    parser.add_argument("--request-timeout-seconds", type=float, default=float(os.environ.get("SEC_REQUEST_TIMEOUT_SECONDS", "120")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("SEC_XBRL_CATCHUP_MAX_RETRIES", "4")))
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.environ.get("SEC_XBRL_CATCHUP_RETRY_BASE_SECONDS", "2.0")))
    parser.add_argument("--progress-interval", type=int, default=25)
    parser.add_argument("--execute", action="store_true", help="Fetch companyfacts and insert rows. Without this, only a plan is written.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    run_id = "sec_xbrl_catchup_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    result_path = run_root / "sec_xbrl_companyfacts_catchup_results.jsonl"
    no_fact_path = run_root / "sec_xbrl_companyfacts_catchup_no_facts.jsonl"
    summary_path = run_root / "sec_xbrl_companyfacts_catchup_summary.json"

    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    writer = SecClickHouseWriter(client, database=args.write_database)
    writer.validate_tables()

    start_date = parse_date(args.start_date) if args.start_date else latest_xbrl_filed_date(client, args.write_database)
    end_date = parse_date(args.end_date) if args.end_date else datetime.now(UTC).date() + timedelta(days=1)
    if end_date <= start_date:
        raise SystemExit("--end-date must be later than --start-date")

    work = load_missing_xbrl_work(
        client,
        read_database=args.read_database,
        write_database=args.write_database,
        start_date=start_date,
        end_date=end_date,
        limit_ciks=args.limit_ciks,
        limit_accessions=args.limit_accessions,
    )

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "execute": bool(args.execute),
        "read_database": args.read_database,
        "write_database": args.write_database,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "ciks": len(work),
        "accessions": sum(len(item.accessions) for item in work),
        "workers": max(1, args.workers),
        "batch_size": max(1, args.batch_size),
        "result_path": str(result_path),
        "no_fact_path": str(no_fact_path),
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(
            [
                "SEC_USER_AGENT",
                "SEC_EDGAR_USER_AGENT",
                "NEWS_SEC_USER_AGENT",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
            ]
        ),
    }
    (run_root / "sec_xbrl_companyfacts_catchup_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 96, flush=True)
    print("SEC XBRL companyfacts catch-up", flush=True)
    print(f"execute={args.execute} read={args.read_database} write={args.write_database}", flush=True)
    print(f"range=[{start_date},{end_date}) ciks={len(work):,} accessions={manifest['accessions']:,}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print("=" * 96, flush=True)
    if not args.execute:
        print("dry_run=true; no SEC requests or ClickHouse inserts were made", flush=True)
        summary_path.write_text(json.dumps({**manifest, "status": "planned"}, indent=2, sort_keys=True), encoding="utf-8")
        return

    started = time.perf_counter()
    limiter = SecRateLimiter(args.sec_request_min_interval_seconds)
    http = SecHttpClient(user_agent=sec_user_agent(), rate_limiter=limiter, timeout_seconds=args.request_timeout_seconds)
    extractor = SecLiveXbrlExtractor(http=http)
    pending = {
        XBRL_CONCEPT_TABLE: [],
        XBRL_COMPANY_FACT_TABLE: [],
        XBRL_FRAME_TABLE: [],
        XBRL_FRAME_OBSERVATION_TABLE: [],
    }
    totals = {
        "completed_ciks": 0,
        "failed_ciks": 0,
        "requested_accessions": 0,
        "matched_accessions": 0,
        "matched_facts": 0,
        "concept_rows": 0,
        "company_fact_rows": 0,
        "frame_rows": 0,
        "frame_observation_rows": 0,
        "no_fact_accessions": 0,
    }
    source_run_id = run_id
    inserted_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "")
    with result_path.open("a", encoding="utf-8") as results, no_fact_path.open("a", encoding="utf-8") as no_facts:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            future_map = {
                pool.submit(process_cik, extractor, item, source_run_id, inserted_at, max(0, args.max_retries), max(0.0, args.retry_base_seconds)): item
                for item in work
            }
            for future in concurrent.futures.as_completed(future_map):
                rows, result = future.result()
                append_rows(pending, rows)
                flush_if_needed(writer, pending, max(1, args.batch_size))
                update_totals(totals, result)
                results.write(json.dumps(asdict(result), sort_keys=True) + "\n")
                results.flush()
                for accession in result.no_fact_accessions:
                    no_facts.write(json.dumps({"cik": result.cik, "accession_number": accession, "run_id": run_id}, sort_keys=True) + "\n")
                no_facts.flush()
                if totals["completed_ciks"] == 1 or totals["completed_ciks"] % max(1, args.progress_interval) == 0 or totals["completed_ciks"] == len(work):
                    elapsed = time.perf_counter() - started
                    print(
                        "progress "
                        f"ciks={totals['completed_ciks']:,}/{len(work):,} failed={totals['failed_ciks']:,} "
                        f"matched_accessions={totals['matched_accessions']:,}/{totals['requested_accessions']:,} "
                        f"facts={totals['company_fact_rows']:,} no_facts={totals['no_fact_accessions']:,} "
                        f"elapsed={elapsed:.1f}s",
                        flush=True,
                    )
    flush_all(writer, pending)
    totals["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    summary = {**manifest, **totals, "status": "ok" if totals["failed_ciks"] == 0 else "completed_with_errors"}
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)


def latest_xbrl_filed_date(client: ClickHouseHttpClient, database: str) -> date:
    out = client.execute(f"SELECT max(filed_at_utc) FROM {qi(database)}.{qi(XBRL_COMPANY_FACT_TABLE)} FINAL FORMAT TSV").strip()
    if not out or out == "\\N":
        return date(1994, 1, 1)
    return parse_date(out[:10])


def load_missing_xbrl_work(
    client: ClickHouseHttpClient,
    *,
    read_database: str,
    write_database: str,
    start_date: date,
    end_date: date,
    limit_ciks: int,
    limit_accessions: int,
) -> list[CikWork]:
    limit_clause = f"LIMIT {int(limit_accessions)}" if limit_accessions > 0 else ""
    sql = f"""
    SELECT cik, groupArray(accession_number) AS accessions
    FROM (
        SELECT d.cik AS cik, d.accession_number AS accession_number
        FROM (
            SELECT DISTINCT cik, accession_number
            FROM {qi(read_database)}.sec_filing_document_v2 FINAL
            WHERE source_archive_date >= toDate({sql_string(start_date.isoformat())})
              AND source_archive_date < toDate({sql_string(end_date.isoformat())})
              AND accession_number != ''
              AND (
                    content_format = 'xbrl'
                 OR document_role = 'xbrl_sidecar'
                 OR positionCaseInsensitive(document_type, 'xbrl') > 0
              )
        ) AS d
        LEFT ANTI JOIN (
            SELECT DISTINCT cik, accession_number
            FROM {qi(write_database)}.{qi(XBRL_COMPANY_FACT_TABLE)} FINAL
            WHERE accession_number IS NOT NULL AND accession_number != ''
        ) AS x
        ON d.cik = x.cik AND d.accession_number = x.accession_number
        ORDER BY d.cik, d.accession_number
        {limit_clause}
    )
    GROUP BY cik
    ORDER BY cik
    FORMAT JSONEachRow
    """
    rows = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    work = [CikWork(cik=str(row["cik"]).zfill(10), accessions=tuple(str(item) for item in row["accessions"])) for row in rows]
    if limit_ciks > 0:
        return work[:limit_ciks]
    return work


def process_cik(
    extractor: SecLiveXbrlExtractor,
    item: CikWork,
    source_run_id: str,
    inserted_at: str,
    max_retries: int,
    retry_base_seconds: float,
) -> tuple[LiveXbrlRows, CikResult]:
    started = time.perf_counter()
    for attempt in range(max_retries + 1):
        try:
            rows = extractor.extract_for_accessions(
                cik=item.cik,
                accession_numbers=set(item.accessions),
                source_run_id=source_run_id,
                inserted_at=inserted_at,
            )
            matched = tuple(sorted({str(row["accession_number"]) for row in rows.company_fact_rows if row.get("accession_number")}))
            no_fact = tuple(sorted(set(item.accessions) - set(matched)))
            return rows, CikResult(
                cik=item.cik,
                requested_accessions=len(item.accessions),
                matched_accessions=len(matched),
                matched_facts=rows.matched_facts,
                concept_rows=len(rows.concept_rows),
                company_fact_rows=len(rows.company_fact_rows),
                frame_rows=len(rows.frame_rows),
                frame_observation_rows=len(rows.frame_observation_rows),
                no_fact_accessions=no_fact,
                status="ok",
                elapsed_seconds=round(time.perf_counter() - started, 3),
            )
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries:
                return LiveXbrlRows(), CikResult(
                    cik=item.cik,
                    requested_accessions=len(item.accessions),
                    matched_accessions=0,
                    matched_facts=0,
                    concept_rows=0,
                    company_fact_rows=0,
                    frame_rows=0,
                    frame_observation_rows=0,
                    no_fact_accessions=item.accessions,
                    status="failed",
                    error=repr(exc),
                    elapsed_seconds=round(time.perf_counter() - started, 3),
                )
            time.sleep(retry_base_seconds * (2**attempt))
    raise AssertionError("unreachable")


def append_rows(pending: dict[str, list[dict[str, Any]]], rows: LiveXbrlRows) -> None:
    pending[XBRL_CONCEPT_TABLE].extend(rows.concept_rows)
    pending[XBRL_COMPANY_FACT_TABLE].extend(rows.company_fact_rows)
    pending[XBRL_FRAME_TABLE].extend(rows.frame_rows)
    pending[XBRL_FRAME_OBSERVATION_TABLE].extend(rows.frame_observation_rows)


def flush_if_needed(writer: SecClickHouseWriter, pending: dict[str, list[dict[str, Any]]], batch_size: int) -> None:
    for table, rows in pending.items():
        if len(rows) >= batch_size:
            writer.insert_rows(table, rows)
            rows.clear()


def flush_all(writer: SecClickHouseWriter, pending: dict[str, list[dict[str, Any]]]) -> None:
    for table, rows in pending.items():
        writer.insert_rows(table, rows)
        rows.clear()


def update_totals(totals: dict[str, int], result: CikResult) -> None:
    totals["completed_ciks"] += 1
    if result.status != "ok":
        totals["failed_ciks"] += 1
    totals["requested_accessions"] += result.requested_accessions
    totals["matched_accessions"] += result.matched_accessions
    totals["matched_facts"] += result.matched_facts
    totals["concept_rows"] += result.concept_rows
    totals["company_fact_rows"] += result.company_fact_rows
    totals["frame_rows"] += result.frame_rows
    totals["frame_observation_rows"] += result.frame_observation_rows
    totals["no_fact_accessions"] += len(result.no_fact_accessions)


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


if __name__ == "__main__":
    main()
