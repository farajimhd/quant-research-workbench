from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import sys
import time
import unicodedata
import warnings
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import parse


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from pipelines.news.benzinga.news_benzinga_normalize import (  # noqa: E402
    BENZINGA_NORMALIZER_VERSION,
    NewsExtractionOptions,
    compact_json,
    content_flags,
    looks_like_pdf_url,
    normalize_benzinga_payload,
    normalize_text,
    stable_hash,
    truncate_text,
)
from pipelines.news.benzinga.news_benzinga_url_extract import extract_row as extract_downloaded_url_row  # noqa: E402
from pipelines.news.benzinga.news_benzinga_url_inventory import (  # noqa: E402
    ALT_RAW_ROOT_WIN,
    DEFAULT_RAW_ROOT_WIN,
    LEGACY_RAW_ROOT_WIN,
)


DEFAULT_FETCH_PLAN_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_fetch_plan")
DEFAULT_DOWNLOAD_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_download")
DEFAULT_EXTRACTION_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_extraction")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_normalized_rows")
DEFAULT_TEXT_LIMIT_CHARS = 24_000
DOWNLOADABLE_ACTIONS = {"fetch_html", "fetch_pdf", "fetch_text", "resolve_redirect", "sec_handler"}
WORKER_PROCESS_CONFIGURED = False
EVENT_TABLE_COLUMNS = [
    "provider",
    "provider_article_id",
    "canonical_news_id",
    "published_date",
    "published_at_utc",
    "published_raw",
    "last_updated_at_utc",
    "last_updated_raw",
    "downloaded_at_utc",
    "provider_delay_ns",
    "title",
    "normalized_title",
    "teaser",
    "text_hash",
    "article_url",
    "article_url_domain",
    "author",
    "tickers",
    "channels",
    "provider_tags",
    "image_urls",
    "has_body",
    "is_title_only",
    "has_external_text",
    "has_pdf",
    "content_quality_flags",
    "external_fetch_status",
    "external_fetch_error",
    "pdf_extract_status",
    "pdf_extract_error",
    "raw_artifact_path",
    "raw_payload_hash",
    "normalizer_version",
    "updated_at_utc",
]
EVENT_TABLE_STRUCTURE = [
    "provider String",
    "provider_article_id String",
    "canonical_news_id String",
    "published_date Date",
    "published_at_utc DateTime64(9, 'UTC')",
    "published_raw String",
    "last_updated_at_utc Nullable(DateTime64(9, 'UTC'))",
    "last_updated_raw String",
    "downloaded_at_utc DateTime64(9, 'UTC')",
    "provider_delay_ns Nullable(Int64)",
    "title String",
    "normalized_title String",
    "teaser String",
    "text_hash String",
    "article_url String",
    "article_url_domain String",
    "author String",
    "tickers Array(String)",
    "channels Array(String)",
    "provider_tags Array(String)",
    "image_urls Array(String)",
    "has_body UInt8",
    "is_title_only UInt8",
    "has_external_text UInt8",
    "has_pdf UInt8",
    "content_quality_flags Array(String)",
    "external_fetch_status String",
    "external_fetch_error String",
    "pdf_extract_status String",
    "pdf_extract_error String",
    "raw_artifact_path String",
    "raw_payload_hash String",
    "normalizer_version String",
    "updated_at_utc DateTime64(9, 'UTC')",
]
TEXT_TABLE_COLUMNS = [
    "canonical_news_id",
    "provider_article_id",
    "published_date",
    "published_at_utc",
    "text_kind",
    "text",
    "text_hash",
    "text_chars",
    "text_bytes",
    "source_count",
    "normalizer_version",
    "updated_at_utc",
]
TEXT_TABLE_STRUCTURE = [
    "canonical_news_id String",
    "provider_article_id String",
    "published_date Date",
    "published_at_utc DateTime64(9, 'UTC')",
    "text_kind String",
    "text String",
    "text_hash String",
    "text_chars UInt32",
    "text_bytes UInt32",
    "source_count UInt16",
    "normalizer_version String",
    "updated_at_utc DateTime64(9, 'UTC')",
]
URL_TABLE_COLUMNS = [
    "canonical_news_id",
    "provider_article_id",
    "published_date",
    "published_at_utc",
    "url_hash",
    "url",
    "registered_domain",
    "url_kind",
    "url_source",
    "url_ordinal",
    "final_action",
    "resolved_action",
    "http_status",
    "content_type",
    "content_length",
    "is_downloadable",
    "is_attached",
    "artifact_path",
    "artifact_sha256",
    "extraction_method",
    "extraction_quality",
    "extracted_text_chars",
    "extracted_text_hash",
    "normalizer_version",
    "updated_at_utc",
]
URL_TABLE_STRUCTURE = [
    "canonical_news_id String",
    "provider_article_id String",
    "published_date Date",
    "published_at_utc DateTime64(9, 'UTC')",
    "url_hash String",
    "url String",
    "registered_domain String",
    "url_kind String",
    "url_source String",
    "url_ordinal UInt16",
    "final_action String",
    "resolved_action String",
    "http_status UInt16",
    "content_type String",
    "content_length UInt64",
    "is_downloadable UInt8",
    "is_attached UInt8",
    "artifact_path String",
    "artifact_sha256 String",
    "extraction_method String",
    "extraction_quality String",
    "extracted_text_chars UInt32",
    "extracted_text_hash String",
    "normalizer_version String",
    "updated_at_utc DateTime64(9, 'UTC')",
]
ATTACHMENT_TABLE_COLUMNS = [
    "canonical_news_id",
    "provider_article_id",
    "published_date",
    "published_at_utc",
    "url_hash",
    "url",
    "registered_domain",
    "attachment_kind",
    "artifact_path",
    "artifact_sha256",
    "content_type",
    "content_length",
    "http_status",
    "extraction_method",
    "extraction_quality",
    "extracted_text_chars",
    "extracted_text_hash",
    "pdf_page_count",
    "quality_flags",
    "downloaded_at_utc",
    "normalizer_version",
    "updated_at_utc",
]
ATTACHMENT_TABLE_STRUCTURE = [
    "canonical_news_id String",
    "provider_article_id String",
    "published_date Date",
    "published_at_utc DateTime64(9, 'UTC')",
    "url_hash String",
    "url String",
    "registered_domain String",
    "attachment_kind String",
    "artifact_path String",
    "artifact_sha256 String",
    "content_type String",
    "content_length UInt64",
    "http_status UInt16",
    "extraction_method String",
    "extraction_quality String",
    "extracted_text_chars UInt32",
    "extracted_text_hash String",
    "pdf_page_count UInt32",
    "quality_flags Array(String)",
    "downloaded_at_utc Nullable(DateTime64(9, 'UTC'))",
    "normalizer_version String",
    "updated_at_utc DateTime64(9, 'UTC')",
]
NEWS_DATASET_SPECS = {
    "event": {
        "table": "benzinga_news_event_v1",
        "columns": EVENT_TABLE_COLUMNS,
        "structure": EVENT_TABLE_STRUCTURE,
        "prefix": "benzinga_news_event_part",
    },
    "text": {
        "table": "benzinga_news_text_v1",
        "columns": TEXT_TABLE_COLUMNS,
        "structure": TEXT_TABLE_STRUCTURE,
        "prefix": "benzinga_news_text_part",
    },
    "url": {
        "table": "benzinga_news_url_v1",
        "columns": URL_TABLE_COLUMNS,
        "structure": URL_TABLE_STRUCTURE,
        "prefix": "benzinga_news_url_part",
    },
    "attachment": {
        "table": "benzinga_news_attachment_v1",
        "columns": ATTACHMENT_TABLE_COLUMNS,
        "structure": ATTACHMENT_TABLE_STRUCTURE,
        "prefix": "benzinga_news_attachment_part",
    },
}
NEWS_TABLE_COLUMNS = EVENT_TABLE_COLUMNS
NEWS_TABLE_STRUCTURE = EVENT_TABLE_STRUCTURE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build final DB-ready Benzinga normalized news rows from raw article JSON plus optional "
            "offline URL extraction results. This script performs no network requests."
        )
    )
    parser.add_argument("--raw-root-win", default=os.environ.get("NEWS_BENZINGA_RAW_ROOT_WIN") or "")
    parser.add_argument("--attachment-jsonl", default=os.environ.get("NEWS_BENZINGA_URL_ATTACHMENT_JSONL") or "")
    parser.add_argument("--fetch-plan-root-win", default=os.environ.get("NEWS_BENZINGA_URL_FETCH_PLAN_OUTPUT_ROOT_WIN") or str(DEFAULT_FETCH_PLAN_ROOT_WIN))
    parser.add_argument("--download-result-jsonl", default=os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_RESULT_JSONL") or "")
    parser.add_argument("--download-root-win", default=os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_ROOT_WIN") or str(DEFAULT_DOWNLOAD_ROOT_WIN))
    parser.add_argument("--extraction-result-jsonl", default=os.environ.get("NEWS_BENZINGA_URL_EXTRACTION_RESULT_JSONL") or "")
    parser.add_argument("--extraction-root-win", default=os.environ.get("NEWS_BENZINGA_URL_EXTRACTION_OUTPUT_ROOT_WIN") or str(DEFAULT_EXTRACTION_ROOT_WIN))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_NORMALIZED_ROWS_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--limit-articles", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_LIMIT_ARTICLES", "0")))
    parser.add_argument(
        "--limit-attachment-rows",
        type=int,
        default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_LIMIT_ATTACHMENT_ROWS", "0")),
        help="Optional smoke-test cap for reading attachment rows. Leave at 0 for production.",
    )
    parser.add_argument("--text-limit-chars", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_TEXT_LIMIT_CHARS", str(DEFAULT_TEXT_LIMIT_CHARS))))
    parser.add_argument("--max-enriched-text-chars-per-url", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_MAX_ENRICHED_TEXT_CHARS_PER_URL", "12000")))
    parser.add_argument("--max-enriched-urls-per-article", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_MAX_ENRICHED_URLS_PER_ARTICLE", "5")))
    parser.add_argument("--processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_PROCESSES", str(max(1, (os.cpu_count() or 4) // 2)))))
    parser.add_argument("--max-pending-futures", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_MAX_PENDING", "0")))
    parser.add_argument("--inline-extract", action=argparse.BooleanOptionalAction, default=os.environ.get("NEWS_BENZINGA_NORMALIZED_INLINE_EXTRACT", "1") != "0")
    parser.add_argument("--reuse-inline-extraction", action=argparse.BooleanOptionalAction, default=os.environ.get("NEWS_BENZINGA_NORMALIZED_REUSE_INLINE_EXTRACT", "1") != "0")
    parser.add_argument("--inline-extraction-processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_INLINE_EXTRACT_PROCESSES", "0")))
    parser.add_argument("--inline-extraction-progress-interval", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_INLINE_EXTRACT_PROGRESS_INTERVAL", "1000")))
    parser.add_argument("--max-pdf-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_MAX_PDF_BYTES", "12000000")))
    parser.add_argument("--rows-per-file", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_ROWS_PER_FILE", "100000")))
    parser.add_argument("--max-output-file-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_MAX_OUTPUT_FILE_BYTES", str(256 * 1024 * 1024))))
    parser.add_argument("--target-database", default=os.environ.get("NEWS_BENZINGA_NORMALIZED_TARGET_DATABASE", "q_live"))
    parser.add_argument("--target-table", default=os.environ.get("NEWS_BENZINGA_NORMALIZED_TARGET_TABLE", "benzinga_news_event_v1"))
    parser.add_argument("--scan-raw-root", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-extraction-result", action="store_true")
    parser.add_argument("--progress-interval", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_PROGRESS_INTERVAL", "25000")))
    parser.add_argument("--flush-interval", type=int, default=int(os.environ.get("NEWS_BENZINGA_NORMALIZED_FLUSH_INTERVAL", "1000")))
    parser.add_argument(
        "--path-prefix-map",
        action="append",
        default=[],
        help=(
            "Optional path mapping in FROM=TO form. Useful when attachment paths contain workstation "
            "D:/ paths but the script is run from the laptop over a share."
        ),
    )
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    output_root = Path(args.output_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    normalized_parts_dir = run_root / "normalized_parts"
    normalized_parts_dir.mkdir(parents=True, exist_ok=True)
    dataset_parts_dirs = {name: normalized_parts_dir / f"{name}_parts" for name in NEWS_DATASET_SPECS}
    for parts_dir in dataset_parts_dirs.values():
        parts_dir.mkdir(parents=True, exist_ok=True)
    error_path = run_root / "benzinga_news_normalized_errors.jsonl"
    attachment_summary_path = run_root / "benzinga_news_normalized_attachment_summary.jsonl"
    manifest_path = run_root / "benzinga_news_normalized_manifest.json"

    path_maps = parse_path_prefix_maps(args.path_prefix_map)
    raw_root = resolve_raw_root(args, path_maps)
    attachment_path = resolve_attachment_path(args)
    download_path = resolve_download_result_path(args)
    extraction_path = resolve_extraction_result_path(args)
    if args.require_extraction_result and not extraction_path.exists():
        raise SystemExit(f"extraction result file does not exist: {extraction_path}")

    print("=" * 96, flush=True)
    print("Benzinga normalized row build", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"raw_root={raw_root}", flush=True)
    print(f"attachment_path={attachment_path if attachment_path.exists() else 'missing'}", flush=True)
    print(f"download_path={download_path if download_path.exists() else 'missing'}", flush=True)
    print(f"extraction_path={extraction_path if extraction_path.exists() else 'missing'}", flush=True)
    print(f"scan_raw_root={args.scan_raw_root} limit_articles={args.limit_articles:,}", flush=True)
    print(f"processes={max(1, args.processes)} max_pending_futures={max(1, args.max_pending_futures or max(1, args.processes) * 4)}", flush=True)
    print(f"normalized_parts_dir={normalized_parts_dir}", flush=True)
    print(f"rows_per_file={max(1, args.rows_per_file):,} max_output_file_bytes={max(1, args.max_output_file_bytes):,}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    attachment_index = load_attachment_index(args, attachment_path, path_maps) if attachment_path.exists() else AttachmentIndex()
    enrichment_index = (
        load_enrichment_index(args, extraction_path, attachment_index)
        if extraction_path.exists() and attachment_index.url_hashes
        else {}
    )
    if args.reuse_inline_extraction and attachment_index.url_hashes:
        load_prior_inline_enrichments(args, output_root, attachment_index, enrichment_index)
    inline_extraction_stats = {}
    if args.inline_extract and attachment_index.url_hashes:
        missing_url_hashes = attachment_index.url_hashes - set(enrichment_index)
        download_index = load_download_index(args, download_path, missing_url_hashes, path_maps) if download_path.exists() and missing_url_hashes else {}
        inline_extraction_stats = run_inline_extraction(
            args=args,
            run_root=run_root,
            download_index=download_index,
            enrichment_index=enrichment_index,
            started=started,
        )
        if inline_extraction_stats.get("interrupted"):
            manifest = interrupted_manifest(
                args=args,
                run_id=run_id,
                run_root=run_root,
                raw_root=raw_root,
                attachment_path=attachment_path,
                download_path=download_path,
                extraction_path=extraction_path,
                normalized_parts_dir=normalized_parts_dir,
                error_path=error_path,
                attachment_summary_path=attachment_summary_path,
                loaded_env_files=loaded_env_files,
                path_maps=path_maps,
                attachment_index=attachment_index,
                enrichment_index=enrichment_index,
                inline_extraction_stats=inline_extraction_stats,
                started=started,
            )
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
            print("manifest_path=" + str(manifest_path), flush=True)
            print("summary=" + json.dumps(manifest, sort_keys=True), flush=True)
            return
    raw_jobs = collect_raw_jobs(args, raw_root, attachment_index, path_maps)

    counters: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    error_count = 0
    processed = 0
    written = 0

    dataset_writers = {
        name: ClickHouseJsonEachRowPartWriter(
            parts_dir=dataset_parts_dirs[name],
            file_prefix=str(spec["prefix"]),
            columns=list(spec["columns"]),
            rows_per_file=max(1, args.rows_per_file),
            max_file_bytes=max(1, args.max_output_file_bytes),
        )
        for name, spec in NEWS_DATASET_SPECS.items()
    }
    with contextlib.ExitStack() as stack:
        for writer in dataset_writers.values():
            stack.enter_context(writer)
        error_handle = stack.enter_context(error_path.open("w", encoding="utf-8"))
        summary_handle = stack.enter_context(attachment_summary_path.open("w", encoding="utf-8"))
        run_stats = run_normalization_workers(
            args=args,
            raw_jobs=raw_jobs,
            attachment_index=attachment_index,
            enrichment_index=enrichment_index,
            dataset_writers=dataset_writers,
            error_handle=error_handle,
            summary_handle=summary_handle,
            counters=counters,
            flag_counts=flag_counts,
            started=started,
        )
        processed = run_stats["processed"]
        written = run_stats["written"]
        error_count = run_stats["error_count"]

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_root": str(run_root),
        "raw_root": str(raw_root),
        "attachment_path": str(attachment_path) if attachment_path.exists() else "",
        "download_path": str(download_path) if download_path.exists() else "",
        "extraction_path": str(extraction_path) if extraction_path.exists() else "",
        "normalized_parts_dir": str(normalized_parts_dir),
        "normalized_file_glob": str(dataset_parts_dirs["event"] / f"{NEWS_DATASET_SPECS['event']['prefix']}_*.jsonl"),
        "normalized_part_files": dataset_writers["event"].part_summaries,
        "datasets": manifest_datasets(args, dataset_parts_dirs, dataset_writers),
        "clickhouse_format": "JSONEachRow",
        "clickhouse_target_database": args.target_database,
        "clickhouse_target_table": args.target_table,
        "clickhouse_columns": NEWS_TABLE_COLUMNS,
        "clickhouse_structure": ", ".join(NEWS_TABLE_STRUCTURE),
        "clickhouse_file_insert_template": clickhouse_file_insert_template(args, normalized_parts_dir),
        "error_path": str(error_path),
        "attachment_summary_path": str(attachment_summary_path),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "path_prefix_maps": [{"from": old, "to": new} for old, new in path_maps],
        "raw_jobs": len(raw_jobs),
        "rows_written": written,
        "error_count": error_count,
        "attachment_article_count": len(attachment_index.unique_article_ids),
        "attachment_lookup_key_count": len(attachment_index.by_article),
        "attachment_url_count": len(attachment_index.url_hashes),
        "enrichment_url_count": len(enrichment_index),
        "inline_extraction": inline_extraction_stats,
        "reuse_inline_extraction": bool(args.reuse_inline_extraction),
        "interrupted": bool(inline_extraction_stats.get("interrupted") or run_stats.get("interrupted")),
        "interrupted_stage": "normalization" if run_stats.get("interrupted") else "",
        "status_counts": dict(counters),
        "quality_flag_counts": dict(flag_counts),
        "text_limit_chars": args.text_limit_chars,
        "max_enriched_text_chars_per_url": args.max_enriched_text_chars_per_url,
        "max_enriched_urls_per_article": args.max_enriched_urls_per_article,
        "processes": max(1, args.processes),
        "max_pending_futures": max(1, args.max_pending_futures or max(1, args.processes) * 4),
        "rows_per_file": max(1, args.rows_per_file),
        "max_output_file_bytes": max(1, args.max_output_file_bytes),
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest_path=" + str(manifest_path), flush=True)
    print("summary=" + json.dumps(manifest, sort_keys=True), flush=True)


class AttachmentIndex:
    def __init__(self) -> None:
        self.by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.raw_jobs: dict[str, RawJob] = {}
        self.url_hashes: set[str] = set()
        self.unique_article_ids: set[str] = set()


class RawJob:
    def __init__(self, *, raw_artifact_path: Path, raw_payload_hash: str = "", provider_article_id: str = "", canonical_news_id: str = "") -> None:
        self.raw_artifact_path = raw_artifact_path
        self.raw_payload_hash = raw_payload_hash
        self.provider_article_id = provider_article_id
        self.canonical_news_id = canonical_news_id


class ClickHouseJsonEachRowPartWriter:
    def __init__(self, *, parts_dir: Path, file_prefix: str, columns: list[str], rows_per_file: int, max_file_bytes: int) -> None:
        self.parts_dir = parts_dir
        self.file_prefix = file_prefix
        self.columns = columns
        self.rows_per_file = rows_per_file
        self.max_file_bytes = max_file_bytes
        self.part_index = 0
        self.current_handle: Any | None = None
        self.current_path: Path | None = None
        self.current_rows = 0
        self.current_bytes = 0
        self.part_summaries: list[dict[str, Any]] = []

    def __enter__(self) -> ClickHouseJsonEachRowPartWriter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def write(self, row: dict[str, Any]) -> None:
        table_row = project_table_row(row, self.columns)
        payload = json.dumps(table_row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        payload_bytes = len(payload.encode("utf-8"))
        if self.current_handle is None or self.should_rotate(payload_bytes):
            self.rotate()
        assert self.current_handle is not None
        self.current_handle.write(payload)
        self.current_rows += 1
        self.current_bytes += payload_bytes
        self.update_latest_summary()

    def flush(self) -> None:
        if self.current_handle is not None:
            self.current_handle.flush()

    def close(self) -> None:
        if self.current_handle is None:
            return
        self.update_latest_summary()
        self.current_handle.flush()
        self.current_handle.close()
        self.current_handle = None

    def should_rotate(self, next_bytes: int) -> bool:
        if self.current_rows <= 0:
            return False
        if self.current_rows >= self.rows_per_file:
            return True
        return self.current_bytes + next_bytes > self.max_file_bytes

    def rotate(self) -> None:
        self.close()
        self.part_index += 1
        self.current_path = self.parts_dir / f"{self.file_prefix}_{self.part_index:06d}.jsonl"
        self.current_rows = 0
        self.current_bytes = 0
        self.current_handle = self.current_path.open("w", encoding="utf-8", newline="")
        self.part_summaries.append(
            {
                "part_index": self.part_index,
                "path": str(self.current_path),
                "format": "JSONEachRow",
                "rows": 0,
                "bytes": 0,
            }
        )

    def update_latest_summary(self) -> None:
        if not self.part_summaries:
            return
        self.part_summaries[-1]["rows"] = self.current_rows
        self.part_summaries[-1]["bytes"] = self.current_bytes


def project_table_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for column in columns:
        projected[column] = normalize_table_value(column, row.get(column))
    extra_columns = sorted(set(row) - set(columns))
    if extra_columns:
        raise ValueError(f"normalized row has non-table columns: {extra_columns}")
    return projected


def normalize_table_value(column: str, value: Any) -> Any:
    array_columns = {
        "tickers",
        "channels",
        "provider_tags",
        "image_urls",
        "links",
        "pdf_urls",
        "pdf_artifact_paths",
        "content_quality_flags",
        "quality_flags",
    }
    uint8_columns = {"has_body", "is_title_only", "has_external_text", "has_pdf", "is_downloadable", "is_attached"}
    uint_columns = {"text_chars", "text_bytes", "source_count", "url_ordinal", "http_status", "content_length", "extracted_text_chars", "pdf_page_count"}
    nullable_columns = {"last_updated_at_utc", "provider_delay_ns", "downloaded_at_utc"}
    if column in array_columns:
        return value if isinstance(value, list) else []
    if column in uint8_columns:
        return int(value or 0)
    if column in uint_columns:
        return int(value or 0)
    if column in nullable_columns and value in ("", None):
        return None
    if value is None:
        return ""
    return value


def clickhouse_file_insert_template(args: argparse.Namespace, normalized_parts_dir: Path) -> str:
    columns = ", ".join(NEWS_TABLE_COLUMNS)
    escaped_glob = sql_literal_text(str(normalized_parts_dir / "event_parts" / "benzinga_news_event_part_*.jsonl").replace("\\", "/"))
    structure = sql_literal_text(", ".join(NEWS_TABLE_STRUCTURE))
    return (
        f"INSERT INTO {args.target_database}.{args.target_table} ({columns}) "
        f"SELECT {columns} FROM file('{escaped_glob}', 'JSONEachRow', '{structure}')"
    )


def manifest_datasets(
    args: argparse.Namespace,
    dataset_parts_dirs: dict[str, Path],
    dataset_writers: dict[str, ClickHouseJsonEachRowPartWriter],
) -> dict[str, dict[str, Any]]:
    datasets: dict[str, dict[str, Any]] = {}
    for name, spec in NEWS_DATASET_SPECS.items():
        table = args.target_table if name == "event" else str(spec["table"])
        parts_dir = dataset_parts_dirs[name]
        prefix = str(spec["prefix"])
        columns = list(spec["columns"])
        structure = list(spec["structure"])
        glob = str(parts_dir / f"{prefix}_*.jsonl")
        datasets[name] = {
            "table": table,
            "parts_dir": str(parts_dir),
            "file_glob": glob,
            "part_files": dataset_writers[name].part_summaries,
            "columns": columns,
            "structure": ", ".join(structure),
            "clickhouse_file_insert_template": dataset_clickhouse_file_insert_template(
                database=args.target_database,
                table=table,
                columns=columns,
                structure=structure,
                file_glob=glob,
            ),
            "rows_written": sum(int(part.get("rows") or 0) for part in dataset_writers[name].part_summaries),
        }
    return datasets


def dataset_clickhouse_file_insert_template(*, database: str, table: str, columns: list[str], structure: list[str], file_glob: str) -> str:
    column_sql = ", ".join(columns)
    escaped_glob = sql_literal_text(file_glob.replace("\\", "/"))
    escaped_structure = sql_literal_text(", ".join(structure))
    return f"INSERT INTO {database}.{table} ({column_sql}) SELECT {column_sql} FROM file('{escaped_glob}', 'JSONEachRow', '{escaped_structure}')"


def sql_literal_text(value: str) -> str:
    return value.replace("'", "''")


def parse_path_prefix_maps(values: list[str]) -> list[tuple[str, str]]:
    maps: list[tuple[str, str]] = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--path-prefix-map must use FROM=TO form: {value}")
        old, new = value.split("=", 1)
        if old:
            maps.append((normalize_path_text(old), normalize_path_text(new)))
    workstation_share = Path("//DESKTOP-SAAI85T/Workstation-D")
    if workstation_share.exists():
        maps.append(("D:/", str(workstation_share).replace("\\", "/").rstrip("/") + "/"))
    return maps


def normalize_path_text(value: str) -> str:
    return str(value or "").replace("\\", "/")


def apply_path_maps(value: str, path_maps: list[tuple[str, str]]) -> Path:
    text = normalize_path_text(value)
    candidate = Path(text)
    if candidate.exists():
        return candidate
    lower = text.lower()
    for old, new in path_maps:
        old_norm = normalize_path_text(old)
        if lower.startswith(old_norm.lower()):
            mapped = new.rstrip("/") + "/" + text[len(old_norm) :].lstrip("/")
            mapped_path = Path(mapped)
            if mapped_path.exists():
                return mapped_path
    return candidate


def resolve_raw_root(args: argparse.Namespace, path_maps: list[tuple[str, str]]) -> Path:
    candidates: list[str] = []
    if args.raw_root_win:
        candidates.append(args.raw_root_win)
    candidates.extend([str(DEFAULT_RAW_ROOT_WIN), str(ALT_RAW_ROOT_WIN), str(LEGACY_RAW_ROOT_WIN)])
    for candidate in candidates:
        path = apply_path_maps(candidate, path_maps)
        if path.exists():
            return path
    return apply_path_maps(candidates[0], path_maps)


def resolve_attachment_path(args: argparse.Namespace) -> Path:
    explicit = str(args.attachment_jsonl or "").strip()
    if explicit:
        return Path(explicit)
    root = Path(args.fetch_plan_root_win)
    manifests = sorted(root.glob("*/news_url_fetch_plan_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidate = Path(manifest.get("attachment_path") or "")
            if candidate.exists():
                return candidate
        except Exception:  # noqa: BLE001
            continue
    latest = sorted(root.glob("*/news_url_fetch_plan_attachments.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if latest:
        return latest[0]
    return root / "news_url_fetch_plan_attachments.jsonl"


def resolve_download_result_path(args: argparse.Namespace) -> Path:
    explicit = str(args.download_result_jsonl or "").strip()
    if explicit:
        return Path(explicit)
    root = Path(args.download_root_win)
    manifests = sorted(root.glob("*/news_url_download_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidate = Path(manifest.get("result_path") or "")
            if candidate.exists():
                return candidate
        except Exception:  # noqa: BLE001
            continue
    latest = sorted(root.glob("*/news_url_download_result.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if latest:
        return latest[0]
    return root / "news_url_download_result.jsonl"


def resolve_extraction_result_path(args: argparse.Namespace) -> Path:
    explicit = str(args.extraction_result_jsonl or "").strip()
    if explicit:
        return Path(explicit)
    root = Path(args.extraction_root_win)
    manifests = sorted(root.glob("*/news_url_extraction_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidate = Path(manifest.get("result_path") or "")
            if candidate.exists():
                return candidate
        except Exception:  # noqa: BLE001
            continue
    latest = sorted(root.glob("*/news_url_extraction_result.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if latest:
        return latest[0]
    return root / "news_url_extraction_result.jsonl"


def load_attachment_index(args: argparse.Namespace, path: Path, path_maps: list[tuple[str, str]]) -> AttachmentIndex:
    index = AttachmentIndex()
    started = time.perf_counter()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if args.limit_attachment_rows and line_number > args.limit_attachment_rows:
                break
            if not line.strip():
                continue
            row = json.loads(line)
            raw_path = apply_path_maps(str(row.get("raw_artifact_path") or ""), path_maps)
            row["resolved_raw_artifact_path"] = str(raw_path)
            for key in attachment_keys(row):
                index.by_article[key].append(row)
            article_id = attachment_article_id(row)
            if article_id:
                index.unique_article_ids.add(article_id)
            url_hash = str(row.get("url_hash") or "")
            if url_hash:
                index.url_hashes.add(url_hash)
            raw_key = str(raw_path)
            if raw_key and raw_key not in index.raw_jobs:
                index.raw_jobs[raw_key] = RawJob(
                    raw_artifact_path=raw_path,
                    raw_payload_hash=str(row.get("raw_payload_hash") or ""),
                    provider_article_id=str(row.get("provider_article_id") or ""),
                    canonical_news_id=str(row.get("canonical_news_id") or ""),
                )
            if line_number % 1_000_000 == 0:
                print(
                    f"attachments_loaded={line_number:,} unique_articles={len(index.unique_article_ids):,} "
                    f"lookup_keys={len(index.by_article):,} urls={len(index.url_hashes):,} "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    print(
        f"attachments_loaded=done unique_articles={len(index.unique_article_ids):,} "
        f"lookup_keys={len(index.by_article):,} urls={len(index.url_hashes):,} "
        f"raw_jobs={len(index.raw_jobs):,} elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return index


def attachment_article_id(row: dict[str, Any]) -> str:
    provider_id = str(row.get("provider_article_id") or "")
    if provider_id:
        return f"provider:{provider_id}"
    canonical = str(row.get("canonical_news_id") or "")
    if canonical:
        return f"canonical:{canonical}"
    raw_path = str(row.get("resolved_raw_artifact_path") or row.get("raw_artifact_path") or "")
    return f"raw:{normalize_path_text(raw_path)}" if raw_path else ""


def attachment_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    provider_id = str(row.get("provider_article_id") or "")
    canonical = str(row.get("canonical_news_id") or "")
    raw_path = str(row.get("resolved_raw_artifact_path") or row.get("raw_artifact_path") or "")
    if provider_id:
        keys.append(f"provider:{provider_id}")
    if canonical:
        keys.append(f"canonical:{canonical}")
    if raw_path:
        keys.append(f"raw:{normalize_path_text(raw_path)}")
    return dedupe_strings(keys)


def load_enrichment_index(args: argparse.Namespace, path: Path, attachment_index: AttachmentIndex) -> dict[str, dict[str, Any]]:
    enrichments: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    needed = attachment_index.url_hashes
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            url_hash = str(row.get("url_hash") or "")
            if url_hash not in needed:
                continue
            text = normalize_text(str(row.get("extracted_text") or ""))
            if not text or row.get("status") == "failed":
                continue
            row["extracted_text"] = truncate_text(text, max(0, args.max_enriched_text_chars_per_url))
            enrichments[url_hash] = row
            if line_number % 500_000 == 0:
                print(f"enrichments_loaded={len(enrichments):,} lines={line_number:,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    print(f"enrichments_loaded=done rows={len(enrichments):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return enrichments


def load_prior_inline_enrichments(
    args: argparse.Namespace,
    output_root: Path,
    attachment_index: AttachmentIndex,
    enrichment_index: dict[str, dict[str, Any]],
) -> None:
    needed = attachment_index.url_hashes - set(enrichment_index)
    if not needed:
        return
    started = time.perf_counter()
    loaded = 0
    files = sorted(output_root.glob("*/benzinga_news_inline_extraction_result.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        if not needed:
            break
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    url_hash = str(row.get("url_hash") or "")
                    if url_hash not in needed:
                        continue
                    text = normalize_text(str(row.get("extracted_text") or ""))
                    if row.get("status") == "failed" or not text:
                        continue
                    row["extracted_text"] = truncate_text(text, max(0, args.max_enriched_text_chars_per_url))
                    enrichment_index[url_hash] = row
                    needed.remove(url_hash)
                    loaded += 1
                    if args.inline_extraction_progress_interval and loaded % max(1, args.inline_extraction_progress_interval * 10) == 0:
                        print(
                            f"prior_inline_enrichments_loaded={loaded:,} remaining={len(needed):,} "
                            f"elapsed={time.perf_counter() - started:.1f}s",
                            flush=True,
                        )
        except OSError as exc:
            print(f"WARN prior inline extraction file skipped path={path} exception={exc!r}", flush=True)
    print(
        f"prior_inline_enrichments_loaded=done rows={loaded:,} remaining={len(needed):,} "
        f"files={len(files):,} elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )


def load_download_index(
    args: argparse.Namespace,
    path: Path,
    needed_url_hashes: set[str],
    path_maps: list[tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    downloads: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            url_hash = str(row.get("url_hash") or "")
            if url_hash not in needed_url_hashes or row.get("status") != "downloaded":
                continue
            artifact_path = apply_path_maps(str(row.get("artifact_path") or ""), path_maps)
            if not artifact_path.exists():
                continue
            row["artifact_path"] = str(artifact_path)
            downloads[url_hash] = row
            if args.inline_extraction_progress_interval and len(downloads) % max(1, args.inline_extraction_progress_interval * 10) == 0:
                print(
                    f"download_index_loaded={len(downloads):,} lines={line_number:,} "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    print(f"download_index_loaded=done rows={len(downloads):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return downloads


def run_inline_extraction(
    *,
    args: argparse.Namespace,
    run_root: Path,
    download_index: dict[str, dict[str, Any]],
    enrichment_index: dict[str, dict[str, Any]],
    started: float,
) -> dict[str, Any]:
    result_path = run_root / "benzinga_news_inline_extraction_result.jsonl"
    error_path = run_root / "benzinga_news_inline_extraction_errors.jsonl"
    rows = list(download_index.values())
    if not rows:
        print("inline_extraction=skipped rows=0", flush=True)
        return {"rows": 0, "processed": 0, "succeeded": 0, "failed": 0, "result_path": str(result_path), "error_path": str(error_path)}

    worker_count = max(1, args.inline_extraction_processes or args.processes)
    max_pending = max(worker_count, args.max_pending_futures or worker_count * 4)
    processed = 0
    succeeded = 0
    failed = 0
    submitted = 0
    interrupted = False
    pending_count_at_shutdown = 0
    cancelled_count = 0
    counters: Counter[str] = Counter()
    print(f"inline_extraction=start rows={len(rows):,} processes={worker_count} max_pending_futures={max_pending}", flush=True)
    with result_path.open("w", encoding="utf-8") as result_handle, error_path.open("w", encoding="utf-8") as error_handle:
        if worker_count == 1:
            try:
                for row in rows:
                    extracted = extract_inline_worker(row, args.max_enriched_text_chars_per_url, args.max_pdf_bytes)
                    processed, succeeded, failed = handle_inline_extraction_result(
                        extracted,
                        enrichment_index,
                        result_handle,
                        error_handle,
                        counters,
                        processed,
                        succeeded,
                        failed,
                    )
                    submitted += 1
                    if args.inline_extraction_progress_interval and processed % args.inline_extraction_progress_interval == 0:
                        print_inline_progress(processed, len(rows), succeeded, failed, counters, started)
            except KeyboardInterrupt:
                interrupted = True
                pending_count_at_shutdown = 0
                print_inline_interrupt(processed, len(rows), pending_count_at_shutdown, cancelled_count, started)
        else:
            row_iter = iter(rows)
            pending: set[concurrent.futures.Future[dict[str, Any]]] = set()
            executor = concurrent.futures.ProcessPoolExecutor(max_workers=worker_count, initializer=configure_worker_process)

            def submit_until_capacity() -> None:
                nonlocal submitted
                while len(pending) < max_pending:
                    try:
                        next_row = next(row_iter)
                    except StopIteration:
                        return
                    pending.add(executor.submit(extract_inline_worker, next_row, args.max_enriched_text_chars_per_url, args.max_pdf_bytes))
                    submitted += 1

            try:
                submit_until_capacity()
                while pending:
                    done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                    for future in done:
                        try:
                            extracted = future.result()
                        except Exception as exc:  # noqa: BLE001
                            extracted = {"status": "failed", "status_reason": "worker_failed", "error_type": type(exc).__name__, "error_message": repr(exc)}
                        processed, succeeded, failed = handle_inline_extraction_result(
                            extracted,
                            enrichment_index,
                            result_handle,
                            error_handle,
                            counters,
                            processed,
                            succeeded,
                            failed,
                        )
                    submit_until_capacity()
                    if args.inline_extraction_progress_interval and processed % args.inline_extraction_progress_interval == 0:
                        print_inline_progress(processed, len(rows), succeeded, failed, counters, started)
            except KeyboardInterrupt:
                interrupted = True
                pending_count_at_shutdown = len(pending)
                for future in pending:
                    if future.cancel():
                        cancelled_count += 1
                print_inline_interrupt(processed, len(rows), pending_count_at_shutdown, cancelled_count, started)
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True, cancel_futures=False)
    print_inline_progress(processed, len(rows), succeeded, failed, counters, started)
    return {
        "rows": len(rows),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "submitted": submitted,
        "interrupted": interrupted,
        "pending_count_at_shutdown": pending_count_at_shutdown,
        "cancelled_count": cancelled_count,
        "status_counts": dict(counters),
        "result_path": str(result_path),
        "error_path": str(error_path),
    }


def extract_inline_worker(row: dict[str, Any], max_text_chars: int, max_pdf_bytes: int) -> dict[str, Any]:
    configure_worker_process()
    return extract_downloaded_url_row(row, max_text_chars, max_pdf_bytes)


def configure_worker_process() -> None:
    global WORKER_PROCESS_CONFIGURED
    if WORKER_PROCESS_CONFIGURED:
        return
    WORKER_PROCESS_CONFIGURED = True
    warnings.filterwarnings("ignore", message="tzname .* identified but not understood.*")
    for module_name in ("pymupdf", "fitz"):
        try:
            module = __import__(module_name)
        except Exception:  # noqa: BLE001
            continue
        tools = getattr(module, "TOOLS", None)
        for method_name in ("mupdf_display_errors", "mupdf_display_warnings"):
            method = getattr(tools, method_name, None)
            if method is not None:
                with contextlib.suppress(Exception):
                    method(False)


def handle_inline_extraction_result(
    row: dict[str, Any],
    enrichment_index: dict[str, dict[str, Any]],
    result_handle: Any,
    error_handle: Any,
    counters: Counter[str],
    processed: int,
    succeeded: int,
    failed: int,
) -> tuple[int, int, int]:
    processed += 1
    status = str(row.get("status") or "unknown")
    counters[status] += 1
    text = normalize_text(str(row.get("extracted_text") or ""))
    if status != "failed" and text:
        enrichment_index[str(row.get("url_hash") or "")] = row
        result_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
        succeeded += 1
    else:
        error_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
        failed += 1
    return processed, succeeded, failed


def print_inline_progress(processed: int, total: int, succeeded: int, failed: int, counters: Counter[str], started: float) -> None:
    elapsed = time.perf_counter() - started
    rate = processed / elapsed if elapsed > 0 else 0.0
    print(
        f"inline_extraction=processed {processed:,}/{total:,} succeeded={succeeded:,} "
        f"failed={failed:,} rate={rate:.1f}/s statuses={dict(counters)} elapsed={elapsed:.1f}s",
        flush=True,
    )


def print_inline_interrupt(processed: int, total: int, pending_count: int, cancelled_count: int, started: float) -> None:
    print(
        f"inline_extraction_interrupt=received processed={processed:,}/{total:,} "
        f"pending={pending_count:,} cancelled={cancelled_count:,} elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )


def print_normalization_interrupt(processed: int, total: int, pending_count: int, cancelled_count: int, started: float) -> None:
    print(
        f"normalization_interrupt=received processed={processed:,}/{total:,} "
        f"pending={pending_count:,} cancelled={cancelled_count:,} elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )


def interrupted_manifest(
    *,
    args: argparse.Namespace,
    run_id: str,
    run_root: Path,
    raw_root: Path,
    attachment_path: Path,
    download_path: Path,
    extraction_path: Path,
    normalized_parts_dir: Path,
    error_path: Path,
    attachment_summary_path: Path,
    loaded_env_files: list[Path],
    path_maps: list[tuple[str, str]],
    attachment_index: AttachmentIndex,
    enrichment_index: dict[str, dict[str, Any]],
    inline_extraction_stats: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_root": str(run_root),
        "raw_root": str(raw_root),
        "attachment_path": str(attachment_path) if attachment_path.exists() else "",
        "download_path": str(download_path) if download_path.exists() else "",
        "extraction_path": str(extraction_path) if extraction_path.exists() else "",
        "normalized_parts_dir": str(normalized_parts_dir),
        "normalized_file_glob": str(normalized_parts_dir / "benzinga_news_normalized_part_*.jsonl"),
        "normalized_part_files": [],
        "clickhouse_format": "JSONEachRow",
        "clickhouse_target_database": args.target_database,
        "clickhouse_target_table": args.target_table,
        "clickhouse_columns": NEWS_TABLE_COLUMNS,
        "clickhouse_structure": ", ".join(NEWS_TABLE_STRUCTURE),
        "clickhouse_file_insert_template": clickhouse_file_insert_template(args, normalized_parts_dir),
        "error_path": str(error_path),
        "attachment_summary_path": str(attachment_summary_path),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "path_prefix_maps": [{"from": old, "to": new} for old, new in path_maps],
        "raw_jobs": 0,
        "rows_written": 0,
        "error_count": 0,
        "attachment_article_count": len(attachment_index.unique_article_ids),
        "attachment_lookup_key_count": len(attachment_index.by_article),
        "attachment_url_count": len(attachment_index.url_hashes),
        "enrichment_url_count": len(enrichment_index),
        "inline_extraction": inline_extraction_stats,
        "reuse_inline_extraction": bool(args.reuse_inline_extraction),
        "interrupted": True,
        "interrupted_stage": "inline_extraction",
        "status_counts": {},
        "quality_flag_counts": {},
        "text_limit_chars": args.text_limit_chars,
        "max_enriched_text_chars_per_url": args.max_enriched_text_chars_per_url,
        "max_enriched_urls_per_article": args.max_enriched_urls_per_article,
        "processes": max(1, args.processes),
        "max_pending_futures": max(1, args.max_pending_futures or max(1, args.processes) * 4),
        "rows_per_file": max(1, args.rows_per_file),
        "max_output_file_bytes": max(1, args.max_output_file_bytes),
        "wall_seconds": round(time.perf_counter() - started, 3),
    }


def collect_raw_jobs(args: argparse.Namespace, raw_root: Path, attachment_index: AttachmentIndex, path_maps: list[tuple[str, str]]) -> list[RawJob]:
    jobs: dict[str, RawJob] = dict(attachment_index.raw_jobs)
    if args.scan_raw_root:
        if not raw_root.exists():
            raise SystemExit(f"raw root does not exist: {raw_root}")
        for raw_path in raw_root.rglob("*.json"):
            key = str(raw_path)
            if key not in jobs:
                jobs[key] = RawJob(raw_artifact_path=raw_path)
            if args.limit_articles and len(jobs) >= args.limit_articles:
                break
    if not args.scan_raw_root:
        for key, job in list(jobs.items()):
            resolved = apply_path_maps(str(job.raw_artifact_path), path_maps)
            jobs[key] = RawJob(
                raw_artifact_path=resolved,
                raw_payload_hash=job.raw_payload_hash,
                provider_article_id=job.provider_article_id,
                canonical_news_id=job.canonical_news_id,
            )
    output = list(jobs.values())
    output.sort(key=lambda job: str(job.raw_artifact_path))
    if args.limit_articles:
        output = output[: max(0, args.limit_articles)]
    print(f"raw_jobs_collected={len(output):,}", flush=True)
    return output


def run_normalization_workers(
    *,
    args: argparse.Namespace,
    raw_jobs: list[RawJob],
    attachment_index: AttachmentIndex,
    enrichment_index: dict[str, dict[str, Any]],
    dataset_writers: dict[str, ClickHouseJsonEachRowPartWriter],
    error_handle: Any,
    summary_handle: Any,
    counters: Counter[str],
    flag_counts: Counter[str],
    started: float,
) -> dict[str, int]:
    worker_count = max(1, args.processes)
    max_pending = max(worker_count, args.max_pending_futures or worker_count * 4)
    processed = 0
    written = 0
    error_count = 0
    interrupted = False

    if worker_count == 1:
        try:
            for job in raw_jobs:
                attachments = job_attachments(job, attachment_index)
                enrichments = article_enrichments(attachments, enrichment_index)
                try:
                    row, summary = build_normalized_row(args, job, attachments, enrichments)
                    written += write_success(row, summary, dataset_writers, summary_handle, counters, flag_counts)
                except Exception as exc:  # noqa: BLE001
                    error_count += write_error(job, exc, error_handle, counters)
                processed += 1
                maybe_flush_and_report(args, processed, len(raw_jobs), written, error_count, dataset_writers, error_handle, summary_handle, started)
        except KeyboardInterrupt:
            interrupted = True
            print_normalization_interrupt(processed, len(raw_jobs), 0, 0, started)
        return {"processed": processed, "written": written, "error_count": error_count, "interrupted": interrupted}

    job_iter = iter(raw_jobs)
    pending: set[concurrent.futures.Future[tuple[dict[str, Any], dict[str, Any]]]] = set()
    future_jobs: dict[concurrent.futures.Future[tuple[dict[str, Any], dict[str, Any]]], RawJob] = {}
    executor = concurrent.futures.ProcessPoolExecutor(max_workers=worker_count, initializer=configure_worker_process)

    def submit_until_capacity() -> None:
        while len(pending) < max_pending:
            try:
                job = next(job_iter)
            except StopIteration:
                return
            attachments = job_attachments(job, attachment_index)
            enrichments = article_enrichments(attachments, enrichment_index)
            future = executor.submit(build_normalized_row_worker, args, job, attachments, enrichments)
            pending.add(future)
            future_jobs[future] = job

    try:
        submit_until_capacity()
        while pending:
            done, pending = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                job = future_jobs.pop(future)
                try:
                    row, summary = future.result()
                    written += write_success(row, summary, dataset_writers, summary_handle, counters, flag_counts)
                except Exception as exc:  # noqa: BLE001
                    error_count += write_error(job, exc, error_handle, counters)
                processed += 1
            submit_until_capacity()
            maybe_flush_and_report(args, processed, len(raw_jobs), written, error_count, dataset_writers, error_handle, summary_handle, started)
    except KeyboardInterrupt:
        interrupted = True
        pending_count = len(pending)
        cancelled_count = 0
        for future in pending:
            if future.cancel():
                cancelled_count += 1
        print_normalization_interrupt(processed, len(raw_jobs), pending_count, cancelled_count, started)
        executor.shutdown(wait=False, cancel_futures=True)
    else:
        executor.shutdown(wait=True, cancel_futures=False)
    return {"processed": processed, "written": written, "error_count": error_count, "interrupted": interrupted}


def build_normalized_row_worker(
    args: argparse.Namespace,
    job: RawJob,
    attachments: list[dict[str, Any]],
    enrichments: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return build_normalized_row(args, job, attachments, enrichments)


def write_success(
    row: dict[str, Any],
    summary: dict[str, Any],
    dataset_writers: dict[str, ClickHouseJsonEachRowPartWriter],
    summary_handle: Any,
    counters: Counter[str],
    flag_counts: Counter[str],
) -> int:
    dataset_writers["event"].write(build_event_row(row))
    for text_row in build_text_rows(row):
        dataset_writers["text"].write(text_row)
    for url_row in build_url_rows(row, summary):
        dataset_writers["url"].write(url_row)
    for attachment_row in build_attachment_rows(row, summary):
        dataset_writers["attachment"].write(attachment_row)
    summary_handle.write(json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
    counters["written"] += 1
    counters[str(row.get("external_fetch_status") or "unknown")] += 1
    counters[f"pdf:{row.get('pdf_extract_status') or 'unknown'}"] += 1
    for flag in row.get("content_quality_flags") or []:
        flag_counts[str(flag)] += 1
    return 1


def build_event_row(row: dict[str, Any]) -> dict[str, Any]:
    article_url = clean_url(str(row.get("article_url") or ""))
    article_kind = classify_url(article_url)
    persisted_article_url = article_url if article_kind == "provider_article" else ""
    return {
        "provider": row.get("provider") or "",
        "provider_article_id": row.get("provider_article_id") or "",
        "canonical_news_id": row.get("canonical_news_id") or "",
        "published_date": row.get("published_date") or "",
        "published_at_utc": row.get("published_at_utc") or "",
        "published_raw": row.get("published_raw") or "",
        "last_updated_at_utc": row.get("last_updated_at_utc"),
        "last_updated_raw": row.get("last_updated_raw") or "",
        "downloaded_at_utc": row.get("downloaded_at_utc") or "",
        "provider_delay_ns": row.get("provider_delay_ns"),
        "title": db_text(row.get("title") or ""),
        "normalized_title": db_text(row.get("normalized_title") or ""),
        "teaser": db_text(row.get("teaser") or ""),
        "text_hash": row.get("text_hash") or "",
        "article_url": persisted_article_url,
        "article_url_domain": registered_domain(article_url) if persisted_article_url else "",
        "author": db_text(row.get("author") or ""),
        "tickers": row.get("tickers") or [],
        "channels": row.get("channels") or [],
        "provider_tags": row.get("provider_tags") or [],
        "image_urls": row.get("image_urls") or [],
        "has_body": row.get("has_body") or 0,
        "is_title_only": row.get("is_title_only") or 0,
        "has_external_text": row.get("has_external_text") or 0,
        "has_pdf": row.get("has_pdf") or 0,
        "content_quality_flags": row.get("content_quality_flags") or [],
        "external_fetch_status": row.get("external_fetch_status") or "",
        "external_fetch_error": row.get("external_fetch_error") or "",
        "pdf_extract_status": row.get("pdf_extract_status") or "",
        "pdf_extract_error": row.get("pdf_extract_error") or "",
        "raw_artifact_path": row.get("raw_artifact_path") or "",
        "raw_payload_hash": row.get("raw_payload_hash") or "",
        "normalizer_version": row.get("normalizer_version") or "",
        "updated_at_utc": row.get("updated_at_utc") or now_clickhouse_dt64(),
    }


def build_text_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_counts = {
        "body": 1 if row.get("body_text") else 0,
        "external": max(1, int(row.get("_external_url_count") or 0)) if row.get("external_text") else 0,
        "pdf": max(1, int(row.get("_pdf_url_count") or 0)) if row.get("pdf_text") else 0,
    }
    for text_kind, source_column in [("body", "body_text"), ("external", "external_text"), ("pdf", "pdf_text")]:
        text = db_text(row.get(source_column) or "")
        if not text:
            continue
        encoded = text.encode("utf-8")
        rows.append(
            {
                "canonical_news_id": row.get("canonical_news_id") or "",
                "provider_article_id": row.get("provider_article_id") or "",
                "published_date": row.get("published_date") or "",
                "published_at_utc": row.get("published_at_utc") or "",
                "text_kind": text_kind,
                "text": text,
                "text_hash": stable_hash(text),
                "text_chars": len(text),
                "text_bytes": len(encoded),
                "source_count": source_counts[text_kind],
                "normalizer_version": row.get("normalizer_version") or "",
                "updated_at_utc": row.get("updated_at_utc") or now_clickhouse_dt64(),
            }
        )
    return rows


def build_url_rows(row: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    metadata_by_url: dict[str, dict[str, Any]] = {}
    for metadata in [*parse_json_list(summary.get("external_metadata_json")), *parse_json_list(summary.get("pdf_metadata_json"))]:
        url = clean_url(str(metadata.get("url") or metadata.get("normalized_url") or ""))
        if url:
            metadata_by_url[url] = metadata

    def add_url(url: str, source: str, ordinal: int, metadata: dict[str, Any] | None = None) -> None:
        clean = clean_url(url)
        if not clean:
            return
        kind = classify_url(clean)
        if not persist_url_kind(kind):
            return
        key = stable_hash(clean)
        if key in seen:
            return
        seen.add(key)
        meta = metadata or metadata_by_url.get(clean) or {}
        rows.append(base_url_row(row, clean, key, kind, source, ordinal, meta))

    add_url(str(row.get("article_url") or ""), "article_url", 0)
    for ordinal, url in enumerate(row.get("links") or [], start=1):
        add_url(str(url or ""), "link", ordinal)
    for ordinal, url in enumerate(row.get("pdf_urls") or [], start=1):
        add_url(str(url or ""), "pdf_url", ordinal)
    for ordinal, metadata in enumerate(metadata_by_url.values(), start=1):
        add_url(str(metadata.get("url") or metadata.get("normalized_url") or ""), "enrichment", ordinal, metadata)
    return rows


def build_attachment_rows(row: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metadata in [*parse_json_list(summary.get("external_metadata_json")), *parse_json_list(summary.get("pdf_metadata_json"))]:
        artifact_path = str(metadata.get("artifact_path") or "")
        artifact_sha256 = str(metadata.get("artifact_sha256") or "")
        extracted_text_hash = str(metadata.get("extracted_text_hash") or "")
        if not artifact_path and not artifact_sha256 and not extracted_text_hash:
            continue
        url = clean_url(str(metadata.get("url") or metadata.get("normalized_url") or ""))
        kind = classify_url(url)
        rows.append(
            {
                "canonical_news_id": row.get("canonical_news_id") or "",
                "provider_article_id": row.get("provider_article_id") or "",
                "published_date": row.get("published_date") or "",
                "published_at_utc": row.get("published_at_utc") or "",
                "url_hash": str(metadata.get("url_hash") or stable_hash(url)),
                "url": url,
                "registered_domain": str(metadata.get("domain") or registered_domain(url)),
                "attachment_kind": "pdf" if kind == "pdf" or str(metadata.get("extraction_method") or "").startswith("pdf") else "external",
                "artifact_path": artifact_path,
                "artifact_sha256": artifact_sha256,
                "content_type": db_text(metadata.get("content_type") or ""),
                "content_length": int(metadata.get("content_length") or 0),
                "http_status": int(metadata.get("http_status") or 0),
                "extraction_method": db_text(metadata.get("extraction_method") or ""),
                "extraction_quality": db_text(metadata.get("extraction_quality") or ""),
                "extracted_text_chars": int(metadata.get("extracted_text_chars") or 0),
                "extracted_text_hash": extracted_text_hash,
                "pdf_page_count": int(metadata.get("pdf_page_count") or 0),
                "quality_flags": metadata.get("quality_flags") or [],
                "downloaded_at_utc": clickhouse_dt_or_none(metadata.get("downloaded_at_utc")),
                "normalizer_version": row.get("normalizer_version") or "",
                "updated_at_utc": row.get("updated_at_utc") or now_clickhouse_dt64(),
            }
        )
    return rows


def base_url_row(row: dict[str, Any], url: str, url_hash: str, kind: str, source: str, ordinal: int, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_news_id": row.get("canonical_news_id") or "",
        "provider_article_id": row.get("provider_article_id") or "",
        "published_date": row.get("published_date") or "",
        "published_at_utc": row.get("published_at_utc") or "",
        "url_hash": str(metadata.get("url_hash") or url_hash),
        "url": url,
        "registered_domain": str(metadata.get("domain") or registered_domain(url)),
        "url_kind": kind,
        "url_source": source,
        "url_ordinal": ordinal,
        "final_action": db_text(metadata.get("final_action") or ""),
        "resolved_action": db_text(metadata.get("resolved_action") or ""),
        "http_status": int(metadata.get("http_status") or 0),
        "content_type": db_text(metadata.get("content_type") or ""),
        "content_length": int(metadata.get("content_length") or 0),
        "is_downloadable": 1 if str(metadata.get("final_action") or "") in DOWNLOADABLE_ACTIONS or kind in {"external_source", "pdf", "sec"} else 0,
        "is_attached": 1 if metadata else 0,
        "artifact_path": str(metadata.get("artifact_path") or ""),
        "artifact_sha256": str(metadata.get("artifact_sha256") or ""),
        "extraction_method": db_text(metadata.get("extraction_method") or ""),
        "extraction_quality": db_text(metadata.get("extraction_quality") or ""),
        "extracted_text_chars": int(metadata.get("extracted_text_chars") or 0),
        "extracted_text_hash": str(metadata.get("extracted_text_hash") or ""),
        "normalizer_version": row.get("normalizer_version") or "",
        "updated_at_utc": row.get("updated_at_utc") or now_clickhouse_dt64(),
    }


def parse_json_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def db_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(ch if ch in "\t\n\r" or unicodedata.category(ch)[0] != "C" else " " for ch in text)
    return normalize_text(text)


def clean_url(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def classify_url(url: str) -> str:
    clean = clean_url(url)
    if not clean:
        return "empty"
    parsed = parse.urlparse(clean)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower().rstrip("/")
    if not host:
        return "relative_or_invalid"
    if looks_like_pdf_url(clean):
        return "pdf"
    if host == "quotes.benzinga.com":
        return "benzinga_quote"
    if host == "benzinga.com" or host.endswith(".benzinga.com"):
        if path.startswith("/quote") or path.startswith("/stock") or path.startswith("/symbol"):
            return "benzinga_quote"
        if path in {"", "/"}:
            return "benzinga_navigation"
        if path.startswith("/news/") or path.startswith("/analyst-ratings/") or path.startswith("/markets/") or len(path.strip("/").split("/")) >= 2:
            return "provider_article"
        return "benzinga_navigation"
    domain = registered_domain(clean)
    if domain in {"doubleclick.net", "googlesyndication.com", "grsm.io", "feedburner.com"}:
        return "tracking"
    if domain in {"twitter.com", "x.com", "youtube.com", "youtu.be", "facebook.com", "linkedin.com"}:
        return "social"
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
        return "image"
    if domain == "sec.gov":
        return "sec"
    return "external_source"


def persist_url_kind(kind: str) -> bool:
    return kind in {"provider_article", "external_source", "pdf", "sec", "social"}


def registered_domain(url: str) -> str:
    host = (parse.urlparse(clean_url(url)).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "gov"}:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def clickhouse_dt_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            return None
    return text


def write_error(job: RawJob, exc: Exception, error_handle: Any, counters: Counter[str]) -> int:
    counters["failed"] += 1
    error_handle.write(
        json.dumps(
            {
                "raw_artifact_path": str(job.raw_artifact_path),
                "provider_article_id": job.provider_article_id,
                "canonical_news_id": job.canonical_news_id,
                "exception_type": type(exc).__name__,
                "exception": repr(exc),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 1


def maybe_flush_and_report(
    args: argparse.Namespace,
    processed: int,
    total: int,
    written: int,
    error_count: int,
    dataset_writers: dict[str, ClickHouseJsonEachRowPartWriter],
    error_handle: Any,
    summary_handle: Any,
    started: float,
) -> None:
    if args.flush_interval and processed % args.flush_interval == 0:
        for writer in dataset_writers.values():
            writer.flush()
        error_handle.flush()
        summary_handle.flush()
    if args.progress_interval and processed % args.progress_interval == 0:
        elapsed = time.perf_counter() - started
        rate = processed / elapsed if elapsed > 0 else 0.0
        print(
            f"progress=processed {processed:,}/{total:,} written={written:,} "
            f"errors={error_count:,} rate={rate:.1f}/s elapsed={elapsed:.1f}s",
            flush=True,
        )


def build_normalized_row(
    args: argparse.Namespace,
    job: RawJob,
    attachments: list[dict[str, Any]],
    enrichments: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload_text = job.raw_artifact_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise TypeError(f"raw payload was {type(payload).__name__}, expected dict")
    row = normalize_benzinga_payload(
        payload,
        raw_artifact_path=str(job.raw_artifact_path),
        raw_payload_hash=job.raw_payload_hash or stable_hash(json.dumps(payload, sort_keys=True, default=str)),
        options=NewsExtractionOptions(fetch_external=False, extract_pdfs=False, text_limit_chars=args.text_limit_chars),
        diagnostics=[],
    )
    row["updated_at_utc"] = now_clickhouse_dt64()
    row, summary = apply_enrichments(args, row, attachments, enrichments)
    return row, summary


def job_attachments(job: RawJob, attachment_index: AttachmentIndex) -> list[dict[str, Any]]:
    keys = {
        f"provider:{job.provider_article_id}",
        f"canonical:{job.canonical_news_id}",
        f"raw:{normalize_path_text(str(job.raw_artifact_path))}",
    }
    attachments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        for attachment in attachment_index.by_article.get(key, []):
            url_hash = str(attachment.get("url_hash") or "")
            if not url_hash or url_hash in seen:
                continue
            attachments.append(attachment)
            seen.add(url_hash)
    return attachments


def article_enrichments(attachments: list[dict[str, Any]], enrichment_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for attachment in attachments:
        url_hash = str(attachment.get("url_hash") or "")
        enrichment = enrichment_index.get(url_hash)
        if not enrichment:
            continue
        enriched = dict(enrichment)
        enriched["attachment_final_action"] = attachment.get("final_action") or ""
        enriched["attachment_policy_reason"] = attachment.get("policy_reason") or ""
        enriched["attachment_url_source"] = attachment.get("url_source") or ""
        enriched["attachment_url_ordinal"] = attachment.get("url_ordinal") or 0
        matched.append(enriched)
    return matched


def apply_enrichments(
    args: argparse.Namespace,
    row: dict[str, Any],
    attachments: list[dict[str, Any]],
    enrichments: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    external_texts: list[str] = []
    pdf_texts: list[str] = []
    pdf_metadata: list[dict[str, Any]] = []
    external_metadata: list[dict[str, Any]] = []
    seen_url_hashes: set[str] = set()

    for enrichment in sorted(enrichments, key=enrichment_sort_key):
        if len(seen_url_hashes) >= max(0, args.max_enriched_urls_per_article):
            break
        url_hash = str(enrichment.get("url_hash") or "")
        text = normalize_text(str(enrichment.get("extracted_text") or ""))
        if not url_hash or url_hash in seen_url_hashes or not text:
            continue
        seen_url_hashes.add(url_hash)
        action = str(enrichment.get("resolved_action") or enrichment.get("final_action") or "")
        meta = compact_enrichment_metadata(enrichment)
        if action == "fetch_pdf" or str(enrichment.get("extraction_method") or "").startswith("pdf"):
            pdf_texts.append(text)
            pdf_metadata.append(meta)
        else:
            external_texts.append(text)
            external_metadata.append(meta)

    existing_external = normalize_text(str(row.get("external_text") or ""))
    existing_pdf = normalize_text(str(row.get("pdf_text") or ""))
    external_text = truncate_text(normalize_text(" ".join([existing_external, *external_texts])), args.text_limit_chars)
    pdf_text = truncate_text(normalize_text(" ".join([existing_pdf, *pdf_texts])), args.text_limit_chars)
    full_text = truncate_text(
        normalize_text(" ".join(part for part in [row.get("title"), row.get("teaser"), row.get("body_text"), external_text, pdf_text] if part)),
        args.text_limit_chars,
    )

    pdf_urls = list(row.get("pdf_urls") or [])
    pdf_artifact_paths = list(row.get("pdf_artifact_paths") or [])
    for metadata in pdf_metadata:
        url = str(metadata.get("url") or "")
        artifact_path = str(metadata.get("artifact_path") or "")
        if url and url not in pdf_urls:
            pdf_urls.append(url)
        if artifact_path and artifact_path not in pdf_artifact_paths:
            pdf_artifact_paths.append(artifact_path)

    row["external_text"] = external_text
    row["pdf_text"] = pdf_text
    row["normalized_full_text"] = full_text
    row["text_hash"] = stable_hash(full_text)
    row["has_external_text"] = 1 if external_text else 0
    row["has_pdf"] = 1 if pdf_urls else 0
    row["is_title_only"] = 1 if not row.get("body_text") and not external_text and not pdf_text else 0
    row["pdf_urls"] = pdf_urls
    row["pdf_artifact_paths"] = pdf_artifact_paths
    row["pdf_metadata_json"] = compact_json(merge_pdf_metadata(row.get("pdf_metadata_json"), pdf_metadata))
    row["external_fetch_status"] = external_status(external_metadata, attachments)
    row["external_fetch_error"] = ""
    row["pdf_extract_status"] = pdf_status(pdf_metadata, pdf_text, attachments)
    row["pdf_extract_error"] = ""
    row["_external_url_count"] = len(external_metadata)
    row["_pdf_url_count"] = len(pdf_metadata)
    quality_flags = content_flags(
        str(row.get("body_text") or ""),
        external_text,
        pdf_text,
        pdf_urls,
        str(row.get("external_fetch_status") or ""),
        str(row.get("pdf_extract_status") or ""),
        merge_pdf_metadata("", pdf_metadata),
    )
    if row["external_fetch_status"] == "artifact_missing":
        quality_flags.append("external_artifact_missing")
    if row["pdf_extract_status"] == "artifact_missing":
        quality_flags.append("pdf_artifact_missing")
    row["content_quality_flags"] = dedupe_strings(quality_flags)
    row["normalizer_version"] = f"{BENZINGA_NORMALIZER_VERSION}+offline-url-assembly-v1"

    summary = {
        "provider_article_id": row.get("provider_article_id") or "",
        "canonical_news_id": row.get("canonical_news_id") or "",
        "raw_artifact_path": row.get("raw_artifact_path") or "",
        "attachment_url_count": len(attachments),
        "enriched_url_count": len(seen_url_hashes),
        "missing_enrichment_url_count": max(0, len(attachments) - len(seen_url_hashes)),
        "external_url_count": len(external_metadata),
        "pdf_url_count": len(pdf_metadata),
        "external_metadata_json": compact_json(external_metadata),
        "pdf_metadata_json": compact_json(pdf_metadata),
    }
    return row, summary


def article_key(row: dict[str, Any]) -> str:
    provider_id = str(row.get("provider_article_id") or "")
    if provider_id:
        return f"provider:{provider_id}"
    canonical = str(row.get("canonical_news_id") or "")
    if canonical:
        return f"canonical:{canonical}"
    raw_path = str(row.get("resolved_raw_artifact_path") or row.get("raw_artifact_path") or "")
    return f"raw:{normalize_path_text(raw_path)}"


def enrichment_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    action = str(row.get("resolved_action") or row.get("final_action") or "")
    quality = str(row.get("extraction_quality") or "")
    action_rank = 0 if action in DOWNLOADABLE_ACTIONS else 1
    quality_rank = {"good": 0, "partial": 1, "low": 2, "unknown": 3}.get(quality, 4)
    return (action_rank, quality_rank, str(row.get("url_hash") or ""))


def compact_enrichment_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "url_hash": row.get("url_hash") or "",
        "url": row.get("final_url") or row.get("normalized_url") or "",
        "normalized_url": row.get("normalized_url") or "",
        "domain": row.get("registered_domain") or row.get("domain") or "",
        "final_action": row.get("final_action") or "",
        "resolved_action": row.get("resolved_action") or "",
        "http_status": row.get("http_status") or 0,
        "content_type": row.get("content_type") or "",
        "content_length": row.get("content_length") or 0,
        "artifact_path": row.get("artifact_path") or "",
        "artifact_sha256": row.get("artifact_sha256") or "",
        "extraction_method": row.get("extraction_method") or "",
        "extraction_quality": row.get("extraction_quality") or "",
        "extracted_text_chars": row.get("extracted_text_chars") or 0,
        "extracted_text_hash": row.get("extracted_text_hash") or "",
        "pdf_page_count": row.get("pdf_page_count") or 0,
        "quality_flags": row.get("quality_flags") or [],
        "downloaded_at_utc": row.get("downloaded_at_utc") or "",
    }


def dedupe_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "")
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def merge_pdf_metadata(existing_json: Any, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    if isinstance(existing_json, str) and existing_json.strip():
        try:
            parsed = json.loads(existing_json)
            if isinstance(parsed, list):
                existing = [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            existing = []
    elif isinstance(existing_json, list):
        existing = [item for item in existing_json if isinstance(item, dict)]
    return [*existing, *additions]


def external_status(external_metadata: list[dict[str, Any]], attachments: list[dict[str, Any]]) -> str:
    if external_metadata:
        return "artifact_extracted"
    if any(str(row.get("final_action") or "") in {"fetch_html", "fetch_text", "resolve_redirect", "sec_handler"} for row in attachments):
        return "artifact_missing"
    return "not_needed"


def pdf_status(pdf_metadata: list[dict[str, Any]], pdf_text: str, attachments: list[dict[str, Any]]) -> str:
    if pdf_text and pdf_metadata:
        return "artifact_extracted"
    if pdf_metadata:
        return "metadata_only"
    if any(str(row.get("final_action") or "") == "fetch_pdf" for row in attachments):
        return "artifact_missing"
    return "not_needed"


def now_clickhouse_dt64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


if __name__ == "__main__":
    main()
