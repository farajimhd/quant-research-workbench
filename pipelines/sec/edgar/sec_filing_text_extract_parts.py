from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
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
from pipelines.sec.edgar.sec_pipeline.submissions import parse_acceptance_datetime  # noqa: E402
from pipelines.sec.edgar.sec_parquet_parts import (  # noqa: E402
    DEFAULT_FILE_BYTES,
    DEFAULT_ROW_GROUP_BYTES,
    ParquetShardWriter,
)
from pipelines.sec.edgar.sec_pipeline.revision import (  # noqa: E402
    SourceRevision,
    parse_pac_event,
    source_revision,
)


DEFAULT_ARCHIVE_ROOT_WIN = Path("D:/market-data/sec_core/daily_archives")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_parts")
DEFAULT_DATABASE = "q_live"
NORMALIZER_VERSION = "sec_text_normalizer_v1"
FILING_COLUMNS = [
    "filing_id",
    "accession_number",
    "accession_number_compact",
    "cik",
    "issuer_id",
    "company_name",
    "form_type",
    "filing_date",
    "report_date",
    "accepted_at_utc",
    "acceptance_datetime_raw",
    "accepted_at_source",
    "primary_document",
    "primary_document_url",
    "filing_detail_url",
    "source_file_name",
    "filing_size",
    "items",
    "text_status",
    "source_run_id",
    "source_content_sha256",
    "inserted_at",
]
DOCUMENT_COLUMNS = [
    "document_id",
    "filing_id",
    "accession_number",
    "accession_number_compact",
    "cik",
    "sequence_number",
    "document_name",
    "document_type",
    "document_role",
    "description",
    "document_url",
    "source_archive_date",
    "source_archive_member",
    "source_archive_path",
    "file_extension",
    "content_format",
    "mime_type",
    "byte_size",
    "payload_char_count",
    "content_sha256",
    "text_sha256",
    "has_normalized_text",
    "extraction_status",
    "extraction_error",
    "normalizer_version",
    "source_version_key",
    "source_revision_at",
    "source_revision_rank",
    "source_revision_kind",
    "pac_event_id",
    "source_run_id",
    "inserted_at",
]
TEXT_SOURCE_COLUMNS = [
    "document_id",
    "filing_id",
    "accession_number",
    "accession_number_compact",
    "cik",
    "sequence_number",
    "document_name",
    "document_type",
    "document_role",
    "description",
    "document_url",
    "text_kind",
    "source_archive_date",
    "source_archive_member",
    "source_archive_path",
    "file_extension",
    "content_format",
    "mime_type",
    "source_text",
    "source_text_char_count",
    "source_text_byte_count",
    "content_sha256",
    "normalizer_version",
    "source_version_key",
    "source_revision_at",
    "source_revision_rank",
    "source_revision_kind",
    "pac_event_id",
    "source_run_id",
    "inserted_at",
]
TEXT_COLUMNS = [
    "document_id",
    "filing_id",
    "accession_number",
    "accession_number_compact",
    "cik",
    "text_kind",
    "text",
    "text_char_count",
    "text_byte_count",
    "text_sha256",
    "extraction_method",
    "normalizer_version",
    "quality_flags",
    "source_archive_date",
    "source_archive_member",
    "source_version_key",
    "source_revision_at",
    "source_revision_rank",
    "source_revision_kind",
    "pac_event_id",
    "extracted_at_utc",
    "source_run_id",
    "inserted_at",
]
SKIP_COLUMNS = [
    "skip_id",
    "document_id",
    "filing_id",
    "accession_number",
    "accession_number_compact",
    "cik",
    "sequence_number",
    "document_name",
    "document_type",
    "document_role",
    "source_archive_date",
    "source_archive_member",
    "content_format",
    "file_extension",
    "skip_reason",
    "quality_flags",
    "extraction_error",
    "normalizer_version",
    "source_version_key",
    "source_revision_at",
    "source_revision_rank",
    "source_revision_kind",
    "pac_event_id",
    "source_run_id",
    "inserted_at",
]
PAC_COLUMNS = [
    "pac_event_id",
    "accession_number",
    "cik",
    "correction_timestamp_raw",
    "correction_order_key",
    "filing_date",
    "date_as_of_change",
    "form_type",
    "action",
    "filing_deleted",
    "sequence_number",
    "document_name",
    "document_type",
    "document_deleted",
    "source_archive_date",
    "source_archive_member",
    "source_archive_path",
    "source_content_sha256",
    "source_run_id",
    "inserted_at",
]


@dataclass(frozen=True, slots=True)
class FilingParent:
    filing_id: str
    accession_number: str
    accession_number_compact: str
    cik: str
    form_type: str
    accepted_at_utc: str
    primary_document: str
    primary_document_url: str
    filing_detail_url: str


@dataclass(frozen=True, slots=True)
class PartFile:
    dataset_name: str
    target_table: str
    path: Path
    rows: int
    bytes: int
    columns: list[str]
    structure: str


class StructuredHTMLTextExtractor(HTMLParser):
    block_tags = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "div",
        "dl",
        "dt",
        "dd",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "tr",
        "ul",
    }
    skip_tags = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.skip_tags:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.skip_tags and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse SEC daily .nc.tar.gz archives and build byte-bounded Parquet "
            "parts for sec_filing_document_v3, sec_filing_text_v3, "
            "sec_filing_text_rendered_v3, and sec_filing_document_skip_v3. This script "
            "does not insert rows."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_sec_clickhouse_url())
    parser.add_argument("--user", default=default_sec_clickhouse_user())
    parser.add_argument("--password", default=default_sec_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_TEXT_TARGET_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_DAILY_ARCHIVE_ROOT_WIN", str(DEFAULT_ARCHIVE_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_TEXT_PARTS_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--start-date", required=True, help="Inclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--archive-workers", type=int, default=int(os.environ.get("SEC_TEXT_EXTRACT_ARCHIVE_WORKERS", "4")))
    parser.add_argument("--pending-multiplier", type=int, default=int(os.environ.get("SEC_TEXT_EXTRACT_PENDING_MULTIPLIER", "1")))
    parser.add_argument("--limit-archives", type=int, default=0)
    parser.add_argument("--max-filings-per-archive", type=int, default=0)
    parser.add_argument("--sample-limit", type=int, default=250)
    parser.add_argument("--sample-text-chars", type=int, default=1200)
    parser.add_argument("--parent-window-days-before", type=int, default=1)
    parser.add_argument("--parent-window-days-after", type=int, default=2)
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=int(os.environ.get("SEC_TEXT_EXTRACT_MAX_TEXT_CHARS", "0")),
        help="Optional normalized text storage cap. 0 means unlimited and is the default.",
    )
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument(
        "--parquet-row-group-mb",
        type=int,
        default=int(os.environ.get("SEC_TEXT_PARQUET_ROW_GROUP_MB", str(DEFAULT_ROW_GROUP_BYTES // 1024**2))),
    )
    parser.add_argument(
        "--parquet-file-mb",
        type=int,
        default=int(os.environ.get("SEC_TEXT_PARQUET_FILE_MB", str(DEFAULT_FILE_BYTES // 1024**2))),
    )
    parser.add_argument(
        "--parquet-compression-level",
        type=int,
        default=int(os.environ.get("SEC_TEXT_PARQUET_ZSTD_LEVEL", "1")),
    )
    parser.add_argument("--dry-run", action="store_true", help="Discover archives and write manifest only.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier(args.database, "--database")
    validate_date(args.start_date, "--start-date")
    validate_date(args.end_date, "--end-date")
    if int(args.parquet_row_group_mb) < 1:
        raise SystemExit("--parquet-row-group-mb must be positive")
    if int(args.parquet_file_mb) < int(args.parquet_row_group_mb):
        raise SystemExit("--parquet-file-mb must be at least --parquet-row-group-mb")
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    source_run_id = f"sec_text_extract_{run_id}"
    run_root = Path(args.output_root_win) / run_id
    parts_root = run_root / "parts"
    samples_path = run_root / "sec_filing_text_extract_samples.jsonl"
    errors_path = run_root / "sec_filing_text_extract_errors.jsonl"
    manifest_path = run_root / "sec_filing_text_extract_manifest.json"
    summary_path = run_root / "sec_filing_text_extract_summary.md"
    run_root.mkdir(parents=True, exist_ok=True)
    parts_root.mkdir(parents=True, exist_ok=True)

    archives = discover_archives(Path(args.archive_root_win), args.start_date, args.end_date)
    if args.limit_archives:
        archives = archives[: max(0, args.limit_archives)]

    print_header(args, run_root, source_run_id, loaded_env, archives)
    if args.dry_run:
        summary = empty_summary(args, source_run_id, loaded_env, archives)
        summary["dry_run"] = True
        write_manifest(manifest_path, args, source_run_id, loaded_env, summary, [])
        write_summary(summary_path, args, source_run_id, summary)
        print("dry_run=true; no archive content parsed", flush=True)
        return

    started = time.perf_counter()
    results = process_archives(args, archives, parts_root, source_run_id, errors_path, samples_path)
    summary = aggregate_results(args, source_run_id, loaded_env, archives, results, time.perf_counter() - started)
    part_files = collect_part_files(results)
    write_manifest(manifest_path, args, source_run_id, loaded_env, summary, part_files)
    write_summary(summary_path, args, source_run_id, summary)

    print("=" * 96, flush=True)
    print(f"done manifest={manifest_path}", flush=True)
    print(f"done summary={summary_path}", flush=True)
    print(
        f"archives={summary['archives_completed']:,}/{summary['archive_count']:,} "
        f"documents={summary['document_rows']:,} text={summary['text_rows']:,} "
        f"skips={summary['skip_rows']:,} errors={summary['error_rows']:,}",
        flush=True,
    )
    print("=" * 96, flush=True)


def discover_archives(archive_root: Path, start_date: str, end_date: str) -> list[Path]:
    if not archive_root.exists():
        raise SystemExit(f"archive root does not exist: {archive_root}")
    start_key = start_date.replace("-", "")
    end_key = end_date.replace("-", "")
    archives = []
    for path in sorted(archive_root.rglob("*.nc.tar.gz")):
        date_key = path.name[:8]
        if start_key <= date_key < end_key:
            archives.append(path)
    return archives


def process_archives(args: argparse.Namespace, archives: list[Path], parts_root: Path, source_run_id: str, errors_path: Path, samples_path: Path) -> list[dict[str, Any]]:
    workers = max(1, int(args.archive_workers))
    max_pending = max(workers, workers * max(1, int(args.pending_multiplier)))
    submitted = 0
    completed = 0
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    archive_iter = iter(archives)
    futures: dict[concurrent.futures.Future[dict[str, Any]], Path] = {}

    def submit_one(pool: concurrent.futures.ProcessPoolExecutor) -> bool:
        nonlocal submitted
        try:
            archive = next(archive_iter)
        except StopIteration:
            return False
        payload = worker_payload(args, archive, parts_root, source_run_id, submitted)
        futures[pool.submit(process_archive_worker, payload)] = archive
        submitted += 1
        return True

    with errors_path.open("w", encoding="utf-8") as errors_out, samples_path.open("w", encoding="utf-8") as samples_out:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            while len(futures) < max_pending and submit_one(pool):
                pass
            print(f"submitted_initial={submitted:,} max_pending={max_pending:,}", flush=True)
            try:
                while futures:
                    done, _ = concurrent.futures.wait(futures, timeout=5.0, return_when=concurrent.futures.FIRST_COMPLETED)
                    if not done:
                        elapsed = time.perf_counter() - started
                        print(
                            f"active={len(futures):,} submitted={submitted:,}/{len(archives):,} "
                            f"completed={completed:,} elapsed={elapsed:.1f}s",
                            flush=True,
                        )
                        continue
                    for future in done:
                        archive = futures.pop(future)
                        completed += 1
                        try:
                            result = future.result()
                        except Exception as exc:  # noqa: BLE001
                            result = failed_archive_result(archive, repr(exc))
                        results.append(result)
                        for row in result.get("errors", []):
                            errors_out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                        errors_out.flush()
                        for row in result.get("samples", []):
                            samples_out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                        samples_out.flush()
                        while len(futures) < max_pending and submit_one(pool):
                            pass
                        if completed == 1 or completed % max(1, int(args.progress_every)) == 0 or completed == len(archives):
                            elapsed = time.perf_counter() - started
                            print(
                                f"completed={completed:,}/{len(archives):,} submitted={submitted:,} "
                                f"active={len(futures):,} elapsed={elapsed:.1f}s "
                                f"last={archive.name} status={result.get('status')}",
                                flush=True,
                            )
            except KeyboardInterrupt:
                print("KeyboardInterrupt received; cancelling archive workers.", flush=True)
                for future in futures:
                    future.cancel()
                raise
    return results


def worker_payload(args: argparse.Namespace, archive: Path, parts_root: Path, source_run_id: str, archive_index: int) -> dict[str, Any]:
    return {
        "archive_path": str(archive),
        "archive_index": archive_index,
        "parts_root": str(parts_root),
        "source_run_id": source_run_id,
        "database": args.database,
        "clickhouse_url": args.clickhouse_url,
        "user": args.user,
        "password": args.password,
        "max_filings_per_archive": max(0, int(args.max_filings_per_archive)),
        "sample_limit": max(0, int(args.sample_limit)),
        "sample_text_chars": max(0, int(args.sample_text_chars)),
        "parent_window_days_before": max(0, int(args.parent_window_days_before)),
        "parent_window_days_after": max(1, int(args.parent_window_days_after)),
        "min_text_chars": max(0, int(args.min_text_chars)),
        "max_text_chars": max(0, int(args.max_text_chars)),
        "parquet_row_group_bytes": max(1, int(args.parquet_row_group_mb)) * 1024**2,
        "parquet_file_bytes": max(1, int(args.parquet_file_mb)) * 1024**2,
        "parquet_compression_level": int(args.parquet_compression_level),
    }


def process_archive_worker(payload: dict[str, Any]) -> dict[str, Any]:
    archive = Path(payload["archive_path"])
    archive_date = archive_date_from_name(archive.name)
    archive_date_text = archive_date.isoformat()
    part_prefix = f"{archive_date:%Y%m%d}_{int(payload['archive_index']):06d}"
    parts_root = Path(payload["parts_root"])
    writer_specs = {
        "filing": ("sec_filing_v3_parts", "sec_filing_v3", FILING_COLUMNS),
        "document": ("sec_filing_document_v3_parts", "sec_filing_document_v3", DOCUMENT_COLUMNS),
        "text_source": ("sec_filing_text_v3_parts", "sec_filing_text_v3", TEXT_SOURCE_COLUMNS),
        "text": ("sec_filing_text_rendered_v3_parts", "sec_filing_text_rendered_v3", TEXT_COLUMNS),
        "skip": ("sec_filing_document_skip_v3_parts", "sec_filing_document_skip_v3", SKIP_COLUMNS),
        "pac": ("sec_filing_pac_event_v3_parts", "sec_filing_pac_event_v3", PAC_COLUMNS),
    }
    writers = {
        dataset: ParquetShardWriter(
            dataset_name=dataset,
            target_table=target_table,
            output_directory=parts_root / directory,
            filename_prefix=f"{target_table}_part_{part_prefix}",
            columns=columns,
            archive_index=int(payload["archive_index"]),
            row_group_bytes=int(payload.get("parquet_row_group_bytes") or DEFAULT_ROW_GROUP_BYTES),
            file_bytes=int(payload.get("parquet_file_bytes") or DEFAULT_FILE_BYTES),
            compression_level=int(payload.get("parquet_compression_level") or 1),
        )
        for dataset, (directory, target_table, columns) in writer_specs.items()
    }

    client = ClickHouseHttpClient(str(payload["clickhouse_url"]), str(payload["user"]), str(payload["password"]))
    parents = load_parent_map(
        client,
        str(payload["database"]),
        archive_date,
        int(payload["parent_window_days_before"]),
        int(payload["parent_window_days_after"]),
    )
    inserted_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    stats: dict[str, Any] = {
        "archive_date": archive_date_text,
        "archive_path": str(archive),
        "status": "ok",
        "members": 0,
        "filings": 0,
        "documents": 0,
        "filing_parent_rows": 0,
        "document_rows": 0,
        "text_source_rows": 0,
        "text_rows": 0,
        "skip_rows": 0,
        "pac_rows": 0,
        "pac_filings": 0,
        "error_rows": 0,
        "parent_rows_loaded": len(parents),
        "parent_missing_filings": 0,
        "parse_errors": 0,
        "truncated_by_limit": False,
        "document_roles": Counter(),
        "text_kinds": Counter(),
        "skip_reasons": Counter(),
        "content_formats": Counter(),
        "form_types": Counter(),
        "part_files": [],
        "errors": [],
        "samples": [],
    }
    filing_parent_count = doc_count = text_source_count = text_count = skip_count = pac_count = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member_sequence, member in enumerate(tar, start=1):
                stop_event = payload.get("stop_event")
                if stop_event is not None and stop_event.is_set():
                    stats["status"] = "cancelled"
                    break
                if not member.isfile() or not member.name.lower().endswith(".nc"):
                    continue
                if payload["max_filings_per_archive"] and stats["filings"] >= payload["max_filings_per_archive"]:
                    stats["truncated_by_limit"] = True
                    break
                stats["members"] += 1
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                raw = handle.read()
                try:
                    filing = parse_filing(raw, member.name)
                except Exception as exc:  # noqa: BLE001
                    stats["parse_errors"] += 1
                    add_error(stats, archive_date_text, member.name, "", "", "parse_error", repr(exc))
                    continue
                stats["filings"] += 1
                stats["form_types"][filing["form_type"]] += 1
                source_sha = sha256_bytes(raw)
                pac_event = parse_pac_event(
                    decode_sec_bytes(raw),
                    archive_date=archive_date,
                    archive_member=member.name,
                    archive_path=archive,
                    source_content_sha256=source_sha,
                )
                if pac_event is not None:
                    pac_rows = pac_event.rows(source_run_id=str(payload["source_run_id"]), inserted_at=inserted_at)
                    for pac_row in pac_rows:
                        writers["pac"].append(pac_row)
                    pac_count += len(pac_rows)
                    stats["pac_filings"] += 1
                    continue
                revision = source_revision(
                    archive_date=archive_date,
                    archive_member=member.name,
                    archive_path=archive,
                    source_content_sha256=source_sha,
                    occurrence_sequence=member_sequence,
                )
                parent = resolve_parent(parents, filing)
                if parent is None:
                    stats["parent_missing_filings"] += 1
                    parent_row, parent = build_missing_parent_row(payload, archive, archive_date, archive_date_text, member.name, raw, filing, inserted_at)
                    writers["filing"].append(parent_row)
                    filing_parent_count += 1
                    parents[(parent.cik, parent.accession_number)] = parent
                for document in filing["documents"]:
                    stats["documents"] += 1
                    doc_row, text_source_row, text_row, skip_row, sample_row = build_rows(
                        payload, archive, archive_date_text, member.name, parent, document, inserted_at, revision=revision
                    )
                    writers["document"].append(doc_row)
                    doc_count += 1
                    stats["document_roles"][doc_row["document_role"]] += 1
                    stats["content_formats"][doc_row["content_format"]] += 1
                    if text_source_row is not None:
                        writers["text_source"].append(text_source_row)
                        text_source_count += 1
                    if text_row is not None:
                        writers["text"].append(text_row)
                        text_count += 1
                        stats["text_kinds"][text_row["text_kind"]] += 1
                    if skip_row is not None:
                        writers["skip"].append(skip_row)
                        skip_count += 1
                        stats["skip_reasons"][skip_row["skip_reason"]] += 1
                    if sample_row is not None and len(stats["samples"]) < payload["sample_limit"]:
                        stats["samples"].append(sample_row)
    except Exception as exc:  # noqa: BLE001
        stats["status"] = "failed"
        add_error(stats, archive_date_text, "", "", "", "archive_error", repr(exc))

    if stats["status"] == "ok":
        try:
            stats["part_files"] = [item for writer in writers.values() for item in writer.close()]
        except Exception as exc:  # noqa: BLE001
            stats["status"] = "failed"
            add_error(stats, archive_date_text, "", "", "", "parquet_close_error", repr(exc))
            for writer in writers.values():
                writer.abort()
    else:
        for writer in writers.values():
            writer.abort()

    stats["filing_parent_rows"] = filing_parent_count
    stats["document_rows"] = doc_count
    stats["text_source_rows"] = text_source_count
    stats["text_rows"] = text_count
    stats["skip_rows"] = skip_count
    stats["pac_rows"] = pac_count
    stats["error_rows"] = len(stats["errors"])
    stats["document_roles"] = dict(stats["document_roles"])
    stats["text_kinds"] = dict(stats["text_kinds"])
    stats["skip_reasons"] = dict(stats["skip_reasons"])
    stats["content_formats"] = dict(stats["content_formats"])
    stats["form_types"] = dict(stats["form_types"])
    return stats


def load_parent_map(client: ClickHouseHttpClient, db: str, archive_date: date, days_before: int, days_after: int) -> dict[tuple[str, str], FilingParent]:
    start = archive_date - timedelta(days=days_before)
    end = archive_date + timedelta(days=days_after)
    rows = query_rows(
        client,
        f"""
        SELECT
            filing_id,
            accession_number,
            accession_number_compact,
            cik,
            form_type,
            toString(accepted_at_utc) AS accepted_at_utc_text,
            ifNull(primary_document, '') AS primary_document,
            ifNull(primary_document_url, '') AS primary_document_url,
            ifNull(filing_detail_url, '') AS filing_detail_url
        FROM {quote_ident(db)}.sec_filing_v3 FINAL
        WHERE accepted_at_utc >= toDateTime64({sql_string(start.isoformat() + ' 00:00:00')}, 9, 'UTC')
          AND accepted_at_utc < toDateTime64({sql_string(end.isoformat() + ' 00:00:00')}, 9, 'UTC')
        FORMAT TSVWithNames
        """,
    )
    parents: dict[tuple[str, str], FilingParent] = {}
    for row in rows:
        cik = normalize_cik(row["cik"])
        accession = normalize_accession(row["accession_number"])
        parents[(cik, accession)] = FilingParent(
            filing_id=row["filing_id"],
            accession_number=accession,
            accession_number_compact=row["accession_number_compact"] or accession.replace("-", ""),
            cik=cik,
            form_type=row["form_type"],
            accepted_at_utc=row["accepted_at_utc_text"],
            primary_document=row["primary_document"],
            primary_document_url=row["primary_document_url"],
            filing_detail_url=row["filing_detail_url"],
        )
    return parents


def resolve_parent(parents: dict[tuple[str, str], FilingParent], filing: dict[str, Any]) -> FilingParent | None:
    return parents.get((normalize_cik(filing.get("cik")), normalize_accession(filing.get("accession_number"))))


def parse_filing(raw: bytes, member_name: str) -> dict[str, Any]:
    decoded = decode_sec_bytes(raw)
    header_text = decoded.split("<DOCUMENT>", 1)[0]
    accession = normalize_accession(header_value(header_text, "ACCESSION NUMBER") or tag_value(header_text, "ACCESSION-NUMBER") or Path(member_name).stem)
    cik = extract_submission_cik(header_text)
    form_type = clean_label(header_value(header_text, "CONFORMED SUBMISSION TYPE") or tag_value(header_text, "TYPE")).upper()
    filing_date = parse_sec_date_value(header_value(header_text, "FILED AS OF DATE") or tag_value(header_text, "FILING-DATE"))
    report_date = parse_sec_date_value(header_value(header_text, "CONFORMED PERIOD OF REPORT") or tag_value(header_text, "PERIOD"))
    acceptance_raw = clean_text_field(header_value(header_text, "ACCEPTANCE-DATETIME") or tag_value(header_text, "ACCEPTANCE-DATETIME"))
    company_name = extract_submission_company_name(header_text)
    items = extract_submission_items(header_text)
    documents = []
    for index, block in enumerate(re.findall(r"<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", decoded, flags=re.S | re.I), start=1):
        sequence = parse_int(tag_value(block, "SEQUENCE")) or index
        document_type = clean_label(tag_value(block, "TYPE")).upper()
        filename = clean_filename(tag_value(block, "FILENAME") or f"document_{sequence}")
        description = clean_text_field(tag_value(block, "DESCRIPTION"))
        payload = document_payload(block)
        documents.append(
            {
                "sequence_number": sequence,
                "document_type": document_type,
                "document_name": filename,
                "description": description,
                "payload": payload,
                "payload_bytes": len(payload.encode("utf-8", errors="replace")),
                "payload_char_count": len(payload),
            }
        )
    return {
        "member_name": member_name,
        "accession_number": accession,
        "accession_number_compact": accession.replace("-", ""),
        "cik": cik,
        "company_name": company_name,
        "form_type": form_type,
        "filing_date": filing_date,
        "report_date": report_date,
        "acceptance_datetime_raw": acceptance_raw,
        "items": items,
        "documents": documents,
    }


def build_missing_parent_row(
    payload: dict[str, Any],
    archive: Path,
    archive_date: date,
    archive_date_text: str,
    member_name: str,
    raw: bytes,
    filing: dict[str, Any],
    inserted_at: str,
) -> tuple[dict[str, Any], FilingParent]:
    accession = normalize_accession(filing["accession_number"])
    accession_compact = filing.get("accession_number_compact") or accession.replace("-", "")
    cik = normalize_cik(filing["cik"])
    primary_document = first_primary_document_name(filing["documents"])
    accepted_at_utc, accepted_source = accepted_timestamp_for_missing_parent(filing, archive_date)
    filing_id = deterministic_id("sec-filing-v2-archive-parent", cik, accession)
    filing_detail_url = sec_filing_detail_url(cik, accession_compact)
    primary_document_url = sec_document_url(cik, accession_compact, primary_document) if primary_document else ""
    row = {
        "filing_id": filing_id,
        "accession_number": accession,
        "accession_number_compact": accession_compact,
        "cik": cik,
        "issuer_id": None,
        "company_name": filing.get("company_name") or None,
        "form_type": filing.get("form_type") or "",
        "filing_date": filing.get("filing_date") or archive_date_text,
        "report_date": filing.get("report_date") or None,
        "accepted_at_utc": accepted_at_utc,
        "acceptance_datetime_raw": filing.get("acceptance_datetime_raw") or None,
        "accepted_at_source": accepted_source,
        "primary_document": primary_document or None,
        "primary_document_url": primary_document_url or None,
        "filing_detail_url": filing_detail_url or None,
        "source_file_name": member_name,
        "filing_size": len(raw),
        "items": filing.get("items") or None,
        "text_status": "archive_text_extracted",
        "source_run_id": str(payload["source_run_id"]),
        "source_content_sha256": sha256_bytes(raw),
        "inserted_at": inserted_at,
    }
    parent = FilingParent(
        filing_id=filing_id,
        accession_number=accession,
        accession_number_compact=accession_compact,
        cik=cik,
        form_type=row["form_type"],
        accepted_at_utc=accepted_at_utc,
        primary_document=primary_document,
        primary_document_url=primary_document_url,
        filing_detail_url=filing_detail_url,
    )
    return row, parent


def build_rows(
    payload: dict[str, Any],
    archive: Path,
    archive_date_text: str,
    member_name: str,
    parent: FilingParent,
    document: dict[str, Any],
    inserted_at: str,
    *,
    revision: SourceRevision | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    doc_type = document["document_type"]
    doc_name = document["document_name"]
    file_ext = file_extension(doc_name)
    content_format = detect_content_format(doc_name, document["payload"])
    document_role = classify_document_role(parent, document, content_format)
    content_sha = sha256_text(document["payload"])
    normalized_text, extraction_method, quality_flags = normalize_document_text(document["payload"], content_format)
    skip_reason = skip_reason_for_document(document_role, content_format, normalized_text, quality_flags, int(payload["min_text_chars"]))
    max_text_chars = int(payload.get("max_text_chars") or 0)
    if max_text_chars > 0 and normalized_text and len(normalized_text) > max_text_chars:
        normalized_text = normalized_text[:max_text_chars].rstrip()
        quality_flags.append("truncated_max_text_chars")
    has_text = bool(normalized_text and not skip_reason)
    text_sha = sha256_text(normalized_text) if normalized_text else ""
    document_id = deterministic_id("sec-document-v2", parent.cik, parent.accession_number, str(document["sequence_number"]), doc_name, doc_type)
    document_url = build_document_url(parent, doc_name)
    status = "text_extracted" if has_text else f"skipped_{skip_reason or 'empty'}"
    extraction_error = None
    text_kind = text_kind_for_role(document_role)
    revision = revision or source_revision(
        archive_date=archive_date_text,
        archive_member=member_name,
        archive_path=archive,
        source_content_sha256=content_sha,
    )
    doc_row = {
        "document_id": document_id,
        "filing_id": parent.filing_id,
        "accession_number": parent.accession_number,
        "accession_number_compact": parent.accession_number_compact,
        "cik": parent.cik,
        "sequence_number": int(document["sequence_number"]),
        "document_name": doc_name,
        "document_type": doc_type,
        "document_role": document_role,
        "description": document["description"] or None,
        "document_url": document_url or None,
        "source_archive_date": archive_date_text,
        "source_archive_member": member_name,
        "source_archive_path": str(archive),
        "file_extension": file_ext,
        "content_format": content_format,
        "mime_type": mime_type_for_format(content_format),
        "byte_size": int(document["payload_bytes"]),
        "payload_char_count": int(document["payload_char_count"]),
        "content_sha256": content_sha,
        "text_sha256": text_sha or None,
        "has_normalized_text": 1 if has_text else 0,
        "extraction_status": status,
        "extraction_error": extraction_error,
        "normalizer_version": NORMALIZER_VERSION,
        "source_version_key": revision.source_version_key,
        "source_revision_at": revision.source_revision_at,
        "source_revision_rank": revision.source_revision_rank,
        "source_revision_kind": revision.source_revision_kind,
        "pac_event_id": revision.pac_event_id or None,
        "source_run_id": str(payload["source_run_id"]),
        "inserted_at": inserted_at,
    }
    text_source_row = None
    if should_persist_text_source(document_role, content_format):
        source_text = document["payload"]
        text_source_row = {
            "document_id": document_id,
            "filing_id": parent.filing_id,
            "accession_number": parent.accession_number,
            "accession_number_compact": parent.accession_number_compact,
            "cik": parent.cik,
            "sequence_number": int(document["sequence_number"]),
            "document_name": doc_name,
            "document_type": doc_type,
            "document_role": document_role,
            "description": document["description"] or None,
            "document_url": document_url or None,
            "text_kind": text_kind,
            "source_archive_date": archive_date_text,
            "source_archive_member": member_name,
            "source_archive_path": str(archive),
            "file_extension": file_ext,
            "content_format": content_format,
            "mime_type": mime_type_for_format(content_format),
            "source_text": source_text,
            "source_text_char_count": len(source_text),
            "source_text_byte_count": len(source_text.encode("utf-8", errors="replace")),
            "content_sha256": content_sha,
            "normalizer_version": NORMALIZER_VERSION,
            "source_version_key": revision.source_version_key,
            "source_revision_at": revision.source_revision_at,
            "source_revision_rank": revision.source_revision_rank,
            "source_revision_kind": revision.source_revision_kind,
            "pac_event_id": revision.pac_event_id or None,
            "source_run_id": str(payload["source_run_id"]),
            "inserted_at": inserted_at,
        }
    text_row = None
    if has_text:
        text_row = {
            "document_id": document_id,
            "filing_id": parent.filing_id,
            "accession_number": parent.accession_number,
            "accession_number_compact": parent.accession_number_compact,
            "cik": parent.cik,
            "text_kind": text_kind,
            "text": normalized_text,
            "text_char_count": len(normalized_text),
            "text_byte_count": len(normalized_text.encode("utf-8")),
            "text_sha256": text_sha,
            "extraction_method": extraction_method,
            "normalizer_version": NORMALIZER_VERSION,
            "quality_flags": sorted(set(quality_flags)),
            "source_archive_date": archive_date_text,
            "source_archive_member": member_name,
            "source_version_key": revision.source_version_key,
            "source_revision_at": revision.source_revision_at,
            "source_revision_rank": revision.source_revision_rank,
            "source_revision_kind": revision.source_revision_kind,
            "pac_event_id": revision.pac_event_id or None,
            "extracted_at_utc": inserted_at,
            "source_run_id": str(payload["source_run_id"]),
            "inserted_at": inserted_at,
        }
    skip_row = None
    if not has_text:
        reason = skip_reason or "empty_text"
        skip_row = {
            "skip_id": deterministic_id("sec-document-skip-v1", document_id, reason),
            "document_id": document_id,
            "filing_id": parent.filing_id,
            "accession_number": parent.accession_number,
            "accession_number_compact": parent.accession_number_compact,
            "cik": parent.cik,
            "sequence_number": int(document["sequence_number"]),
            "document_name": doc_name,
            "document_type": doc_type,
            "document_role": document_role,
            "source_archive_date": archive_date_text,
            "source_archive_member": member_name,
            "content_format": content_format,
            "file_extension": file_ext,
            "skip_reason": reason,
            "quality_flags": sorted(set(quality_flags)),
            "extraction_error": extraction_error,
            "normalizer_version": NORMALIZER_VERSION,
            "source_version_key": revision.source_version_key,
            "source_revision_at": revision.source_revision_at,
            "source_revision_rank": revision.source_revision_rank,
            "source_revision_kind": revision.source_revision_kind,
            "pac_event_id": revision.pac_event_id or None,
            "source_run_id": str(payload["source_run_id"]),
            "inserted_at": inserted_at,
        }
    sample_row = None
    if has_text and document_role in {"primary_document", "press_release_exhibit", "material_exhibit", "proxy_document", "prospectus"}:
        sample_row = {
            "archive_date": archive_date_text,
            "accession_number": parent.accession_number,
            "cik": parent.cik,
            "form_type": parent.form_type,
            "document_type": doc_type,
            "document_name": doc_name,
            "document_role": document_role,
            "text_kind": text_kind,
            "text_char_count": len(normalized_text),
            "quality_flags": sorted(set(quality_flags)),
            "text_prefix": normalized_text[: int(payload["sample_text_chars"])],
        }
    return doc_row, text_source_row, text_row, skip_row, sample_row


def normalize_document_text(payload: str, content_format: str) -> tuple[str, str, list[str]]:
    flags: list[str] = []
    if not payload:
        return "", "empty_text_v1", ["empty_payload"]
    if "\ufffd" in payload:
        flags.append("replacement_char")
    if has_mojibake(payload):
        flags.append("mojibake_suspect")
    if any(ord(ch) > 127 for ch in payload[:5000]):
        flags.append("non_ascii")
    if content_format == "html":
        text = html_to_text(payload)
        method = "html_text_v1"
    elif content_format == "plain_text":
        text = plain_text_to_text(payload)
        method = "plain_text_v1"
    elif content_format == "pdf":
        return "", "pdf_text_pending_v1", flags + ["pdf_text_pending"]
    else:
        return "", f"skipped_{content_format}_v1", flags
    text = canonicalize_text(text)
    if len(text) < 100:
        flags.append("short_text")
    return text, method, flags


def html_to_text(payload: str) -> str:
    text = re.sub(r"(?is)<!--.*?-->", " ", payload)
    text = re.sub(r"(?is)<ix:hidden\b.*?</ix:hidden>", " ", text)
    parser = StructuredHTMLTextExtractor()
    try:
        parser.feed(text)
        parser.close()
        return parser.text()
    except Exception:  # noqa: BLE001
        text = re.sub(r"(?is)<script\b.*?</script>", " ", text)
        text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        return text


def plain_text_to_text(payload: str) -> str:
    text = payload
    if "<" in text and ">" in text:
        text = re.sub(r"(?is)<script\b.*?</script>", " ", text)
        text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
    return text


def should_persist_text_source(document_role: str, content_format: str) -> bool:
    if document_role == "xbrl_sidecar" or content_format == "xbrl":
        return False
    if document_role in {"image", "stylesheet_or_script", "spreadsheet", "archive_or_json", "pdf"}:
        return False
    return content_format in {"html", "plain_text", "xml"}


def canonicalize_text(text: str) -> str:
    text = html.unescape(text)
    text = repair_common_mojibake(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if is_low_signal_line(stripped):
            continue
        lines.append(stripped)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def repair_common_mojibake(text: str) -> str:
    replacements = {
        "\u00e2\u20ac\u201c": "\u2013",
        "\u00e2\u20ac\u201d": "\u2014",
        "\u00e2\u20ac\u2122": "\u2019",
        "\u00e2\u20ac\u02dc": "\u2018",
        "\u00e2\u20ac\u0153": "\u201c",
        "\u00e2\u20ac\u009d": "\u201d",
        "\u00e2\u20ac\u00a6": "\u2026",
        "\u00e2\u201e\u00a2": "\u2122",
        "\u00e2\u20ac\u2030": " ",
        "\u00ef\u00ac\u0081": "fi",
        "\u00ef\u00ac\u0082": "fl",
        "\u00c2\u00a0": " ",
        "\u00c2\u00ae": "\u00ae",
        "\u00c2\u00a9": "\u00a9",
        "\u00c3\u00a1": "\u00e1",
        "\u00c3\u00a9": "\u00e9",
        "\u00c3\u00ad": "\u00ed",
        "\u00c3\u00b3": "\u00f3",
        "\u00c3\u00ba": "\u00fa",
        "\u00c3\u00b1": "\u00f1",
        "\u00c3\u00bc": "\u00fc",
        "\u00c3\u00b6": "\u00f6",
        "\u00c3\u00a4": "\u00e4",
        "\u00c3\u2026": "\u00c5",
        "\u00c3\u00a5": "\u00e5",
        "\u00c3\u02dc": "\u00d8",
        "\u00c3\u00b8": "\u00f8",
        "\u00c3\u2021": "\u00c7",
        "\u00c3\u00a7": "\u00e7",
        "\u00c3\u2018": "\u00d1",
        "\u00c3\u0089": "\u00c9",
        "\u00c3\u201c": "\u00d3",
        "\u00c3\u0161": "\u00da",
        "\u00c2": "",
    }
    repaired = text
    for bad, good in replacements.items():
        repaired = repaired.replace(bad, good)
    return repaired


def is_low_signal_line(line: str) -> bool:
    if not line:
        return False
    lowered = line.lower()
    if lowered.startswith("javascript:"):
        return True
    if len(line) > 2000 and len(set(line)) < 20:
        return True
    return False


def skip_reason_for_document(document_role: str, content_format: str, text: str, quality_flags: list[str], min_text_chars: int) -> str:
    if document_role == "xbrl_sidecar":
        return "xbrl_sidecar"
    if document_role == "image":
        return "image_or_graphic"
    if document_role == "stylesheet_or_script":
        return "stylesheet_or_script"
    if document_role == "spreadsheet":
        return "spreadsheet"
    if document_role == "archive_or_json":
        return "archive_or_json"
    if content_format == "pdf":
        return "pdf_text_pending"
    if content_format in {"xml", "xbrl"}:
        return "structured_xml_or_xbrl"
    if content_format in {"image", "binary_like", "unknown"}:
        return f"unsupported_{content_format}"
    if not text:
        return "empty_text"
    if len(text) < min_text_chars:
        return "too_short"
    return ""


def classify_document_role(parent: FilingParent, document: dict[str, Any], content_format: str) -> str:
    doc_type = document["document_type"].upper()
    doc_name = document["document_name"].lower()
    form_type = parent.form_type.upper()
    primary_name = parent.primary_document.lower()
    if content_format == "image" or doc_type == "GRAPHIC":
        return "image"
    if doc_name.endswith((".css", ".js")) or doc_type in {"CSS", "JS"}:
        return "stylesheet_or_script"
    if doc_name.endswith((".xlsx", ".xls")) or doc_type in {"EXCEL"}:
        return "spreadsheet"
    if doc_name.endswith((".zip", ".json")) or doc_type in {"ZIP", "JSON"}:
        return "archive_or_json"
    if doc_type.startswith("EX-101") or content_format == "xbrl":
        return "xbrl_sidecar"
    if doc_type in {"XML", "INFORMATION TABLE"} and content_format == "xml":
        return "xbrl_sidecar"
    if doc_name == primary_name or doc_type == form_type:
        return "primary_document"
    if doc_type.startswith("EX-99"):
        return "press_release_exhibit"
    if doc_type.startswith("EX-10"):
        return "material_exhibit"
    if any(token in doc_type for token in ("DEF 14", "PRE 14", "DEFM14", "PREM14", "DEFA14", "PRER14", "DFAN14")):
        return "proxy_document"
    if doc_type.startswith(("424B", "S-1", "F-1", "S-3", "F-3")) or form_type.startswith(("424B", "S-1", "F-1", "S-3", "F-3")):
        return "prospectus"
    if content_format == "pdf":
        return "pdf"
    if doc_type.startswith("EX-"):
        return "other_text_exhibit"
    return "other_text_document"


def text_kind_for_role(document_role: str) -> str:
    if document_role in {"primary_document", "press_release_exhibit", "material_exhibit", "proxy_document", "prospectus", "other_text_exhibit"}:
        return document_role
    return "other_text_exhibit"


def detect_content_format(filename: str, payload: str) -> str:
    ext = file_extension(filename)
    lowered_payload = payload[:2000].lower()
    html_like = bool(re.search(r"<html\b|<table\b|<div\b|<p\b|</[a-z][a-z0-9]*>", lowered_payload, flags=re.I))
    if not payload:
        return "empty"
    if ext in {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff"}:
        return "image"
    if ext == "pdf" or payload.lstrip().startswith("%PDF"):
        return "pdf"
    if ext in {"xlsx", "xls"}:
        return "spreadsheet"
    if ext in {"zip"}:
        return "archive"
    if ext in {"json"}:
        return "json"
    if ext in {"xsd", "xml"}:
        return "xml"
    if ext in {"htm", "html"} and html_like:
        return "html"
    if "xbrl" in lowered_payload or "<xbrl" in lowered_payload or "<ix:" in lowered_payload:
        return "xbrl"
    if html_like:
        return "html"
    if is_binary_like(payload):
        return "binary_like"
    return "plain_text"


def structure_for_columns(columns: list[str]) -> str:
    type_map = {
        "sequence_number": "UInt32",
        "byte_size": "UInt64",
        "payload_char_count": "UInt64",
        "source_text_char_count": "UInt64",
        "source_text_byte_count": "UInt64",
        "has_normalized_text": "UInt8",
        "filing_deleted": "UInt8",
        "document_deleted": "UInt8",
        "text_char_count": "UInt64",
        "text_byte_count": "UInt64",
        "filing_size": "Nullable(UInt64)",
        "quality_flags": "Array(String)",
        "filing_date": "Nullable(Date)",
        "report_date": "Nullable(Date)",
        "date_as_of_change": "Nullable(Date)",
        "source_archive_date": "Date",
        "accepted_at_utc": "Nullable(DateTime64(9, 'UTC'))",
        "source_revision_at": "DateTime64(3, 'UTC')",
        "source_revision_rank": "UInt64",
        "correction_order_key": "UInt64",
        "inserted_at": "DateTime64(3, 'UTC')",
        "extracted_at_utc": "DateTime64(3, 'UTC')",
    }
    nullable = {
        "description",
        "document_url",
        "source_archive_path",
        "mime_type",
        "text_sha256",
        "extraction_error",
        "issuer_id",
        "company_name",
        "acceptance_datetime_raw",
        "primary_document",
        "primary_document_url",
        "filing_detail_url",
        "items",
        "pac_event_id",
    }
    parts = []
    for column in columns:
        if column in type_map:
            typ = type_map[column]
        elif column in nullable:
            typ = "Nullable(String)"
        else:
            typ = "String"
        parts.append(f"{column} {typ}")
    return ", ".join(parts)


def collect_part_files(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    part_files = []
    for result in results:
        for part in result.get("part_files", []):
            if int(part.get("rows") or 0) > 0:
                part_files.append(part)
    return part_files


def aggregate_results(args: argparse.Namespace, source_run_id: str, loaded_env: list[Path], archives: list[Path], results: list[dict[str, Any]], wall_seconds: float) -> dict[str, Any]:
    summary = empty_summary(args, source_run_id, loaded_env, archives)
    summary["wall_seconds"] = round(wall_seconds, 3)
    summary["archives_completed"] = len(results)
    for result in results:
        if result.get("status") != "ok":
            summary["failed_archives"] += 1
        for key in ("members", "filings", "documents", "document_rows", "text_source_rows", "text_rows", "skip_rows", "pac_rows", "pac_filings", "error_rows", "parent_rows_loaded", "parent_missing_filings", "parse_errors"):
            summary[key] += int(result.get(key) or 0)
        summary["filing_parent_rows"] += int(result.get("filing_parent_rows") or 0)
        merge_counter(summary["document_roles"], result.get("document_roles") or {})
        merge_counter(summary["text_kinds"], result.get("text_kinds") or {})
        merge_counter(summary["skip_reasons"], result.get("skip_reasons") or {})
        merge_counter(summary["content_formats"], result.get("content_formats") or {})
        merge_counter(summary["form_types"], result.get("form_types") or {})
    return summary


def empty_summary(args: argparse.Namespace, source_run_id: str, loaded_env: list[Path], archives: list[Path]) -> dict[str, Any]:
    return {
        "source_run_id": source_run_id,
        "normalizer_version": NORMALIZER_VERSION,
        "database": args.database,
        "archive_root_win": args.archive_root_win,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "archive_count": len(archives),
        "archives_completed": 0,
        "failed_archives": 0,
        "members": 0,
        "filings": 0,
        "documents": 0,
        "filing_parent_rows": 0,
        "document_rows": 0,
        "text_source_rows": 0,
        "text_rows": 0,
        "skip_rows": 0,
        "pac_rows": 0,
        "pac_filings": 0,
        "error_rows": 0,
        "parent_rows_loaded": 0,
        "parent_missing_filings": 0,
        "parse_errors": 0,
        "document_roles": {},
        "text_kinds": {},
        "skip_reasons": {},
        "content_formats": {},
        "form_types": {},
        "loaded_env_files": [str(path) for path in loaded_env],
        "git_commit": git_commit(),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }


def write_manifest(path: Path, args: argparse.Namespace, source_run_id: str, loaded_env: list[Path], summary: dict[str, Any], part_files: list[dict[str, Any]]) -> None:
    payload = {
        "source_run_id": source_run_id,
        "normalizer_version": NORMALIZER_VERSION,
        "clickhouse_format": "Parquet",
        "database": args.database,
        "target_tables": {
            "filing": "sec_filing_v3",
            "document": "sec_filing_document_v3",
            "text_source": "sec_filing_text_v3",
            "text": "sec_filing_text_rendered_v3",
            "skip": "sec_filing_document_skip_v3",
            "pac": "sec_filing_pac_event_v3",
        },
        "parts_root": str(path.parent / "parts"),
        "part_files": part_files,
        "summary": summary,
        "secret_status": secret_status(secret_keys()),
        "loaded_env_files": [str(item) for item in loaded_env],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, source_run_id: str, summary: dict[str, Any]) -> None:
    lines = [
        "# SEC Filing Text Extract Parts",
        "",
        f"- Source run id: `{source_run_id}`",
        f"- Date range: `{args.start_date}` to `{args.end_date}` exclusive",
        f"- Archives: `{summary['archives_completed']:,}/{summary['archive_count']:,}`",
        f"- Failed archives: `{summary['failed_archives']:,}`",
        f"- Filings: `{summary['filings']:,}`",
        f"- Documents parsed: `{summary['documents']:,}`",
        f"- Missing parent rows written: `{summary['filing_parent_rows']:,}`",
        f"- Document rows: `{summary['document_rows']:,}`",
        f"- Text source rows: `{summary['text_source_rows']:,}`",
        f"- Text rows: `{summary['text_rows']:,}`",
        f"- Skip rows: `{summary['skip_rows']:,}`",
        f"- PAC filings/events: `{summary['pac_filings']:,}` / `{summary['pac_rows']:,}`",
        f"- Error rows: `{summary['error_rows']:,}`",
        f"- Parent missing filings: `{summary['parent_missing_filings']:,}`",
        "",
    ]
    for title, key in (("Document Roles", "document_roles"), ("Text Kinds", "text_kinds"), ("Skip Reasons", "skip_reasons"), ("Content Formats", "content_formats")):
        lines.extend([f"## {title}", "", "| Value | Rows |", "| --- | ---: |"])
        for value, count in sorted((summary.get(key) or {}).items(), key=lambda item: item[1], reverse=True)[:50]:
            lines.append(f"| `{value}` | {int(count):,} |")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def failed_archive_result(archive: Path, error: str) -> dict[str, Any]:
    return {
        "archive_date": archive_date_from_name(archive.name).isoformat(),
        "archive_path": str(archive),
        "status": "failed",
        "members": 0,
        "filings": 0,
        "documents": 0,
        "document_rows": 0,
        "text_source_rows": 0,
        "text_rows": 0,
        "skip_rows": 0,
        "error_rows": 1,
        "errors": [{"archive_path": str(archive), "error_type": "archive_worker_error", "error": error}],
        "samples": [],
        "part_files": [],
    }


def add_error(stats: dict[str, Any], archive_date: str, member_name: str, cik: str, accession: str, error_type: str, error: str) -> None:
    stats["errors"].append(
        {
            "archive_date": archive_date,
            "archive_path": stats.get("archive_path", ""),
            "source_archive_member": member_name,
            "cik": cik,
            "accession_number": accession,
            "error_type": error_type,
            "error": error,
        }
    )


def query_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, str]]:
    text = client.execute(sql.strip())
    lines = [line for line in text.splitlines() if line]
    if not lines:
        return []
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def document_payload(block: str) -> str:
    match = re.search(r"<TEXT>\s*(.*)", block, flags=re.S | re.I)
    payload = match.group(1) if match else ""
    return re.sub(r"</TEXT>\s*$", "", payload, flags=re.S | re.I).strip()


def header_value(text: str, label: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.*?)\s*$", flags=re.I | re.M)
    match = pattern.search(text)
    return clean_text_field(match.group(1)) if match else ""


def tag_value(text: str, tag: str) -> str:
    match = re.search(rf"<{re.escape(tag)}>\s*([^\n\r<]+)", text, flags=re.I)
    return clean_text_field(match.group(1)) if match else ""


def all_tag_values(text: str, tag: str) -> list[str]:
    return [clean_text_field(match.group(1)) for match in re.finditer(rf"<{re.escape(tag)}>\s*([^\n\r<]+)", text, flags=re.I)]


def extract_submission_cik(header_text: str) -> str:
    preferred_blocks = ("ISSUER", "FILER", "REPORTING-OWNER")
    for block_name in preferred_blocks:
        block_match = re.search(rf"<{block_name}>\s*(.*?)(?=</{block_name}>|<(?:ISSUER|FILER|REPORTING-OWNER|DOCUMENT)>|\Z)", header_text, flags=re.I | re.S)
        if not block_match:
            continue
        cik = tag_value(block_match.group(1), "CIK")
        if cik:
            return normalize_cik(cik)
    cik = header_value(header_text, "CENTRAL INDEX KEY") or tag_value(header_text, "CENTRAL-INDEX-KEY")
    if cik:
        return normalize_cik(cik)
    company_match = re.search(r"<COMPANY-DATA>\s*(.*?)(?=</COMPANY-DATA>|<(?:BUSINESS-ADDRESS|MAIL-ADDRESS|FILING-VALUES|DOCUMENT)>|\Z)", header_text, flags=re.I | re.S)
    if company_match:
        return normalize_cik(tag_value(company_match.group(1), "CIK"))
    return normalize_cik(tag_value(header_text, "CIK"))


def extract_submission_company_name(header_text: str) -> str:
    for block_name in ("ISSUER", "FILER", "REPORTING-OWNER"):
        block_match = re.search(rf"<{block_name}>\s*(.*?)(?=</{block_name}>|<(?:ISSUER|FILER|REPORTING-OWNER|DOCUMENT)>|\Z)", header_text, flags=re.I | re.S)
        if not block_match:
            continue
        name = tag_value(block_match.group(1), "CONFORMED-NAME")
        if name:
            return name
    return header_value(header_text, "COMPANY CONFORMED NAME") or tag_value(header_text, "CONFORMED-NAME")


def extract_submission_items(header_text: str) -> str:
    values = all_tag_values(header_text, "ITEMS")
    if values:
        return "; ".join(sorted(set(value for value in values if value)))
    return header_value(header_text, "ITEM INFORMATION")


def parse_sec_date_value(value: str | None) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def accepted_timestamp_for_missing_parent(filing: dict[str, Any], archive_date: date) -> tuple[str, str]:
    accepted = parse_acceptance_datetime(filing.get("acceptance_datetime_raw"))
    if accepted:
        return accepted.replace("T", " ").removesuffix("Z"), "archive_acceptance_datetime"
    filing_date = str(filing.get("filing_date") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", filing_date):
        return f"{filing_date} 00:00:00.000000000", "archive_filing_date_midnight"
    return f"{archive_date.isoformat()} 00:00:00.000000000", "archive_date_midnight"


def first_primary_document_name(documents: list[dict[str, Any]]) -> str:
    if not documents:
        return ""
    for document in documents:
        if int(document.get("sequence_number") or 0) == 1 and document.get("document_name"):
            return str(document["document_name"])
    for document in documents:
        if document.get("document_name"):
            return str(document["document_name"])
    return ""


def decode_sec_bytes(raw: bytes) -> str:
    decoded = raw.decode("utf-8", errors="replace")
    if decoded.count("\ufffd") > 20:
        fallback = raw.decode("cp1252", errors="replace")
        if fallback.count("\ufffd") < decoded.count("\ufffd"):
            return fallback
    return decoded


def clean_text_field(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def clean_label(value: str | None) -> str:
    return clean_text_field(value).replace("\x00", "")


def clean_filename(value: str | None) -> str:
    text = clean_text_field(value)
    text = text.replace("\\", "/").split("/")[-1]
    return text[:512] if text else ""


def normalize_cik(value: str | None) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits.zfill(10) if digits else ""


def normalize_accession(value: str | None) -> str:
    text = clean_text_field(value)
    digits = re.sub(r"\D+", "", text)
    if len(digits) == 18:
        return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"
    return text


def file_extension(filename: str) -> str:
    suffix = Path(filename.lower()).suffix
    return suffix[1:] if suffix else ""


def mime_type_for_format(content_format: str) -> str | None:
    return {
        "html": "text/html",
        "plain_text": "text/plain",
        "xml": "application/xml",
        "xbrl": "application/xml",
        "pdf": "application/pdf",
        "image": "image/*",
        "spreadsheet": "application/vnd.ms-excel",
        "json": "application/json",
        "archive": "application/zip",
    }.get(content_format)


def build_document_url(parent: FilingParent, document_name: str) -> str:
    if parent.primary_document and document_name == parent.primary_document and parent.primary_document_url:
        return parent.primary_document_url
    if not document_name:
        return ""
    return sec_document_url(parent.cik, parent.accession_number_compact, document_name)


def sec_filing_detail_url(cik: str, accession_compact: str) -> str:
    cik_int = str(int(cik)) if cik else ""
    if not cik_int or not accession_compact:
        return ""
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/"


def sec_document_url(cik: str, accession_compact: str, document_name: str) -> str:
    base = sec_filing_detail_url(cik, accession_compact)
    if not base or not document_name:
        return ""
    return base + document_name


def parse_int(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_binary_like(text: str) -> bool:
    sample = text[:4000]
    if not sample:
        return False
    controls = sum(1 for char in sample if ord(char) < 32 and char not in "\n\r\t")
    return controls / max(1, len(sample)) > 0.05


def has_mojibake(text: str) -> bool:
    sample = text[:10000]
    return any(token in sample for token in ("Ã", "Â", "â€™", "â€œ", "â€�", "â€“", "â€”"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def deterministic_id(*parts: str) -> str:
    return hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def archive_date_from_name(name: str) -> date:
    match = re.match(r"(\d{8})\.nc\.tar\.gz$", name)
    if not match:
        raise ValueError(f"invalid SEC archive name: {name}")
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def validate_date(value: str, label: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"{label} must be YYYY-MM-DD: {value!r}") from exc


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def merge_counter(target: dict[str, int], values: dict[str, int]) -> None:
    for key, value in values.items():
        target[key] = int(target.get(key, 0)) + int(value or 0)


def secret_keys() -> list[str]:
    return [
        "SEC_CLICKHOUSE_URL",
        "SEC_CLICKHOUSE_USER",
        "SEC_CLICKHOUSE_PASSWORD",
        "QMD_CLICKHOUSE_URL",
        "QMD_CLICKHOUSE_USER",
        "QMD_CLICKHOUSE_PASSWORD",
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_USER",
        "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
    ]


def print_header(args: argparse.Namespace, run_root: Path, source_run_id: str, loaded_env: list[Path], archives: list[Path]) -> None:
    print("=" * 96, flush=True)
    print("SEC filing text extract parts", flush=True)
    print(f"source_run_id={source_run_id}", flush=True)
    print(f"archive_root={args.archive_root_win}", flush=True)
    print(f"date_range=[{args.start_date}, {args.end_date}) archives={len(archives):,}", flush=True)
    print(f"workers={max(1, args.archive_workers)} run_root={run_root}", flush=True)
    print("loaded_env_files=" + json.dumps([str(item) for item in loaded_env]), flush=True)
    print("secret_status=" + json.dumps(secret_status(secret_keys()), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def default_sec_clickhouse_url() -> str:
    return os.environ.get("SEC_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_sec_clickhouse_user() -> str:
    return os.environ.get("SEC_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_sec_clickhouse_password() -> str:
    return (
        os.environ.get("SEC_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


if __name__ == "__main__":
    main()
