from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.mlops.news_benzinga_normalize import (  # noqa: E402
    BENZINGA_NORMALIZER_VERSION,
    NewsExtractionOptions,
    compact_json,
    content_flags,
    normalize_benzinga_payload,
    normalize_text,
    stable_hash,
    truncate_text,
)
from research.mlops.news_benzinga_url_inventory import (  # noqa: E402
    ALT_RAW_ROOT_WIN,
    DEFAULT_RAW_ROOT_WIN,
    LEGACY_RAW_ROOT_WIN,
)


DEFAULT_FETCH_PLAN_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_fetch_plan")
DEFAULT_EXTRACTION_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_extraction")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_normalized_rows")
DEFAULT_TEXT_LIMIT_CHARS = 24_000
DOWNLOADABLE_ACTIONS = {"fetch_html", "fetch_pdf", "fetch_text", "resolve_redirect", "sec_handler"}


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

    normalized_path = run_root / "benzinga_news_normalized_rows.jsonl"
    error_path = run_root / "benzinga_news_normalized_errors.jsonl"
    attachment_summary_path = run_root / "benzinga_news_normalized_attachment_summary.jsonl"
    manifest_path = run_root / "benzinga_news_normalized_manifest.json"

    path_maps = parse_path_prefix_maps(args.path_prefix_map)
    raw_root = resolve_raw_root(args, path_maps)
    attachment_path = resolve_attachment_path(args)
    extraction_path = resolve_extraction_result_path(args)
    if args.require_extraction_result and not extraction_path.exists():
        raise SystemExit(f"extraction result file does not exist: {extraction_path}")

    print("=" * 96, flush=True)
    print("Benzinga normalized row build", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"raw_root={raw_root}", flush=True)
    print(f"attachment_path={attachment_path if attachment_path.exists() else 'missing'}", flush=True)
    print(f"extraction_path={extraction_path if extraction_path.exists() else 'missing'}", flush=True)
    print(f"scan_raw_root={args.scan_raw_root} limit_articles={args.limit_articles:,}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    attachment_index = load_attachment_index(args, attachment_path, path_maps) if attachment_path.exists() else AttachmentIndex()
    enrichment_index = (
        load_enrichment_index(args, extraction_path, attachment_index)
        if extraction_path.exists() and attachment_index.url_hashes
        else {}
    )
    raw_jobs = collect_raw_jobs(args, raw_root, attachment_index, path_maps)

    counters: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    error_count = 0
    processed = 0
    written = 0

    with normalized_path.open("w", encoding="utf-8") as normalized_handle, error_path.open("w", encoding="utf-8") as error_handle, attachment_summary_path.open("w", encoding="utf-8") as summary_handle:
        for job in raw_jobs:
            processed += 1
            try:
                row, summary = build_normalized_row(args, job, attachment_index, enrichment_index)
                normalized_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                summary_handle.write(json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                written += 1
                counters["written"] += 1
                counters[str(row.get("external_fetch_status") or "unknown")] += 1
                counters[f"pdf:{row.get('pdf_extract_status') or 'unknown'}"] += 1
                for flag in row.get("content_quality_flags") or []:
                    flag_counts[str(flag)] += 1
            except Exception as exc:  # noqa: BLE001
                error_count += 1
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
            if args.flush_interval and processed % args.flush_interval == 0:
                normalized_handle.flush()
                error_handle.flush()
                summary_handle.flush()
            if args.progress_interval and processed % args.progress_interval == 0:
                elapsed = time.perf_counter() - started
                rate = processed / elapsed if elapsed > 0 else 0.0
                print(
                    f"progress=processed {processed:,}/{len(raw_jobs):,} written={written:,} "
                    f"errors={error_count:,} rate={rate:.1f}/s elapsed={elapsed:.1f}s",
                    flush=True,
                )

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_root": str(run_root),
        "raw_root": str(raw_root),
        "attachment_path": str(attachment_path) if attachment_path.exists() else "",
        "extraction_path": str(extraction_path) if extraction_path.exists() else "",
        "normalized_path": str(normalized_path),
        "error_path": str(error_path),
        "attachment_summary_path": str(attachment_summary_path),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "path_prefix_maps": [{"from": old, "to": new} for old, new in path_maps],
        "raw_jobs": len(raw_jobs),
        "rows_written": written,
        "error_count": error_count,
        "attachment_article_count": len(attachment_index.by_article),
        "attachment_url_count": len(attachment_index.url_hashes),
        "enrichment_url_count": len(enrichment_index),
        "status_counts": dict(counters),
        "quality_flag_counts": dict(flag_counts),
        "text_limit_chars": args.text_limit_chars,
        "max_enriched_text_chars_per_url": args.max_enriched_text_chars_per_url,
        "max_enriched_urls_per_article": args.max_enriched_urls_per_article,
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


class RawJob:
    def __init__(self, *, raw_artifact_path: Path, raw_payload_hash: str = "", provider_article_id: str = "", canonical_news_id: str = "") -> None:
        self.raw_artifact_path = raw_artifact_path
        self.raw_payload_hash = raw_payload_hash
        self.provider_article_id = provider_article_id
        self.canonical_news_id = canonical_news_id


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
            key = article_key(row)
            index.by_article[key].append(row)
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
                    f"attachments_loaded={line_number:,} articles={len(index.by_article):,} "
                    f"urls={len(index.url_hashes):,} elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    print(
        f"attachments_loaded=done articles={len(index.by_article):,} urls={len(index.url_hashes):,} "
        f"raw_jobs={len(index.raw_jobs):,} elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return index


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


def build_normalized_row(
    args: argparse.Namespace,
    job: RawJob,
    attachment_index: AttachmentIndex,
    enrichment_index: dict[str, dict[str, Any]],
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
    attachments = article_attachments(row, job, attachment_index)
    enrichments = article_enrichments(attachments, enrichment_index)
    row, summary = apply_enrichments(args, row, attachments, enrichments)
    return row, summary


def article_attachments(
    row: dict[str, Any],
    job: RawJob,
    attachment_index: AttachmentIndex,
) -> list[dict[str, Any]]:
    keys = {
        article_key(row),
        f"provider:{row.get('provider_article_id') or job.provider_article_id}",
        f"canonical:{row.get('canonical_news_id') or job.canonical_news_id}",
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
