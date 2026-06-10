from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import gzip
import io
import json
import mimetypes
import os
import re
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.mlops.news_benzinga_normalize import (  # noqa: E402
    extract_pdf_text,
    normalize_text,
    safe_filename,
    stable_hash,
    stable_sha256,
)


DEFAULT_FETCH_PLAN_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_fetch_plan")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_enrichment")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TEXT_LIKE_TYPES = {
    "application/json",
    "application/ld+json",
    "application/rss+xml",
    "application/xhtml+xml",
    "application/xml",
    "text/html",
    "text/plain",
    "text/xml",
}
PERMANENT_ERROR_TYPES = {
    "deferred_sec_handler",
    "metadata_only",
    "ignored",
    "unsupported_content_type",
    "too_large",
    "empty_text",
}


class FetchedResponse:
    def __init__(
        self,
        *,
        url: str,
        final_url: str,
        status_code: int,
        headers: dict[str, str],
        body: bytes,
        elapsed_seconds: float,
    ) -> None:
        self.url = url
        self.final_url = final_url
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.elapsed_seconds = elapsed_seconds


class CleanTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._block_pending = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag_name in {"script", "style", "noscript", "svg", "canvas", "iframe", "form"}:
            self._skip_depth += 1
            return
        if tag_name == "title":
            self._in_title = True
            return
        href = attr_map.get("href", "")
        if tag_name == "a" and href:
            self.links.append(href)
        if self._skip_depth:
            return
        if tag_name in {"article", "section", "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}:
            self._block_pending = True

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"script", "style", "noscript", "svg", "canvas", "iframe", "form"} and self._skip_depth:
            self._skip_depth -= 1
        if tag_name == "title":
            self._in_title = False
        if tag_name in {"p", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}:
            self._block_pending = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            text = normalize_text(data)
            if text:
                self.title_parts.append(text)
            return
        if self._skip_depth:
            return
        text = normalize_text(data)
        if not text:
            return
        if self._block_pending and self.parts:
            self.parts.append("\n")
        self.parts.append(text)
        self._block_pending = False

    @property
    def text(self) -> str:
        return normalize_multiline_text(" ".join(self.parts).replace(" \n ", "\n"))

    @property
    def title(self) -> str:
        return normalize_text(" ".join(self.title_parts))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and extract clean text for deduplicated Benzinga URL fetch-plan rows. "
            "Writes text/metadata only by default."
        )
    )
    parser.add_argument("--fetch-plan-jsonl", default=os.environ.get("NEWS_BENZINGA_URL_FETCH_PLAN_JSONL") or "")
    parser.add_argument("--fetch-plan-root-win", default=os.environ.get("NEWS_BENZINGA_URL_FETCH_PLAN_ROOT_WIN") or str(DEFAULT_FETCH_PLAN_ROOT_WIN))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_URL_ENRICHMENT_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--limit-urls", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_ENRICH_LIMIT_URLS", "0")))
    parser.add_argument("--network-concurrency", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_ENRICH_NETWORK_CONCURRENCY", "8")))
    parser.add_argument("--per-domain-min-interval-seconds", type=float, default=float(os.environ.get("NEWS_BENZINGA_URL_ENRICH_PER_DOMAIN_SECONDS", "0.2")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("NEWS_BENZINGA_URL_ENRICH_TIMEOUT_SECONDS", "12")))
    parser.add_argument("--max-html-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_ENRICH_MAX_HTML_BYTES", str(4_000_000))))
    parser.add_argument("--max-pdf-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_ENRICH_MAX_PDF_BYTES", str(12_000_000))))
    parser.add_argument("--max-text-chars", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_ENRICH_MAX_TEXT_CHARS", str(300_000))))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_ENRICH_MAX_RETRIES", "2")))
    parser.add_argument("--progress-interval", type=int, default=500)
    parser.add_argument("--load-progress-interval", type=int, default=100_000)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("--max-pending-futures", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--save-raw-artifacts", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    print("Benzinga URL enrichment starting", flush=True)
    fetch_plan_path = resolve_fetch_plan_path(args)
    if not fetch_plan_path.exists():
        raise SystemExit(f"fetch plan file does not exist: {fetch_plan_path}")
    output_root = Path(args.output_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    result_path = run_root / "news_url_enrichment_result.jsonl"
    error_path = run_root / "news_url_enrichment_errors.jsonl"
    manifest_path = run_root / "news_url_enrichment_manifest.json"
    raw_root = run_root / "raw_artifacts" if args.save_raw_artifacts else None
    if raw_root is not None:
        raw_root.mkdir(parents=True, exist_ok=True)

    print(f"fetch_plan_path={fetch_plan_path}", flush=True)
    print(f"run_root={run_root}", flush=True)
    completed = load_completed_url_hashes(output_root) if args.resume else set()
    if completed:
        print(f"resume_completed_url_hashes={len(completed):,}", flush=True)
    rows = load_fetch_plan_rows(fetch_plan_path, args.limit_urls, completed, args.load_progress_interval)
    limiter = DomainRateLimiter(args.per_domain_min_interval_seconds)

    print("=" * 96, flush=True)
    print("Benzinga URL enrichment", flush=True)
    print(f"fetch_plan_path={fetch_plan_path}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"rows_to_process={len(rows):,} skipped_completed={len(completed):,}", flush=True)
    print(f"network_concurrency={max(1, args.network_concurrency)}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    status_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    quality_flag_counts: Counter[str] = Counter()
    processed = 0
    interrupted = False
    cancelled_count = 0
    pending_count_at_shutdown = 0
    submitted_count = 0
    with result_path.open("w", encoding="utf-8") as result_handle, error_path.open("w", encoding="utf-8") as error_handle:
        worker_count = max(1, args.network_concurrency)
        max_pending_futures = max(worker_count, args.max_pending_futures or worker_count * 4)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
        pending: set[concurrent.futures.Future[dict[str, Any]]] = set()
        future_url_hashes: dict[concurrent.futures.Future[dict[str, Any]], str] = {}
        row_iter = iter(rows)

        def submit_until_capacity() -> None:
            nonlocal submitted_count
            while len(pending) < max_pending_futures:
                try:
                    row = next(row_iter)
                except StopIteration:
                    return
                future = pool.submit(enrich_row, row, args, limiter, raw_root)
                pending.add(future)
                future_url_hashes[future] = str(row.get("url_hash", ""))
                submitted_count += 1

        try:
            submit_until_capacity()
            print(
                f"submitted_initial={submitted_count:,} max_pending_futures={max_pending_futures:,} "
                f"total_rows={len(rows):,}",
                flush=True,
            )
            last_heartbeat = time.perf_counter()
            while pending:
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=max(1.0, args.heartbeat_seconds),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    last_heartbeat = print_progress(
                        processed=processed,
                        total=len(rows),
                        status_counts=status_counts,
                        started=started,
                        pending_count=len(pending),
                        submitted_count=submitted_count,
                        prefix="heartbeat",
                    )
                    continue
                for future in done:
                    processed += 1
                    try:
                        row = future.result()
                    except Exception as exc:  # noqa: BLE001
                        row = {
                            "url_hash": future_url_hashes.get(future, ""),
                            "status": "failed",
                            "status_reason": "worker_failed",
                            "error_type": type(exc).__name__,
                            "error_message": repr(exc),
                            "quality_flags": [],
                        }
                    status = str(row.get("status") or "unknown")
                    status_counts[status] += 1
                    action_counts[str(row.get("resolved_action") or row.get("final_action") or "")] += 1
                    for flag in row.get("quality_flags") or []:
                        quality_flag_counts[str(flag)] += 1
                    target = error_handle if status in {"failed", "transient_failed"} else result_handle
                    target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    future_url_hashes.pop(future, None)
                submit_until_capacity()
                now = time.perf_counter()
                if (args.progress_interval and processed % args.progress_interval == 0) or now - last_heartbeat >= args.heartbeat_seconds:
                    last_heartbeat = print_progress(
                        processed=processed,
                        total=len(rows),
                        status_counts=status_counts,
                        started=started,
                        pending_count=len(pending),
                        submitted_count=submitted_count,
                        prefix="progress",
                    )
        except KeyboardInterrupt:
            interrupted = True
            pending_count_at_shutdown = len(pending)
            for future in pending:
                if future.cancel():
                    cancelled_count += 1
            print(
                f"interrupt=received processed={processed:,}/{len(rows):,} "
                f"pending={pending_count_at_shutdown:,} cancelled={cancelled_count:,} "
                f"elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
        finally:
            pool.shutdown(wait=not interrupted, cancel_futures=interrupted)

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "fetch_plan_path": str(fetch_plan_path),
        "run_root": str(run_root),
        "result_path": str(result_path),
        "error_path": str(error_path),
        "raw_artifact_root": str(raw_root or ""),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "limit_urls": args.limit_urls,
        "rows_loaded": len(rows),
        "resume": bool(args.resume),
        "skipped_completed_count": len(completed),
        "status_counts": dict(status_counts),
        "resolved_action_counts": dict(action_counts),
        "quality_flag_counts": dict(quality_flag_counts),
        "interrupted": interrupted,
        "pending_count_at_shutdown": pending_count_at_shutdown,
        "cancelled_count": cancelled_count,
        "submitted_count": submitted_count,
        "network_concurrency": max(1, args.network_concurrency),
        "max_pending_futures": max(1, args.max_pending_futures or args.network_concurrency * 4),
        "per_domain_min_interval_seconds": args.per_domain_min_interval_seconds,
        "max_html_bytes": args.max_html_bytes,
        "max_pdf_bytes": args.max_pdf_bytes,
        "max_text_chars": args.max_text_chars,
        "save_raw_artifacts": bool(args.save_raw_artifacts),
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest_path=" + str(manifest_path), flush=True)
    print("summary=" + json.dumps(manifest, sort_keys=True), flush=True)


def resolve_fetch_plan_path(args: argparse.Namespace) -> Path:
    explicit = str(args.fetch_plan_jsonl or "").strip()
    if explicit:
        return Path(explicit)
    root = Path(args.fetch_plan_root_win)
    manifests = sorted(root.glob("*/news_url_fetch_plan_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidate = Path(manifest.get("fetch_plan_path") or "")
            if candidate.exists():
                return candidate
        except Exception:  # noqa: BLE001
            continue
    latest = sorted(root.glob("*/news_url_fetch_plan.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if latest:
        return latest[0]
    return root / "news_url_fetch_plan.jsonl"


def load_fetch_plan_rows(fetch_plan_path: Path, limit_urls: int, completed: set[str], progress_interval: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    print("loading_fetch_plan_rows=started", flush=True)
    with fetch_plan_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            url_hash = str(row.get("url_hash") or "")
            if completed and url_hash in completed:
                continue
            rows.append(row)
            if progress_interval and len(rows) % progress_interval == 0:
                print(
                    f"loading_fetch_plan_rows={len(rows):,} file_lines={line_number:,} "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
            if limit_urls and len(rows) >= limit_urls:
                break
    print(f"loading_fetch_plan_rows=done rows={len(rows):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return rows


def print_progress(
    *,
    processed: int,
    total: int,
    status_counts: Counter[str],
    started: float,
    pending_count: int,
    submitted_count: int,
    prefix: str,
) -> float:
    elapsed = time.perf_counter() - started
    rate = processed / elapsed if elapsed > 0 else 0.0
    print(
        f"{prefix}=processed {processed:,}/{total:,} submitted={submitted_count:,} pending={pending_count:,} "
        f"rate={rate:.2f}/s statuses={dict(status_counts)} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return time.perf_counter()


def load_completed_url_hashes(output_root: Path) -> set[str]:
    completed: set[str] = set()
    for path in output_root.glob("*/news_url_enrichment_result.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    url_hash = str(row.get("url_hash") or "")
                    status = str(row.get("status") or "")
                    if url_hash and status not in {"failed", "transient_failed"}:
                        completed.add(url_hash)
        except OSError:
            continue
    return completed


class DomainRateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._lock = threading.Lock()
        self._last_by_domain: dict[str, float] = {}

    def wait(self, url: str) -> None:
        if self.min_interval_seconds <= 0:
            return
        domain = parse.urlparse(url).netloc.lower() or "unknown"
        with self._lock:
            now = time.perf_counter()
            last = self._last_by_domain.get(domain, 0.0)
            sleep_for = max(0.0, self.min_interval_seconds - (now - last))
            if sleep_for:
                time.sleep(sleep_for)
            self._last_by_domain[domain] = time.perf_counter()


def enrich_row(row: dict[str, Any], args: argparse.Namespace, limiter: DomainRateLimiter, raw_root: Path | None) -> dict[str, Any]:
    started = time.perf_counter()
    base = base_result(row)
    final_action = str(row.get("final_action") or "")
    if final_action == "sec_handler":
        base.update({"status": "deferred_sec_handler", "status_reason": "sec_handler_deferred_to_sec_pipeline", "resolved_action": "sec_handler"})
        return base

    url = prepare_request_url(str(row.get("normalized_url") or ""))
    try:
        max_bytes = args.max_pdf_bytes if final_action == "fetch_pdf" else args.max_html_bytes
        response = fetch_with_retries(url, args=args, limiter=limiter, max_bytes=max_bytes)
        content_type = normalize_content_type(response.headers.get("content-type", ""))
        final_url = response.final_url or url
        resolved_action = classify_resolved_action(final_action, final_url, content_type)
        base.update(
            {
                "final_url": final_url,
                "final_url_hash": stable_hash(final_url),
                "resolved_action": resolved_action,
                "http_status": response.status_code,
                "content_type": content_type,
                "content_length": int(response.headers.get("content-length") or len(response.body) or 0),
                "fetched_at_utc": datetime.now(UTC).isoformat(),
                "redirect_chain_json": compact_json([url, final_url] if final_url != url else [url]),
                "fetched_bytes": len(response.body),
                "fetched_sha256": stable_sha256(response.body),
                "fetch_elapsed_seconds": round(response.elapsed_seconds, 3),
            }
        )
        if raw_root is not None:
            base.update(write_raw_artifact(raw_root, row, response, content_type))
        if resolved_action == "fetch_pdf":
            extracted = extract_pdf_result(response.body, args)
        elif resolved_action == "fetch_html":
            extracted = extract_html_result(response.body, final_url, content_type, args)
        elif resolved_action == "fetch_text":
            extracted = extract_plain_text_result(response.body, content_type, args)
        else:
            extracted = {
                "status": "unsupported_content_type",
                "status_reason": f"unsupported_content_type:{content_type}",
                "extracted_text": "",
                "extraction_method": "none",
                "quality_flags": ["unsupported_content_type"],
            }
        base.update(extracted)
        base["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return base
    except Exception as exc:  # noqa: BLE001
        base.update(
            {
                "status": "transient_failed" if is_transient_exception(exc) else "failed",
                "status_reason": "fetch_or_extract_failed",
                "http_status": exception_http_status(exc),
                "error_type": type(exc).__name__,
                "error_message": repr(exc),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        return base


def base_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "url_hash": row.get("url_hash") or "",
        "normalized_url": row.get("normalized_url") or "",
        "final_url": "",
        "final_url_hash": "",
        "final_action": row.get("final_action") or "",
        "resolved_action": row.get("final_action") or "",
        "status": "started",
        "status_reason": "",
        "http_status": 0,
        "content_type": "",
        "content_length": 0,
        "fetched_at_utc": "",
        "redirect_chain_json": "[]",
        "title": "",
        "canonical_url": "",
        "extracted_text": "",
        "extracted_text_chars": 0,
        "extracted_text_hash": "",
        "extraction_method": "",
        "extraction_quality": "unknown",
        "language": "",
        "quality_flags": [],
        "pdf_page_count": 0,
        "pdf_metadata_json": "[]",
        "artifact_path": "",
        "artifact_sha256": "",
        "fetched_bytes": 0,
        "fetched_sha256": "",
        "fetch_elapsed_seconds": 0.0,
        "elapsed_seconds": 0.0,
        "error_type": "",
        "error_message": "",
        "retry_count": 0,
        "occurrence_count": row.get("occurrence_count") or 0,
        "sample_provider_article_ids": row.get("sample_provider_article_ids") or [],
        "sample_canonical_news_ids": row.get("sample_canonical_news_ids") or [],
    }


def fetch_with_retries(url: str, *, args: argparse.Namespace, limiter: DomainRateLimiter, max_bytes: int) -> FetchedResponse:
    last_exc: Exception | None = None
    for attempt in range(1, max(1, args.max_retries + 1) + 1):
        try:
            return fetch_once(url, args=args, limiter=limiter, max_bytes=max_bytes)
        except error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429:
                retry_after = parse_retry_after(exc.headers.get("Retry-After", ""))
                time.sleep(retry_after or min(30.0, 1.5 * attempt))
                continue
            if 500 <= exc.code <= 599:
                time.sleep(min(15.0, 1.5 * attempt))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(min(10.0, 1.25 * attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError("fetch_failed_without_exception")


def fetch_once(url: str, *, args: argparse.Namespace, limiter: DomainRateLimiter, max_bytes: int) -> FetchedResponse:
    limiter.wait(url)
    req = request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.8",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    started = time.perf_counter()
    with request.urlopen(req, timeout=args.timeout_seconds) as response:  # noqa: S310
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"too_large:max_bytes={max_bytes}")
            chunks.append(chunk)
        body = b"".join(chunks)
        headers = {key.lower(): value for key, value in response.headers.items()}
        if headers.get("content-encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return FetchedResponse(
            url=url,
            final_url=response.geturl(),
            status_code=int(getattr(response, "status", 0) or response.getcode() or 0),
            headers=headers,
            body=body,
            elapsed_seconds=time.perf_counter() - started,
        )


def prepare_request_url(value: str) -> str:
    url = normalize_text(value).strip()
    if not url:
        return url
    parts = parse.urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    try:
        netloc = parts.netloc.encode("idna").decode("ascii")
    except UnicodeError:
        netloc = parts.netloc
    path = parse.quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = parse.quote(parts.query, safe="=&%:@/?!$'()*+,;,-._~")
    fragment = parse.quote(parts.fragment, safe="=&%:@/?!$'()*+,;,-._~")
    return parse.urlunsplit((parts.scheme, netloc, path, query, fragment))


def classify_resolved_action(original_action: str, final_url: str, content_type: str) -> str:
    path = parse.urlparse(final_url).path.lower()
    if "pdf" in content_type or path.endswith(".pdf"):
        return "fetch_pdf"
    if content_type.startswith("text/plain") or content_type in {"application/json", "application/xml", "text/xml", "application/rss+xml"}:
        return "fetch_text"
    if content_type in TEXT_LIKE_TYPES or "html" in content_type or original_action in {"fetch_html", "resolve_redirect"}:
        return "fetch_html"
    return "unsupported"


def extract_html_result(body: bytes, final_url: str, content_type: str, args: argparse.Namespace) -> dict[str, Any]:
    html_text = decode_bytes(body, content_type)
    extracted_text, title, canonical_url, method = extract_html_clean_text(html_text, final_url)
    text = truncate_text(normalize_multiline_text(extracted_text), args.max_text_chars)
    flags = quality_flags(text, content_type=content_type, method=method)
    return {
        "status": "success" if text else "empty_text",
        "status_reason": "html_extracted" if text else "html_empty_after_extraction",
        "title": title,
        "canonical_url": canonical_url,
        "extracted_text": text,
        "extracted_text_chars": len(text),
        "extracted_text_hash": stable_hash(text),
        "extraction_method": method,
        "extraction_quality": quality_from_flags(flags),
        "quality_flags": flags,
    }


def extract_plain_text_result(body: bytes, content_type: str, args: argparse.Namespace) -> dict[str, Any]:
    text = truncate_text(normalize_multiline_text(decode_bytes(body, content_type)), args.max_text_chars)
    flags = quality_flags(text, content_type=content_type, method="plain_text")
    return {
        "status": "success" if text else "empty_text",
        "status_reason": "plain_text_extracted" if text else "plain_text_empty",
        "extracted_text": text,
        "extracted_text_chars": len(text),
        "extracted_text_hash": stable_hash(text),
        "extraction_method": "plain_text",
        "extraction_quality": quality_from_flags(flags),
        "quality_flags": flags,
    }


def extract_pdf_result(body: bytes, args: argparse.Namespace) -> dict[str, Any]:
    try:
        text = truncate_text(extract_pdf_text(body), args.max_text_chars)
        page_count = pdf_page_count(body)
        flags = quality_flags(text, content_type="application/pdf", method="pymupdf_pdf")
        if not text:
            flags.append("pdf_scanned_or_image_only")
        metadata = [
            {
                "status": "extracted" if text else "empty",
                "content_length": len(body),
                "max_pdf_bytes": args.max_pdf_bytes,
                "fetched_sha256": stable_sha256(body),
                "extracted_text_chars": len(text),
                "page_count": page_count,
            }
        ]
        return {
            "status": "success" if text else "empty_text",
            "status_reason": "pdf_text_extracted" if text else "pdf_empty_after_extraction",
            "extracted_text": text,
            "extracted_text_chars": len(text),
            "extracted_text_hash": stable_hash(text),
            "extraction_method": "pymupdf_pdf",
            "extraction_quality": quality_from_flags(flags),
            "quality_flags": flags,
            "pdf_page_count": page_count,
            "pdf_metadata_json": compact_json(metadata),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "status_reason": "pdf_extract_failed",
            "error_type": type(exc).__name__,
            "error_message": repr(exc),
            "quality_flags": ["pdf_extract_failed"],
            "pdf_metadata_json": compact_json([{"status": "failed", "exception": repr(exc), "content_length": len(body)}]),
        }


def extract_html_clean_text(html_text: str, final_url: str) -> tuple[str, str, str, str]:
    trafilatura_result = extract_with_trafilatura(html_text, final_url)
    if trafilatura_result[0]:
        return trafilatura_result
    readability_result = extract_with_readability(html_text, final_url)
    if readability_result[0]:
        return readability_result
    bs_result = extract_with_beautifulsoup(html_text, final_url)
    if bs_result[0]:
        return bs_result
    parser = CleanTextParser()
    parser.feed(html_text)
    return parser.text, parser.title, extract_canonical_url(html_text, final_url), "htmlparser_fallback"


def extract_with_trafilatura(html_text: str, final_url: str) -> tuple[str, str, str, str]:
    try:
        import trafilatura  # type: ignore
    except ImportError:
        return "", "", "", "trafilatura_unavailable"
    try:
        extracted = trafilatura.extract(
            html_text,
            url=final_url,
            include_comments=False,
            include_tables=True,
            favor_precision=False,
            output_format="txt",
        )
        if not extracted:
            return "", "", "", "trafilatura_empty"
        metadata = trafilatura.extract_metadata(html_text, default_url=final_url)
        title = normalize_text(getattr(metadata, "title", "") or "") if metadata else ""
        canonical = normalize_text(getattr(metadata, "url", "") or "") if metadata else ""
        return normalize_multiline_text(extracted), title, canonical or extract_canonical_url(html_text, final_url), "trafilatura"
    except Exception:  # noqa: BLE001
        return "", "", "", "trafilatura_failed"


def extract_with_readability(html_text: str, final_url: str) -> tuple[str, str, str, str]:
    if not html_text.strip():
        return "", "", "", "readability_empty_input"
    try:
        from readability import Document  # type: ignore
    except ImportError:
        return "", "", "", "readability_unavailable"
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            doc = Document(html_text)
            summary_html = doc.summary(html_partial=True)
        parser = CleanTextParser()
        parser.feed(summary_html)
        text = parser.text
        if not text:
            return "", "", "", "readability_empty"
        return text, normalize_text(doc.short_title() or ""), extract_canonical_url(html_text, final_url), "readability"
    except Exception:  # noqa: BLE001
        return "", "", "", "readability_failed"


def extract_with_beautifulsoup(html_text: str, final_url: str) -> tuple[str, str, str, str]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return "", "", "", "beautifulsoup_unavailable"
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe", "form", "nav", "footer", "header"]):
            tag.decompose()
        title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = normalize_multiline_text(main.get_text("\n", strip=True))
        return text, title, extract_canonical_url(html_text, final_url), "beautifulsoup"
    except Exception:  # noqa: BLE001
        return "", "", "", "beautifulsoup_failed"


def extract_canonical_url(html_text: str, final_url: str) -> str:
    match = re.search(r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']", html_text, re.IGNORECASE)
    if not match:
        match = re.search(r"<meta[^>]+property=[\"']og:url[\"'][^>]+content=[\"']([^\"']+)[\"']", html_text, re.IGNORECASE)
    if not match:
        return ""
    return parse.urljoin(final_url, normalize_text(match.group(1)))


def decode_bytes(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([A-Za-z0-9_.-]+)", content_type or "", re.IGNORECASE)
    encodings = [charset_match.group(1)] if charset_match else []
    encodings.extend(["utf-8", "cp1252", "latin-1"])
    for encoding in encodings:
        try:
            return body.decode(encoding)
        except Exception:  # noqa: BLE001
            continue
    return body.decode("utf-8", errors="replace")


def normalize_content_type(value: str) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def normalize_multiline_text(value: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", value or "")
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_text(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].rstrip()


def quality_flags(text: str, *, content_type: str, method: str) -> list[str]:
    flags: list[str] = []
    lowered = text[:5000].casefold()
    if not text:
        flags.append("empty_text")
    elif len(text) < 500:
        flags.append("short_text")
    if len(text) > 0 and boilerplate_ratio(text) > 0.45:
        flags.append("boilerplate_heavy")
    if any(token in lowered for token in ["enable javascript", "access denied", "verify you are human", "captcha"]):
        flags.append("blocked_or_bot_challenge")
    if any(token in lowered for token in ["subscribe to continue", "sign in to continue", "already a subscriber"]):
        flags.append("paywall_or_login")
    if "pdf" in content_type:
        flags.append("pdf_text")
    if method.endswith("fallback"):
        flags.append("fallback_extractor")
    return flags


def boilerplate_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z]{3,}", text.casefold())
    if not words:
        return 0.0
    boilerplate_words = {
        "cookie",
        "privacy",
        "terms",
        "subscribe",
        "advertisement",
        "newsletter",
        "facebook",
        "twitter",
        "linkedin",
        "copyright",
        "login",
        "sign",
    }
    hits = sum(1 for word in words if word in boilerplate_words)
    return hits / max(1, len(words))


def quality_from_flags(flags: list[str]) -> str:
    if not flags:
        return "good"
    if "empty_text" in flags or "blocked_or_bot_challenge" in flags:
        return "bad"
    if "short_text" in flags or "boilerplate_heavy" in flags or "paywall_or_login" in flags:
        return "weak"
    return "usable"


def pdf_page_count(pdf_bytes: bytes) -> int:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return 0
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def write_raw_artifact(raw_root: Path, row: dict[str, Any], response: FetchedResponse, content_type: str) -> dict[str, str]:
    suffix = mimetypes.guess_extension(content_type) or Path(parse.urlparse(response.final_url).path).suffix or ".bin"
    action = safe_filename(str(row.get("final_action") or "unknown"))
    folder = raw_root / action / safe_filename(str(row.get("registered_domain") or row.get("domain") or "unknown"))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_filename(str(row.get('url_hash') or stable_hash(response.final_url)))}{suffix}"
    path.write_bytes(response.body)
    return {"artifact_path": str(path), "artifact_sha256": stable_sha256(response.body)}


def is_transient_exception(exc: Exception) -> bool:
    if isinstance(exc, error.HTTPError):
        return exc.code == 429 or 500 <= exc.code <= 599
    text = repr(exc).casefold()
    return any(token in text for token in ["timeout", "incompleteread", "connection reset", "429", "500", "502", "503", "504"])


def exception_http_status(exc: Exception) -> int:
    if isinstance(exc, error.HTTPError):
        return int(exc.code or 0)
    return 0


def parse_retry_after(value: str) -> float:
    try:
        return max(0.0, float(value.strip()))
    except Exception:  # noqa: BLE001
        return 0.0


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


if __name__ == "__main__":
    main()
