from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from pipelines.news.benzinga.news_benzinga_url_enrich import (  # noqa: E402
    compact_json,
    extract_html_result,
    extract_pdf_result,
    extract_plain_text_result,
)


DEFAULT_DOWNLOAD_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_download")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_extraction")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract clean text from raw artifacts produced by news_benzinga_url_download.py. "
            "This stage does not perform network requests."
        )
    )
    parser.add_argument("--download-result-jsonl", default=os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_RESULT_JSONL") or "")
    parser.add_argument("--download-root-win", default=os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_ROOT_WIN") or str(DEFAULT_DOWNLOAD_ROOT_WIN))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_URL_EXTRACTION_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--limit-rows", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_EXTRACT_LIMIT_ROWS", "0")))
    parser.add_argument("--processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_EXTRACT_PROCESSES", str(max(1, (os.cpu_count() or 4) // 2)))))
    parser.add_argument("--max-pending-futures", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_EXTRACT_MAX_PENDING", "0")))
    parser.add_argument("--max-text-chars", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_EXTRACT_MAX_TEXT_CHARS", str(300_000))))
    parser.add_argument("--max-pdf-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_EXTRACT_MAX_PDF_BYTES", str(12_000_000))))
    parser.add_argument("--load-progress-interval", type=int, default=100_000)
    parser.add_argument("--progress-interval", type=int, default=1_000)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("--flush-interval", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    download_result_path = resolve_download_result_path(args)
    if not download_result_path.exists():
        raise SystemExit(f"download result file does not exist: {download_result_path}")

    output_root = Path(args.output_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    result_path = run_root / "news_url_extraction_result.jsonl"
    error_path = run_root / "news_url_extraction_errors.jsonl"
    manifest_path = run_root / "news_url_extraction_manifest.json"

    completed = load_completed_url_hashes(output_root) if args.resume else set()
    rows = load_download_rows(args, download_result_path, completed)

    print("=" * 96, flush=True)
    print("Benzinga URL extraction", flush=True)
    print(f"download_result_path={download_result_path}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"rows_to_extract={len(rows):,} skipped_completed={len(completed):,}", flush=True)
    print(f"processes={max(1, args.processes)}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    counters: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    processed = 0
    submitted = 0
    interrupted = False
    cancelled_count = 0
    pending_count_at_shutdown = 0

    with result_path.open("w", encoding="utf-8") as result_handle, error_path.open("w", encoding="utf-8") as error_handle:
        worker_count = max(1, args.processes)
        max_pending = max(worker_count, args.max_pending_futures or worker_count * 4)
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=worker_count)
        pending: set[concurrent.futures.Future[dict[str, Any]]] = set()
        future_hashes: dict[concurrent.futures.Future[dict[str, Any]], str] = {}
        row_iter = iter(rows)

        def submit_until_capacity() -> None:
            nonlocal submitted
            while len(pending) < max_pending:
                try:
                    row = next(row_iter)
                except StopIteration:
                    return
                future = executor.submit(extract_row, row, args.max_text_chars, args.max_pdf_bytes)
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
                    last_heartbeat = print_progress("heartbeat", processed, len(rows), submitted, len(pending), counters, started)
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
                    method_counts[str(row.get("extraction_method") or "")] += 1
                    for flag in row.get("quality_flags") or []:
                        flag_counts[str(flag)] += 1
                    target = error_handle if status == "failed" else result_handle
                    target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    if args.flush_interval and processed % args.flush_interval == 0:
                        result_handle.flush()
                        error_handle.flush()
                    future_hashes.pop(future, None)
                submit_until_capacity()
                now = time.perf_counter()
                if (args.progress_interval and processed % args.progress_interval == 0) or now - last_heartbeat >= args.heartbeat_seconds:
                    last_heartbeat = print_progress("progress", processed, len(rows), submitted, len(pending), counters, started)
        except KeyboardInterrupt:
            interrupted = True
            pending_count_at_shutdown = len(pending)
            for future in pending:
                if future.cancel():
                    cancelled_count += 1
            print(
                f"interrupt=received processed={processed:,}/{len(rows):,} "
                f"pending={pending_count_at_shutdown:,} cancelled={cancelled_count:,}",
                flush=True,
            )
        finally:
            executor.shutdown(wait=not interrupted, cancel_futures=interrupted)

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "download_result_path": str(download_result_path),
        "run_root": str(run_root),
        "result_path": str(result_path),
        "error_path": str(error_path),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "rows_loaded": len(rows),
        "resume": bool(args.resume),
        "skipped_completed_count": len(completed),
        "status_counts": dict(counters),
        "extraction_method_counts": dict(method_counts),
        "quality_flag_counts": dict(flag_counts),
        "processes": max(1, args.processes),
        "max_pending_futures": max(1, args.max_pending_futures or args.processes * 4),
        "interrupted": interrupted,
        "pending_count_at_shutdown": pending_count_at_shutdown,
        "cancelled_count": cancelled_count,
        "submitted_count": submitted,
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest_path=" + str(manifest_path), flush=True)
    print("summary=" + json.dumps(manifest, sort_keys=True), flush=True)


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


def load_download_rows(args: argparse.Namespace, download_result_path: Path, completed: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    with download_result_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"WARN skipping malformed download result line={line_number}", flush=True)
                continue
            if row.get("status") != "downloaded":
                continue
            url_hash = str(row.get("url_hash") or "")
            if completed and url_hash in completed:
                continue
            if not row.get("artifact_path"):
                continue
            rows.append(row)
            if args.load_progress_interval and len(rows) % args.load_progress_interval == 0:
                print(
                    f"loading_rows={len(rows):,} file_lines={line_number:,} elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
            if args.limit_rows and len(rows) >= args.limit_rows:
                break
    print(f"loading_rows=done rows={len(rows):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return rows


def extract_row(row: dict[str, Any], max_text_chars: int, max_pdf_bytes: int) -> dict[str, Any]:
    started = time.perf_counter()
    base = base_extraction_row(row)
    try:
        body = read_artifact(Path(str(row.get("artifact_path") or "")), str(row.get("artifact_compression") or ""))
        args = SimpleNamespace(max_text_chars=max_text_chars, max_pdf_bytes=max_pdf_bytes)
        resolved_action = str(row.get("resolved_action") or row.get("final_action") or "")
        content_type = str(row.get("content_type") or "")
        final_url = str(row.get("final_url") or row.get("normalized_url") or "")
        if resolved_action == "fetch_pdf":
            extracted = extract_pdf_result(body, args)
        elif resolved_action == "fetch_text":
            extracted = extract_plain_text_result(body, content_type, args)
        else:
            extracted = extract_html_result(body, final_url, content_type, args)
        base.update(extracted)
        base["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        return base
    except Exception as exc:  # noqa: BLE001
        base.update(
            {
                "status": "failed",
                "status_reason": "extract_failed",
                "error_type": type(exc).__name__,
                "error_message": repr(exc),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        return base


def base_extraction_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "url_hash": row.get("url_hash") or "",
        "normalized_url": row.get("normalized_url") or "",
        "requested_url": row.get("requested_url") or "",
        "final_url": row.get("final_url") or "",
        "final_url_hash": row.get("final_url_hash") or "",
        "domain": row.get("domain") or "",
        "registered_domain": row.get("registered_domain") or "",
        "final_action": row.get("final_action") or "",
        "resolved_action": row.get("resolved_action") or "",
        "download_status": row.get("status") or "",
        "http_status": row.get("http_status") or 0,
        "content_type": row.get("content_type") or "",
        "content_length": row.get("content_length") or 0,
        "downloaded_at_utc": row.get("downloaded_at_utc") or "",
        "artifact_path": row.get("artifact_path") or "",
        "artifact_compression": row.get("artifact_compression") or "",
        "artifact_sha256": row.get("artifact_sha256") or "",
        "status": "started",
        "status_reason": "",
        "title": "",
        "canonical_url": "",
        "extracted_text": "",
        "extracted_text_chars": 0,
        "extracted_text_hash": "",
        "extraction_method": "",
        "extraction_quality": "unknown",
        "quality_flags": [],
        "pdf_page_count": 0,
        "pdf_metadata_json": "[]",
        "error_type": "",
        "error_message": "",
        "elapsed_seconds": 0.0,
        "occurrence_count": row.get("occurrence_count") or 0,
        "sample_provider_article_ids": row.get("sample_provider_article_ids") or [],
        "sample_canonical_news_ids": row.get("sample_canonical_news_ids") or [],
    }


def read_artifact(path: Path, compression: str) -> bytes:
    data = path.read_bytes()
    if compression == "gzip" or path.name.endswith(".gz"):
        return gzip.decompress(data)
    return data


def load_completed_url_hashes(output_root: Path) -> set[str]:
    completed: set[str] = set()
    for path in output_root.glob("*/news_url_extraction_result.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        print(f"WARN skipping malformed extraction resume line path={path} line={line_number}", flush=True)
                        continue
                    if row.get("url_hash") and row.get("status") != "failed":
                        completed.add(str(row["url_hash"]))
        except OSError:
            continue
    return completed


def print_progress(prefix: str, processed: int, total: int, submitted: int, pending: int, counters: Counter[str], started: float) -> float:
    elapsed = time.perf_counter() - started
    rate = processed / elapsed if elapsed > 0 else 0.0
    print(
        f"{prefix}=processed {processed:,}/{total:,} submitted={submitted:,} pending={pending:,} "
        f"rate={rate:.2f}/s statuses={dict(counters)} elapsed={elapsed:.1f}s",
        flush=True,
    )
    return time.perf_counter()


if __name__ == "__main__":
    main()
