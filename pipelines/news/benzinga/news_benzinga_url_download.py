from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import mimetypes
import os
import random
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from pipelines.news.benzinga.news_benzinga_normalize import normalize_text, safe_filename, stable_hash, stable_sha256  # noqa: E402


DEFAULT_FETCH_PLAN_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_fetch_plan")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_download")
DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/news_benzinga_url_download_artifacts")
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


class FetchedResponse:
    def __init__(
        self,
        *,
        requested_url: str,
        final_url: str,
        status_code: int,
        headers: dict[str, str],
        body: bytes,
        elapsed_seconds: float,
    ) -> None:
        self.requested_url = requested_url
        self.final_url = final_url
        self.status_code = status_code
        self.headers = headers
        self.body = body
        self.elapsed_seconds = elapsed_seconds


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download deduplicated Benzinga URL fetch-plan rows into compressed raw artifacts. "
            "This stage does not extract text."
        )
    )
    parser.add_argument("--fetch-plan-jsonl", default=os.environ.get("NEWS_BENZINGA_URL_FETCH_PLAN_JSONL") or "")
    parser.add_argument("--fetch-plan-root-win", default=os.environ.get("NEWS_BENZINGA_URL_FETCH_PLAN_ROOT_WIN") or str(DEFAULT_FETCH_PLAN_ROOT_WIN))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--artifact-root-win", default=os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_ARTIFACT_ROOT_WIN") or str(DEFAULT_ARTIFACT_ROOT_WIN))
    parser.add_argument("--limit-urls", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_LIMIT_URLS", "0")))
    parser.add_argument("--network-concurrency", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_CONCURRENCY", "64")))
    parser.add_argument("--max-pending-futures", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_MAX_PENDING", "0")))
    parser.add_argument("--per-domain-min-interval-seconds", type=float, default=float(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_PER_DOMAIN_SECONDS", "0.02")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_TIMEOUT_SECONDS", "5")))
    parser.add_argument("--max-html-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_MAX_HTML_BYTES", str(4_000_000))))
    parser.add_argument("--max-pdf-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_MAX_PDF_BYTES", str(12_000_000))))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_MAX_RETRIES", "0")))
    parser.add_argument("--queue-seed", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_QUEUE_SEED", "1729")))
    parser.add_argument("--load-progress-interval", type=int, default=100_000)
    parser.add_argument("--progress-interval", type=int, default=1_000)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("--flush-interval", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-permanent-failures",
        action="store_true",
        help="When resuming, also retry previous permanent failures such as 401/403/404/410 and invalid URLs.",
    )
    parser.add_argument(
        "--include-sec-handler",
        action="store_true",
        help="Write sec_handler rows as deferred results. By default these are skipped in the downloader.",
    )
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    fetch_plan_path = resolve_fetch_plan_path(args)
    if not fetch_plan_path.exists():
        raise SystemExit(f"fetch plan file does not exist: {fetch_plan_path}")

    output_root = Path(args.output_root_win)
    artifact_root = Path(args.artifact_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    result_path = run_root / "news_url_download_result.jsonl"
    error_path = run_root / "news_url_download_errors.jsonl"
    queue_path = run_root / "news_url_download_queue.jsonl"
    manifest_path = run_root / "news_url_download_manifest.json"

    completed, permanent_failed = load_resume_url_hashes(output_root, retry_permanent_failures=bool(args.retry_permanent_failures)) if args.resume else (set(), set())
    rows = load_download_rows(args, fetch_plan_path, completed, permanent_failed)
    queued_rows = interleave_by_domain(rows, seed=args.queue_seed)
    write_queue(queue_path, queued_rows)

    print("=" * 96, flush=True)
    print("Benzinga URL download", flush=True)
    print(f"fetch_plan_path={fetch_plan_path}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"artifact_root={artifact_root}", flush=True)
    print(f"rows_to_download={len(queued_rows):,}", flush=True)
    print(f"skipped_completed={len(completed):,} skipped_permanent_failed={len(permanent_failed):,}", flush=True)
    print(f"network_concurrency={max(1, args.network_concurrency)}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    counters: Counter[str] = Counter()
    http_counts: Counter[int] = Counter()
    domain_counts: Counter[str] = Counter()
    processed = 0
    submitted = 0
    interrupted = False
    cancelled_count = 0
    pending_count_at_shutdown = 0
    limiter = DomainRateLimiter(args.per_domain_min_interval_seconds)

    with result_path.open("w", encoding="utf-8") as result_handle, error_path.open("w", encoding="utf-8") as error_handle:
        worker_count = max(1, args.network_concurrency)
        max_pending = max(worker_count, args.max_pending_futures or worker_count * 4)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
        pending: set[concurrent.futures.Future[dict[str, Any]]] = set()
        future_hashes: dict[concurrent.futures.Future[dict[str, Any]], str] = {}
        row_iter = iter(queued_rows)

        def submit_until_capacity() -> None:
            nonlocal submitted
            while len(pending) < max_pending:
                try:
                    row = next(row_iter)
                except StopIteration:
                    return
                future = executor.submit(download_row, row, args, limiter, artifact_root)
                pending.add(future)
                future_hashes[future] = str(row.get("url_hash") or "")
                submitted += 1

        try:
            submit_until_capacity()
            print(f"submitted_initial={submitted:,} max_pending_futures={max_pending:,}", flush=True)
            last_heartbeat = time.perf_counter()
            while pending:
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=max(1.0, args.heartbeat_seconds),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                if not done:
                    last_heartbeat = print_progress(
                        prefix="heartbeat",
                        processed=processed,
                        total=len(queued_rows),
                        submitted=submitted,
                        pending=len(pending),
                        counters=counters,
                        started=started,
                    )
                    continue
                for future in done:
                    processed += 1
                    try:
                        row = future.result()
                    except Exception as exc:  # noqa: BLE001
                        row = {
                            "url_hash": future_hashes.get(future, ""),
                            "status": "failed",
                            "status_reason": "worker_failed",
                            "error_type": type(exc).__name__,
                            "error_message": repr(exc),
                        }
                    status = str(row.get("status") or "unknown")
                    counters[status] += 1
                    http_counts[int(row.get("http_status") or 0)] += 1
                    domain = str(row.get("registered_domain") or row.get("domain") or "")
                    if domain:
                        domain_counts[domain] += 1
                    target = error_handle if status in {"failed", "transient_failed"} else result_handle
                    target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    if args.flush_interval and processed % args.flush_interval == 0:
                        result_handle.flush()
                        error_handle.flush()
                    future_hashes.pop(future, None)
                submit_until_capacity()
                now = time.perf_counter()
                if (args.progress_interval and processed % args.progress_interval == 0) or now - last_heartbeat >= args.heartbeat_seconds:
                    last_heartbeat = print_progress(
                        prefix="progress",
                        processed=processed,
                        total=len(queued_rows),
                        submitted=submitted,
                        pending=len(pending),
                        counters=counters,
                        started=started,
                    )
        except KeyboardInterrupt:
            interrupted = True
            pending_count_at_shutdown = len(pending)
            for future in pending:
                if future.cancel():
                    cancelled_count += 1
            print(
                f"interrupt=received processed={processed:,}/{len(queued_rows):,} "
                f"pending={pending_count_at_shutdown:,} cancelled={cancelled_count:,}",
                flush=True,
            )
        finally:
            executor.shutdown(wait=not interrupted, cancel_futures=interrupted)

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "fetch_plan_path": str(fetch_plan_path),
        "run_root": str(run_root),
        "artifact_root": str(artifact_root),
        "queue_path": str(queue_path),
        "result_path": str(result_path),
        "error_path": str(error_path),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "rows_loaded": len(rows),
        "rows_queued": len(queued_rows),
        "resume": bool(args.resume),
        "retry_permanent_failures": bool(args.retry_permanent_failures),
        "skipped_completed_count": len(completed),
        "skipped_permanent_failed_count": len(permanent_failed),
        "status_counts": dict(counters),
        "http_status_counts": {str(key): value for key, value in http_counts.items()},
        "top_domains": dict(domain_counts.most_common(50)),
        "network_concurrency": max(1, args.network_concurrency),
        "max_pending_futures": max(1, args.max_pending_futures or args.network_concurrency * 4),
        "per_domain_min_interval_seconds": args.per_domain_min_interval_seconds,
        "timeout_seconds": args.timeout_seconds,
        "max_retries": args.max_retries,
        "interrupted": interrupted,
        "pending_count_at_shutdown": pending_count_at_shutdown,
        "cancelled_count": cancelled_count,
        "submitted_count": submitted,
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


def load_download_rows(args: argparse.Namespace, fetch_plan_path: Path, completed: set[str], permanent_failed: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    with fetch_plan_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            url_hash = str(row.get("url_hash") or "")
            if completed and url_hash in completed:
                continue
            if permanent_failed and url_hash in permanent_failed:
                continue
            action = str(row.get("final_action") or "")
            if action == "sec_handler" and not args.include_sec_handler:
                continue
            if action not in {"fetch_html", "fetch_pdf", "fetch_text", "resolve_redirect", "sec_handler"}:
                continue
            rows.append(row)
            if args.load_progress_interval and len(rows) % args.load_progress_interval == 0:
                print(
                    f"loading_rows={len(rows):,} file_lines={line_number:,} elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
            if args.limit_urls and len(rows) >= args.limit_urls:
                break
    print(f"loading_rows=done rows={len(rows):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return rows


def interleave_by_domain(rows: list[dict[str, Any]], *, seed: int) -> list[dict[str, Any]]:
    buckets: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    rng = random.Random(seed)
    for row in rows:
        domain = str(row.get("registered_domain") or row.get("domain") or parse.urlparse(str(row.get("normalized_url") or "")).netloc.lower() or "unknown")
        buckets[domain].append(row)
    domains = list(buckets)
    rng.shuffle(domains)
    output: list[dict[str, Any]] = []
    active = deque(domains)
    while active:
        domain = active.popleft()
        bucket = buckets[domain]
        output.append(bucket.popleft())
        if bucket:
            active.append(domain)
    return output


def write_queue(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(rows):
            handle.write(
                json.dumps(
                    {
                        "queue_index": index,
                        "url_hash": row.get("url_hash") or "",
                        "normalized_url": row.get("normalized_url") or "",
                        "domain": row.get("domain") or "",
                        "registered_domain": row.get("registered_domain") or "",
                        "final_action": row.get("final_action") or "",
                        "fetch_priority": row.get("fetch_priority") or 0,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            )


def download_row(row: dict[str, Any], args: argparse.Namespace, limiter: DomainRateLimiter, artifact_root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    base = base_download_row(row)
    final_action = str(row.get("final_action") or "")
    if final_action == "sec_handler":
        base.update({"status": "deferred_sec_handler", "status_reason": "sec_handler_deferred_to_sec_pipeline"})
        return base
    url = prepare_request_url(str(row.get("normalized_url") or ""))
    try:
        max_bytes = args.max_pdf_bytes if final_action == "fetch_pdf" else args.max_html_bytes
        response = fetch_with_retries(url, args=args, limiter=limiter, max_bytes=max_bytes)
        content_type = normalize_content_type(response.headers.get("content-type", ""))
        resolved_action = classify_resolved_action(final_action, response.final_url or url, content_type)
        artifact = write_artifact(artifact_root, row, response.body, content_type)
        base.update(
            {
                "status": "downloaded",
                "status_reason": "downloaded",
                "requested_url": url,
                "final_url": response.final_url or url,
                "final_url_hash": stable_hash(response.final_url or url),
                "resolved_action": resolved_action,
                "http_status": response.status_code,
                "content_type": content_type,
                "content_length": int(response.headers.get("content-length") or len(response.body) or 0),
                "response_headers_json": compact_json(response.headers),
                "redirect_chain_json": compact_json([url, response.final_url] if response.final_url != url else [url]),
                "downloaded_at_utc": datetime.now(UTC).isoformat(),
                "downloaded_bytes": len(response.body),
                "downloaded_sha256": stable_sha256(response.body),
                "artifact_path": artifact["artifact_path"],
                "artifact_compression": artifact["artifact_compression"],
                "artifact_bytes": artifact["artifact_bytes"],
                "artifact_sha256": artifact["artifact_sha256"],
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "fetch_elapsed_seconds": round(response.elapsed_seconds, 3),
            }
        )
        return base
    except Exception as exc:  # noqa: BLE001
        base.update(
            {
                "status": "transient_failed" if is_transient_exception(exc) else "failed",
                "status_reason": "download_failed",
                "requested_url": url,
                "http_status": exception_http_status(exc),
                "error_type": type(exc).__name__,
                "error_message": repr(exc),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        return base


def base_download_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "url_hash": row.get("url_hash") or "",
        "normalized_url": row.get("normalized_url") or "",
        "requested_url": "",
        "final_url": "",
        "final_url_hash": "",
        "domain": row.get("domain") or "",
        "registered_domain": row.get("registered_domain") or "",
        "final_action": row.get("final_action") or "",
        "resolved_action": row.get("final_action") or "",
        "policy_reason": row.get("policy_reason") or "",
        "status": "started",
        "status_reason": "",
        "http_status": 0,
        "content_type": "",
        "content_length": 0,
        "response_headers_json": "{}",
        "redirect_chain_json": "[]",
        "downloaded_at_utc": "",
        "downloaded_bytes": 0,
        "downloaded_sha256": "",
        "artifact_path": "",
        "artifact_compression": "",
        "artifact_bytes": 0,
        "artifact_sha256": "",
        "fetch_elapsed_seconds": 0.0,
        "elapsed_seconds": 0.0,
        "error_type": "",
        "error_message": "",
        "occurrence_count": row.get("occurrence_count") or 0,
        "sample_provider_article_ids": row.get("sample_provider_article_ids") or [],
        "sample_canonical_news_ids": row.get("sample_canonical_news_ids") or [],
    }


def fetch_with_retries(url: str, *, args: argparse.Namespace, limiter: DomainRateLimiter, max_bytes: int) -> FetchedResponse:
    last_exc: Exception | None = None
    for attempt in range(max(0, args.max_retries) + 1):
        try:
            return fetch_once(url, args=args, limiter=limiter, max_bytes=max_bytes)
        except error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429 or 500 <= exc.code <= 599:
                if attempt >= args.max_retries:
                    raise
                retry_after = parse_retry_after(exc.headers.get("Retry-After", ""))
                time.sleep(retry_after or min(30.0, 1.5 * (attempt + 1)))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= args.max_retries:
                raise
            time.sleep(min(10.0, 1.25 * (attempt + 1)))
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
            requested_url=url,
            final_url=response.geturl(),
            status_code=int(getattr(response, "status", 0) or response.getcode() or 0),
            headers=headers,
            body=body,
            elapsed_seconds=time.perf_counter() - started,
        )


def write_artifact(artifact_root: Path, row: dict[str, Any], body: bytes, content_type: str) -> dict[str, Any]:
    url_hash = safe_filename(str(row.get("url_hash") or stable_hash(str(row.get("normalized_url") or ""))))
    registered_domain = safe_filename(str(row.get("registered_domain") or row.get("domain") or "unknown"))
    suffix = mimetypes.guess_extension(content_type) or ".bin"
    folder = artifact_root / registered_domain / url_hash[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{url_hash}{suffix}.gz"
    compressed = gzip.compress(body, compresslevel=6)
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.part")
    tmp_path.write_bytes(compressed)
    tmp_path.replace(path)
    return {
        "artifact_path": str(path),
        "artifact_compression": "gzip",
        "artifact_bytes": len(compressed),
        "artifact_sha256": stable_sha256(compressed),
    }


def load_resume_url_hashes(output_root: Path, *, retry_permanent_failures: bool) -> tuple[set[str], set[str]]:
    completed: set[str] = set()
    permanent_failed: set[str] = set()
    for path in output_root.glob("*/news_url_download_result.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        print(f"WARN skipping malformed result line path={path} line={line_number}", flush=True)
                        continue
                    if row.get("url_hash") and row.get("status") in {"downloaded", "deferred_sec_handler"}:
                        completed.add(str(row["url_hash"]))
        except OSError:
            continue
    if not retry_permanent_failures:
        for path in output_root.glob("*/news_url_download_errors.jsonl"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, 1):
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            print(f"WARN skipping malformed error line path={path} line={line_number}", flush=True)
                            continue
                        if row.get("url_hash") and is_permanent_failure_row(row):
                            permanent_failed.add(str(row["url_hash"]))
            except OSError:
                continue
    permanent_failed.difference_update(completed)
    return completed, permanent_failed


def is_permanent_failure_row(row: dict[str, Any]) -> bool:
    if str(row.get("status") or "") != "failed":
        return False
    http_status = int(row.get("http_status") or 0)
    error_type = str(row.get("error_type") or "")
    error_message = str(row.get("error_message") or "").casefold()
    if http_status in {400, 401, 403, 404, 405, 410, 451}:
        return True
    if error_type in {"InvalidURL", "UnicodeEncodeError"}:
        return True
    if error_type == "ValueError" and "too_large" in error_message:
        return True
    return False


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


def normalize_content_type(value: str) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


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


def print_progress(
    *,
    prefix: str,
    processed: int,
    total: int,
    submitted: int,
    pending: int,
    counters: Counter[str],
    started: float,
) -> float:
    elapsed = time.perf_counter() - started
    rate = processed / elapsed if elapsed > 0 else 0.0
    print(
        f"{prefix}=processed {processed:,}/{total:,} submitted={submitted:,} pending={pending:,} "
        f"rate={rate:.2f}/s statuses={dict(counters)} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return time.perf_counter()


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


if __name__ == "__main__":
    main()
