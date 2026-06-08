from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import multiprocessing as mp
import os
import sys
import time
import ssl
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request

from tqdm import tqdm


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_START_DATE = "2022-01-01"
DEFAULT_END_DATE = "2022-12-31"
DEFAULT_PROCESSES = 8
DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_AWS_SERVICE = "s3"
KIND_PREFIXES = {
    "quotes": "us_stocks_sip/quotes_v1",
    "trades": "us_stocks_sip/trades_v1",
}
ENV_KEYS = [
    "MASSIVE_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_ENDPOINT_URL",
    "BUCKET",
    "FLATFILES_ROOT",
]


@dataclass(frozen=True, slots=True)
class DownloadJob:
    kind: str
    session_date: str
    key: str
    destination: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    worker_id: int
    kind: str
    session_date: str
    key: str
    destination: str
    status: str
    bytes_expected: int = 0
    bytes_written: int = 0
    wall_seconds: float = 0.0
    exception: str = ""


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    endpoint_url: str
    bucket: str
    access_key: str
    secret_key: str
    region: str
    service: str
    timeout_seconds: float
    chunk_bytes: int
    verify_tls: bool
    overwrite_incomplete: bool
    dry_run: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Massive stock SIP quote/trade flatfiles concurrently from the S3-compatible flatfiles bucket. "
            "The local directory structure matches the S3 object key structure."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD start date.")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD end date.")
    parser.add_argument("--kinds", default="quotes,trades", help="Comma-separated subset: quotes,trades.")
    parser.add_argument("--processes", type=int, default=DEFAULT_PROCESSES)
    parser.add_argument("--flatfiles-root", default="", help="Destination root. Defaults to FLATFILES_ROOT.")
    parser.add_argument("--endpoint-url", default="", help="S3 endpoint. Defaults to S3_ENDPOINT_URL.")
    parser.add_argument("--bucket", default="", help="S3 bucket. Defaults to BUCKET.")
    parser.add_argument("--aws-access-key-id", default="", help="Defaults to AWS_ACCESS_KEY_ID.")
    parser.add_argument("--aws-secret-access-key", default="", help="Defaults to AWS_SECRET_ACCESS_KEY.")
    parser.add_argument("--aws-region", default=DEFAULT_AWS_REGION)
    parser.add_argument("--aws-service", default=DEFAULT_AWS_SERVICE)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--limit-files", type=int, default=0, help="Debug limit after job discovery. 0 means no limit.")
    parser.add_argument("--report-path", default="", help="Optional JSONL report path.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-verify-tls", action="store_true")
    parser.add_argument(
        "--keep-incomplete",
        action="store_true",
        help="Keep incomplete existing destination files instead of replacing them.",
    )
    return parser.parse_args()


def iter_dates(start: str, end: str) -> Iterable[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError(f"end-date {end} is before start-date {start}")
    current = start_date
    while current <= end_date:
        yield current.isoformat()
        current += timedelta(days=1)


def parse_kinds(raw: str) -> list[str]:
    kinds = [item.strip() for item in raw.split(",") if item.strip()]
    invalid = [kind for kind in kinds if kind not in KIND_PREFIXES]
    if invalid:
        raise ValueError(f"Invalid kinds {invalid}; expected subset of {sorted(KIND_PREFIXES)}")
    if not kinds:
        raise ValueError("--kinds must include at least one kind")
    return kinds


def object_key(kind: str, session_date: str) -> str:
    year = session_date[:4]
    month = session_date[5:7]
    return f"{KIND_PREFIXES[kind]}/{year}/{month}/{session_date}.csv.gz"


def build_jobs(flatfiles_root: Path, start_date: str, end_date: str, kinds: list[str]) -> list[DownloadJob]:
    jobs: list[DownloadJob] = []
    for session_date in iter_dates(start_date, end_date):
        for kind in kinds:
            key = object_key(kind, session_date)
            jobs.append(DownloadJob(kind=kind, session_date=session_date, key=key, destination=str(flatfiles_root / key)))
    return jobs


def env_value(cli_value: str, key: str, *, required: bool = True) -> str:
    value = cli_value or os.environ.get(key, "")
    if required and not value:
        raise RuntimeError(f"Missing required configuration {key}. Set it in .env or pass the matching CLI argument.")
    return value


def canonical_query(params: dict[str, str]) -> str:
    return "&".join(
        f"{parse.quote(key, safe='-_.~')}={parse.quote(value, safe='-_.~')}"
        for key, value in sorted(params.items())
    )


def sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    key_region = sign(key_date, region)
    key_service = sign(key_region, service)
    return sign(key_service, "aws4_request")


def signed_headers(
    *,
    method: str,
    endpoint_url: str,
    bucket: str,
    key: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    query_params: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    endpoint = endpoint_url.rstrip("/")
    parsed = parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid S3 endpoint URL: {endpoint_url}")
    canonical_uri = "/" + parse.quote(f"{bucket}/{key}", safe="/-_.~")
    query_params = query_params or {}
    canonical_qs = canonical_query(query_params)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp[:8]
    host = parsed.netloc
    payload_hash = "UNSIGNED-PAYLOAD"
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{timestamp}\n"
    signed_header_names = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_qs, canonical_headers, signed_header_names, payload_hash]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            timestamp,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        signing_key(secret_key, date_stamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_header_names}, Signature={signature}"
    )
    url = f"{endpoint}{canonical_uri}"
    if canonical_qs:
        url += f"?{canonical_qs}"
    return url, {
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": timestamp,
    }


def signed_request(config: DownloadConfig, method: str, key: str) -> request.Request:
    url, headers = signed_headers(
        method=method,
        endpoint_url=config.endpoint_url,
        bucket=config.bucket,
        key=key,
        access_key=config.access_key,
        secret_key=config.secret_key,
        region=config.region,
        service=config.service,
    )
    return request.Request(url, headers=headers, method=method)


def urlopen_signed(req: request.Request, config: DownloadConfig):
    context = None if config.verify_tls else ssl._create_unverified_context()
    return request.urlopen(req, timeout=config.timeout_seconds, context=context)


def remote_size(config: DownloadConfig, key: str) -> int | None:
    req = signed_request(config, "HEAD", key)
    try:
        with urlopen_signed(req, config) as response:
            return int(response.headers.get("Content-Length", "0"))
    except error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HEAD failed for {key}: HTTP {exc.code} {exc.reason}: {body}") from exc


def download_one(config: DownloadConfig, job: DownloadJob, worker_id: int) -> DownloadResult:
    t0 = time.time()
    destination = Path(job.destination)
    part_path = destination.with_name(destination.name + ".part")
    try:
        expected_size = remote_size(config, job.key)
        if expected_size is None:
            return DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "missing_remote", wall_seconds=time.time() - t0)
        if destination.exists():
            local_size = destination.stat().st_size
            if local_size == expected_size:
                return DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "skipped_complete", expected_size, local_size, time.time() - t0)
            if not config.overwrite_incomplete:
                return DownloadResult(
                    worker_id,
                    job.kind,
                    job.session_date,
                    job.key,
                    str(destination),
                    "incomplete_existing",
                    expected_size,
                    local_size,
                    time.time() - t0,
                    f"existing size {local_size} != remote size {expected_size}",
                )
        if config.dry_run:
            return DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "would_download", expected_size, 0, time.time() - t0)

        destination.parent.mkdir(parents=True, exist_ok=True)
        if part_path.exists():
            part_path.unlink()
        req = signed_request(config, "GET", job.key)
        bytes_written = 0
        with urlopen_signed(req, config) as response, part_path.open("wb") as handle:
            while True:
                chunk = response.read(config.chunk_bytes)
                if not chunk:
                    break
                handle.write(chunk)
                bytes_written += len(chunk)
        actual_size = part_path.stat().st_size
        if actual_size != expected_size:
            return DownloadResult(
                worker_id,
                job.kind,
                job.session_date,
                job.key,
                str(destination),
                "failed_size_mismatch",
                expected_size,
                actual_size,
                time.time() - t0,
                f"downloaded {actual_size} bytes, expected {expected_size}",
            )
        if destination.exists():
            destination.unlink()
        part_path.replace(destination)
        return DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "downloaded", expected_size, actual_size, time.time() - t0)
    except Exception as exc:
        return DownloadResult(worker_id, job.kind, job.session_date, job.key, str(destination), "failed", wall_seconds=time.time() - t0, exception=repr(exc))


def worker_main(worker_id: int, jobs: list[DownloadJob], config: DownloadConfig, queue: mp.Queue | None) -> list[DownloadResult]:
    results: list[DownloadResult] = []
    counters: dict[str, int] = {
        "downloaded": 0,
        "skipped": 0,
        "missing": 0,
        "failed": 0,
    }
    bar = tqdm(
        total=len(jobs),
        desc=f"worker {worker_id:02d}",
        position=worker_id,
        dynamic_ncols=True,
        leave=True,
    )
    for job in jobs:
        result = download_one(config, job, worker_id)
        results.append(result)
        if queue is not None:
            queue.put(asdict(result))
        if result.status in ("downloaded", "would_download"):
            counters["downloaded"] += 1
        elif result.status.startswith("skipped") or result.status == "incomplete_existing":
            counters["skipped"] += 1
        elif result.status == "missing_remote":
            counters["missing"] += 1
        else:
            counters["failed"] += 1
        bar.set_postfix(
            dl=counters["downloaded"],
            skip=counters["skipped"],
            miss=counters["missing"],
            fail=counters["failed"],
            refresh=True,
        )
        bar.update(1)
    bar.close()
    return results


def result_writer(report_path: Path, queue: mp.Queue, expected_done_messages: int) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with report_path.open("a", encoding="utf-8") as handle:
        while done < expected_done_messages:
            item = queue.get()
            if item == {"type": "worker_done"}:
                done += 1
                continue
            handle.write(json.dumps(item, sort_keys=True) + "\n")
            handle.flush()


def split_jobs(jobs: list[DownloadJob], processes: int) -> list[list[DownloadJob]]:
    chunks = [[] for _ in range(processes)]
    for idx, job in enumerate(jobs):
        chunks[idx % processes].append(job)
    return chunks


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    kinds = parse_kinds(args.kinds)
    flatfiles_root_raw = env_value(args.flatfiles_root, "FLATFILES_ROOT")
    flatfiles_root = Path(flatfiles_root_raw)
    config = DownloadConfig(
        endpoint_url=env_value(args.endpoint_url, "S3_ENDPOINT_URL"),
        bucket=env_value(args.bucket, "BUCKET"),
        access_key=env_value(args.aws_access_key_id, "AWS_ACCESS_KEY_ID"),
        secret_key=env_value(args.aws_secret_access_key, "AWS_SECRET_ACCESS_KEY"),
        region=args.aws_region,
        service=args.aws_service,
        timeout_seconds=float(args.timeout_seconds),
        chunk_bytes=int(args.chunk_bytes),
        verify_tls=not args.no_verify_tls,
        overwrite_incomplete=not args.keep_incomplete,
        dry_run=bool(args.dry_run),
    )
    jobs = build_jobs(flatfiles_root, args.start_date, args.end_date, kinds)
    if args.limit_files > 0:
        jobs = jobs[: args.limit_files]
    processes = max(1, min(int(args.processes), len(jobs) or 1))
    chunks = split_jobs(jobs, processes)
    default_report = flatfiles_root / "_download_reports" / f"massive_sip_flatfiles_download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    report_path = Path(args.report_path) if args.report_path else default_report

    print("=" * 96, flush=True)
    print("Massive SIP flatfile downloader", flush=True)
    print(f"date_range={args.start_date} -> {args.end_date}", flush=True)
    print(f"kinds={kinds}", flush=True)
    print(f"jobs={len(jobs):,} processes={processes}", flush=True)
    print(f"endpoint={config.endpoint_url} bucket={config.bucket}", flush=True)
    print(f"flatfiles_root={flatfiles_root}", flush=True)
    print(f"report_path={report_path}", flush=True)
    print(f"dry_run={config.dry_run} overwrite_incomplete={config.overwrite_incomplete}", flush=True)
    print(f"secret_status={secret_status(ENV_KEYS)}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    manager = mp.Manager()
    queue = manager.Queue()
    writer = mp.Process(target=result_writer, args=(report_path, queue, processes), daemon=True)
    writer.start()
    result_sets: list[list[DownloadResult]] = []
    with mp.Pool(processes=processes) as pool:
        async_results = [
            pool.apply_async(worker_main, (worker_id, chunk, config, queue))
            for worker_id, chunk in enumerate(chunks)
        ]
        for async_result in async_results:
            result_sets.append(async_result.get())
    for _ in range(processes):
        queue.put({"type": "worker_done"})
    writer.join()

    all_results = [result for result_set in result_sets for result in result_set]
    counts: dict[str, int] = {}
    total_downloaded_bytes = 0
    for result in all_results:
        counts[result.status] = counts.get(result.status, 0) + 1
        if result.status == "downloaded":
            total_downloaded_bytes += result.bytes_written
    print("\n" + "=" * 96, flush=True)
    print("Download summary", flush=True)
    for status in sorted(counts):
        print(f"{status}: {counts[status]:,}", flush=True)
    print(f"downloaded_bytes={total_downloaded_bytes:,}", flush=True)
    print(f"downloaded_gib={total_downloaded_bytes / (1024**3):.3f}", flush=True)
    print(f"report_path={report_path}", flush=True)
    failed = sum(count for status, count in counts.items() if status.startswith("failed"))
    incomplete = counts.get("incomplete_existing", 0)
    if failed or incomplete:
        raise SystemExit(f"Download completed with failed={failed:,} incomplete_existing={incomplete:,}. See report: {report_path}")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
