from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.news_benzinga_normalize import (  # noqa: E402
    artifact_path_for_payload,
    parse_provider_datetime,
    to_provider_rfc3339,
    write_raw_payload,
)


DEFAULT_ENDPOINT = "https://api.massive.com/benzinga/v2/news"
DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/benzinga_news_canonical")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_ingest")
BUCKET_MINUTES = 90
PAGE_LIMIT = 1000
MAX_PAGES = 1000
PROVIDER_RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class RawDownloadJob:
    bucket_id: str
    start_utc: str
    end_utc: str
    endpoint_url: str
    api_key: str
    artifact_root_win: str


@dataclass(frozen=True, slots=True)
class RawArtifact:
    raw_artifact_path: str
    raw_payload_hash: str
    downloaded_at_utc: str


@dataclass(frozen=True, slots=True)
class RawDownloadError:
    bucket_id: str
    raw_artifact_path: str = ""
    provider_article_id: str = ""
    published_raw: str = ""
    exception: str = ""
    traceback: str = ""


@dataclass(frozen=True, slots=True)
class RawDownloadResult:
    bucket_id: str
    start_utc: str
    end_utc: str
    artifacts: list[RawArtifact]
    errors: list[RawDownloadError]
    downloaded_rows: int
    page_count: int
    saturated: int
    wall_seconds: float
    exception: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download raw Benzinga news payloads only; no normalization, enrichment, or database writes.")
    parser.add_argument("--start-utc", required=True, help="Inclusive UTC start, e.g. 2024-01-01T00:00:00Z or 2024-01-01.")
    parser.add_argument("--end-utc", required=True, help="Exclusive UTC end, e.g. 2024-02-01T00:00:00Z or 2024-02-01.")
    parser.add_argument("--download-processes", type=int, required=True, help="Number of parallel bucket download workers.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    download_processes = max(1, args.download_processes)
    api_key = os.environ.get("MASSIVE_API_KEY", "")
    if not api_key:
        raise RuntimeError("MASSIVE_API_KEY is required")
    endpoint_url = os.environ.get("NEWS_BENZINGA_URL") or os.environ.get("NEWS_MASSIVE_BENZINGA_URL", DEFAULT_ENDPOINT)
    artifact_root = Path(os.environ.get("NEWS_BENZINGA_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    output_root = Path(os.environ.get("NEWS_BENZINGA_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_path = output_root / f"benzinga_raw_download_{run_id}.jsonl"
    jobs = build_jobs(args.start_utc, args.end_utc, endpoint_url, api_key, str(artifact_root))

    print("=" * 96, flush=True)
    print("Raw Benzinga news download only", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"start_utc={args.start_utc} end_utc={args.end_utc} bucket_minutes={BUCKET_MINUTES}", flush=True)
    print(f"buckets={len(jobs):,} download_processes={download_processes} limit={PAGE_LIMIT} max_pages={MAX_PAGES}", flush=True)
    print(f"endpoint_url={endpoint_url}", flush=True)
    print(f"artifact_root={artifact_root}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(['MASSIVE_API_KEY'])}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)
    append_jsonl(
        report_path,
        {
            "type": "config",
            "run_id": run_id,
            "start_utc": args.start_utc,
            "end_utc": args.end_utc,
            "download_processes": download_processes,
            "bucket_minutes": BUCKET_MINUTES,
            "limit": PAGE_LIMIT,
            "max_pages": MAX_PAGES,
            "artifact_root": str(artifact_root),
            "endpoint_url": endpoint_url,
            "bucket_count": len(jobs),
            "api_key": "present",
        },
    )

    started_at = time.perf_counter()
    completed = 0
    failed = 0
    downloaded_rows = 0
    artifact_count = 0
    page_count = 0
    saturated = 0

    with concurrent.futures.ProcessPoolExecutor(max_workers=download_processes) as pool:
        futures = {pool.submit(download_bucket, job): job for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = RawDownloadResult(
                    bucket_id=job.bucket_id,
                    start_utc=job.start_utc,
                    end_utc=job.end_utc,
                    artifacts=[],
                    errors=[
                        RawDownloadError(
                            bucket_id=job.bucket_id,
                            exception=repr(exc),
                            traceback=traceback.format_exc(),
                        )
                    ],
                    downloaded_rows=0,
                    page_count=0,
                    saturated=0,
                    wall_seconds=0.0,
                    exception=repr(exc),
                )
            append_jsonl(report_path, {"type": "bucket", "run_id": run_id, "result": public_result(result)})
            for item in result.errors:
                append_jsonl(report_path, {"type": "error", "run_id": run_id, "error": asdict(item)})
            if result.exception:
                failed += 1
            else:
                completed += 1
            downloaded_rows += result.downloaded_rows
            artifact_count += len(result.artifacts)
            page_count += result.page_count
            saturated += result.saturated
            print_progress(
                total=len(jobs),
                completed=completed,
                failed=failed,
                downloaded_rows=downloaded_rows,
                artifact_count=artifact_count,
                page_count=page_count,
                saturated=saturated,
                started_at=started_at,
            )

    elapsed = time.perf_counter() - started_at
    print("=" * 96, flush=True)
    print(
        f"DONE completed={completed:,} failed={failed:,} downloaded_rows={downloaded_rows:,} "
        f"artifacts={artifact_count:,} pages={page_count:,} saturated_buckets={saturated:,}",
        flush=True,
    )
    print(f"elapsed_min={elapsed / 60:.1f} report={report_path}", flush=True)
    print("=" * 96, flush=True)


def build_jobs(start_utc: str, end_utc: str, endpoint_url: str, api_key: str, artifact_root_win: str) -> list[RawDownloadJob]:
    start = parse_input_datetime(start_utc)
    end = parse_input_datetime(end_utc)
    if end <= start:
        raise ValueError("--end-utc must be after --start-utc")
    jobs: list[RawDownloadJob] = []
    current = start
    step = timedelta(minutes=BUCKET_MINUTES)
    while current < end:
        bucket_end = min(current + step, end)
        jobs.append(
            RawDownloadJob(
                bucket_id=bucket_identity(current, bucket_end),
                start_utc=to_provider_rfc3339(current),
                end_utc=to_provider_rfc3339(bucket_end),
                endpoint_url=endpoint_url,
                api_key=api_key,
                artifact_root_win=artifact_root_win,
            )
        )
        current = bucket_end
    return jobs


def download_bucket(job: RawDownloadJob) -> RawDownloadResult:
    started_at = time.perf_counter()
    artifact_root = Path(job.artifact_root_win)
    artifacts: list[RawArtifact] = []
    errors: list[RawDownloadError] = []
    downloaded_rows = 0
    page_count = 0
    saturated = 0
    next_url: str | None = build_benzinga_url(job.endpoint_url, job.api_key, job.start_utc, job.end_utc)
    try:
        while next_url and page_count < MAX_PAGES:
            page_count += 1
            response = fetch_json(next_url)
            items = response.get("results") or []
            downloaded_rows += len(items)
            downloaded_at = to_provider_rfc3339(datetime.now(UTC))
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw_path: Path | None = None
                try:
                    try:
                        published = parse_provider_datetime(str(item.get("published") or ""))
                    except Exception:
                        published = datetime.now(UTC)
                    raw_path = artifact_path_for_payload(artifact_root, item, published)
                    raw_hash = write_raw_payload(raw_path, item)
                    artifacts.append(
                        RawArtifact(
                            raw_artifact_path=str(raw_path),
                            raw_payload_hash=raw_hash,
                            downloaded_at_utc=downloaded_at,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        RawDownloadError(
                            bucket_id=job.bucket_id,
                            raw_artifact_path=str(raw_path or ""),
                            provider_article_id=provider_id(item),
                            published_raw=str(item.get("published") or ""),
                            exception=repr(exc),
                            traceback=traceback.format_exc(),
                        )
                    )
            next_url = response.get("next_url")
            if next_url:
                next_url = append_api_key(str(next_url), job.api_key)
        if next_url:
            saturated = 1
    except Exception as exc:  # noqa: BLE001
        return RawDownloadResult(
            bucket_id=job.bucket_id,
            start_utc=job.start_utc,
            end_utc=job.end_utc,
            artifacts=artifacts,
            errors=errors,
            downloaded_rows=downloaded_rows,
            page_count=page_count,
            saturated=saturated,
            wall_seconds=time.perf_counter() - started_at,
            exception=repr(exc),
        )
    return RawDownloadResult(
        bucket_id=job.bucket_id,
        start_utc=job.start_utc,
        end_utc=job.end_utc,
        artifacts=artifacts,
        errors=errors,
        downloaded_rows=downloaded_rows,
        page_count=page_count,
        saturated=saturated,
        wall_seconds=time.perf_counter() - started_at,
    )


def build_benzinga_url(endpoint_url: str, api_key: str, start_utc: str, end_utc: str) -> str:
    params = {
        "published.gte": start_utc,
        "published.lt": end_utc,
        "limit": str(PAGE_LIMIT),
        "sort": "published.asc",
        "apiKey": api_key,
    }
    separator = "&" if "?" in endpoint_url else "?"
    return endpoint_url.rstrip("?&") + separator + parse.urlencode(params)


def append_api_key(url: str, api_key: str) -> str:
    if "apiKey=" in url:
        return url
    return url + ("&" if "?" in url else "?") + parse.urlencode({"apiKey": api_key})


def fetch_json(url: str) -> dict[str, Any]:
    request_obj = request.Request(url, headers={"User-Agent": "quant-research-workbench-benzinga-raw-download/1.0"})
    attempts = 4
    body = ""
    for attempt in range(1, attempts + 1):
        try:
            with request.urlopen(request_obj, timeout=60) as response:  # noqa: S310
                body = response.read().decode("utf-8", errors="replace")
                break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code not in PROVIDER_RETRY_HTTP_CODES or attempt >= attempts:
                raise RuntimeError(f"Massive Benzinga HTTP {exc.code}: {body}") from exc
            time.sleep(provider_retry_sleep_seconds(exc, attempt))
        except (TimeoutError, error.URLError):
            if attempt >= attempts:
                raise
            time.sleep(provider_retry_sleep_seconds(None, attempt))
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("Massive Benzinga response was not a JSON object")
    return value


def provider_retry_sleep_seconds(exc: error.HTTPError | None, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After", "") if exc is not None else ""
    parsed_retry_after = parse_retry_after_seconds(retry_after)
    if parsed_retry_after is not None:
        return min(300.0, parsed_retry_after)
    return min(300.0, 1.0 * (2 ** (attempt - 1)))


def parse_retry_after_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds())


def parse_input_datetime(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10:
        text += "T00:00:00Z"
    return parse_provider_datetime(text)


def bucket_identity(start: datetime, end: datetime) -> str:
    return hashlib.blake2b(f"{to_provider_rfc3339(start)}|{to_provider_rfc3339(end)}".encode("utf-8"), digest_size=12).hexdigest()


def provider_id(payload: dict[str, Any]) -> str:
    value = payload.get("benzinga_id", payload.get("id", ""))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def public_result(result: RawDownloadResult) -> dict[str, Any]:
    payload = asdict(result)
    artifacts = payload.pop("artifacts", [])
    errors = payload.pop("errors", [])
    payload["artifact_count"] = len(artifacts)
    payload["error_count"] = len(errors)
    return payload


def print_progress(
    *,
    total: int,
    completed: int,
    failed: int,
    downloaded_rows: int,
    artifact_count: int,
    page_count: int,
    saturated: int,
    started_at: float,
) -> None:
    elapsed = time.perf_counter() - started_at
    done = completed + failed
    rate = done / elapsed if elapsed > 0 else 0.0
    remaining = max(0, total - done)
    eta_seconds = remaining / rate if rate > 0 else 0.0
    print(
        f"[raw {done:,}/{total:,}] completed={completed:,} failed={failed:,} "
        f"downloaded_rows={downloaded_rows:,} artifacts={artifact_count:,} pages={page_count:,} "
        f"saturated_buckets={saturated:,} elapsed_min={elapsed / 60:.1f} eta_min={eta_seconds / 60:.1f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
