from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from pipelines.sec.edgar.sec_acceptance_backfill_build import (  # noqa: E402
    create_stage_table,
    insert_rows,
)


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v2"
DEFAULT_STAGE_DATABASE = "sec_core"
DEFAULT_STAGE_TABLE = "sec_bulk_mirror_filing_acceptance_v1"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_date_fallback_fill")
DEFAULT_BATCH_SIZE = 5_000
DATE_FALLBACK_SOURCE = "filing_date_midnight_fallback"


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    candidate_rows_jsonl: Path
    inserted_rows_jsonl: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RunPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            candidate_rows_jsonl=run_root / "candidate_rows.jsonl",
            inserted_rows_jsonl=run_root / "inserted_rows.jsonl",
            manifest_json=run_root / "sec_acceptance_date_fallback_fill_manifest.json",
            summary_md=run_root / "sec_acceptance_date_fallback_fill_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Final low-precision SEC accepted timestamp fallback. It inserts rows into the narrow "
            "acceptance staging table for q_live filings that still have no staged accepted_at "
            "source, using filing_date at 00:00:00 UTC. The source is explicitly labeled so it "
            "is not confused with exact SEC acceptance timestamps."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--target-table", default=os.environ.get("QLIVE_MIGRATION_SEC_FILING_TABLE", DEFAULT_TARGET_TABLE))
    parser.add_argument("--stage-database", default=os.environ.get("SEC_ACCEPTANCE_STAGE_DATABASE", DEFAULT_STAGE_DATABASE))
    parser.add_argument("--stage-table", default=os.environ.get("SEC_ACCEPTANCE_STAGE_TABLE", DEFAULT_STAGE_TABLE))
    parser.add_argument("--storage-policy", default=os.environ.get("SEC_CLICKHOUSE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "")
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ACCEPTANCE_DATE_FALLBACK_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_DATE_FALLBACK_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))))
    parser.add_argument("--max-rows", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_DATE_FALLBACK_MAX_ROWS", "0")), help="Optional safety cap; 0 means no cap.")
    parser.add_argument("--execute", action="store_true", help="Insert fallback rows. Without this flag, only write candidates and a manifest.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    run_id = f"sec_acceptance_date_fallback_fill_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root_win), run_id)
    started = time.perf_counter()
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print("=" * 96, flush=True)
    print("SEC acceptance date fallback fill", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target_table={args.target_database}.{args.target_table}", flush=True)
    print(f"stage_table={args.stage_database}.{args.stage_table}", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"loaded_env_files={json.dumps([str(path) for path in loaded_env_files])}", flush=True)
    print("=" * 96, flush=True)

    create_stage_table(client, args)

    candidates = load_candidates(client, args)
    write_jsonl(paths.candidate_rows_jsonl, candidates)

    inserted_rows = 0
    if args.execute and candidates:
        for offset in range(0, len(candidates), args.batch_size):
            batch = candidates[offset : offset + args.batch_size]
            inserted_rows += insert_rows(client, args.stage_database, args.stage_table, batch)
            write_jsonl(paths.inserted_rows_jsonl, batch, append=True)
            print(f"inserted={inserted_rows:,}/{len(candidates):,}", flush=True)

    remaining_after = count_remaining_without_staged_source(client, args)
    manifest = {
        "run_id": run_id,
        "execute": args.execute,
        "target_table": f"{args.target_database}.{args.target_table}",
        "stage_table": f"{args.stage_database}.{args.stage_table}",
        "accepted_at_source": DATE_FALLBACK_SOURCE,
        "candidate_rows": len(candidates),
        "inserted_rows": inserted_rows,
        "remaining_without_staged_source": remaining_after,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "secret_status": secret_status(
            [
                "QLIVE_MIGRATION_CLICKHOUSE_URL",
                "QLIVE_MIGRATION_CLICKHOUSE_USER",
                "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                "QMD_CLICKHOUSE_URL",
                "QMD_CLICKHOUSE_USER",
                "QMD_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_LIVE_STORAGE_POLICY",
            ]
        ),
    }
    paths.manifest_json.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    paths.summary_md.write_text(summary_markdown(manifest, candidates), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True), flush=True)


def load_candidates(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    stage = f"{quote_ident(args.stage_database)}.{quote_ident(args.stage_table)}"
    limit_sql = f"LIMIT {int(args.max_rows)}" if args.max_rows > 0 else ""
    sql = f"""
    SELECT
        q.cik,
        q.accession_number,
        q.accession_number_compact,
        q.company_name,
        q.form_type,
        toString(q.filing_date) AS filing_date,
        if(isNull(q.report_date), '', toString(q.report_date)) AS report_date,
        q.primary_document,
        q.primary_document_url,
        q.filing_detail_url,
        q.filing_size,
        q.items,
        q.source_file_name,
        q.source_content_sha256
    FROM (SELECT * FROM {target} FINAL WHERE accepted_at_utc IS NULL AND filing_date IS NOT NULL) AS q
    LEFT JOIN (SELECT cik, accession_number FROM {stage} FINAL) AS s
        ON q.cik = s.cik
       AND q.accession_number = s.accession_number
    WHERE s.accession_number = ''
    ORDER BY q.filing_date, q.cik, q.accession_number
    {limit_sql}
    FORMAT JSONEachRow
    """
    text = client.execute(sql)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        source = json.loads(line)
        key = (str(source["cik"]), str(source["accession_number"]))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(stage_row_from_source(source))
    return candidates


def stage_row_from_source(source: dict[str, Any]) -> dict[str, Any]:
    filing_date = source["filing_date"]
    accepted_at_utc = f"{filing_date} 00:00:00.000000000"
    accession = clean_string(source["accession_number"])
    cik = clean_string(source["cik"])
    row_fingerprint = "|".join([cik, accession, filing_date, DATE_FALLBACK_SOURCE])
    return {
        "acceptance_id": hashlib.sha256(row_fingerprint.encode("utf-8")).hexdigest(),
        "cik": cik,
        "accession_number": accession,
        "accession_number_compact": clean_string(source.get("accession_number_compact")),
        "company_name": clean_string(source.get("company_name")),
        "form_type": clean_string(source.get("form_type")),
        "filing_date": filing_date,
        "report_date": empty_to_none(source.get("report_date")),
        "accepted_at_utc": accepted_at_utc,
        "acceptance_datetime_raw": filing_date.replace("-", ""),
        "accepted_at_source": DATE_FALLBACK_SOURCE,
        "primary_document": empty_to_none(source.get("primary_document")),
        "primary_document_url": empty_to_none(source.get("primary_document_url")),
        "filing_detail_url": empty_to_none(source.get("filing_detail_url")),
        "filing_size": source.get("filing_size"),
        "items": empty_to_none(source.get("items")),
        "source_file_id": clean_string(source.get("source_file_name")) or "q_live_sec_filing_v2",
        "source_zip_sha256": "",
        "source_content_sha256": hashlib.sha256(
            f"{source.get('source_content_sha256') or ''}:{row_fingerprint}".encode("utf-8")
        ).hexdigest(),
        "last_seen_at_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    }


def count_remaining_without_staged_source(client: ClickHouseHttpClient, args: argparse.Namespace) -> int:
    target = f"{quote_ident(args.target_database)}.{quote_ident(args.target_table)}"
    stage = f"{quote_ident(args.stage_database)}.{quote_ident(args.stage_table)}"
    text = client.execute(
        f"""
        SELECT count()
        FROM (SELECT * FROM {target} FINAL WHERE accepted_at_utc IS NULL) AS q
        LEFT JOIN (SELECT cik, accession_number FROM {stage} FINAL) AS s
            ON q.cik = s.cik
           AND q.accession_number = s.accession_number
        WHERE s.accession_number = ''
        FORMAT TSV
        """
    )
    return int(text.strip() or "0")


def write_jsonl(path: Path, rows: list[dict[str, Any]], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def summary_markdown(manifest: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    lines = [
        "# SEC Acceptance Date Fallback Fill",
        "",
        f"- run_id: `{manifest['run_id']}`",
        f"- execute: `{manifest['execute']}`",
        f"- target_table: `{manifest['target_table']}`",
        f"- stage_table: `{manifest['stage_table']}`",
        f"- accepted_at_source: `{manifest['accepted_at_source']}`",
        f"- candidate_rows: `{manifest['candidate_rows']:,}`",
        f"- inserted_rows: `{manifest['inserted_rows']:,}`",
        f"- remaining_without_staged_source: `{manifest['remaining_without_staged_source']:,}`",
        "",
        "This pass is intentionally date-level only. It uses `filing_date 00:00:00 UTC` so older filings can carry a consistent non-null date, but these rows should not be treated as exact intraday event timestamps.",
        "",
        "## Candidates",
        "",
    ]
    for row in candidates:
        lines.append(
            f"- `{row['cik']}` `{row['accession_number']}` `{row['form_type']}` "
            f"`{row['filing_date']}` -> `{row['accepted_at_utc']}`"
        )
    lines.append("")
    return "\n".join(lines)


def default_migration_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_migration_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_migration_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


def clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def empty_to_none(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


if __name__ == "__main__":
    main()
