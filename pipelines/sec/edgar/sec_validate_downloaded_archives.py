from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from pipelines.sec.edgar.sec_archive_content_discovery import (  # noqa: E402
    empty_aggregate,
    finalize_aggregate,
    merge_aggregate,
    rank_samples,
    scan_archive,
    terminate_process_pool,
    write_jsonl,
)


DEFAULT_DOWNLOADER_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_daily_feed_archives")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_downloaded_archive_validation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate only SEC daily archives selected from a downloader manifest. This is "
            "intended for post-redownload checks, so you do not need to rerun full content "
            "discovery over every historical archive."
        )
    )
    parser.add_argument(
        "--manifest-jsonl",
        default="",
        help="Downloader manifest JSONL. If omitted, latest sec_daily_feed_archives_*.jsonl is used.",
    )
    parser.add_argument(
        "--downloader-output-root-win",
        default=os.environ.get("SEC_DAILY_FEED_OUTPUT_ROOT_WIN", str(DEFAULT_DOWNLOADER_OUTPUT_ROOT_WIN)),
        help="Folder used to find the latest downloader manifest when --manifest-jsonl is omitted.",
    )
    parser.add_argument(
        "--output-root-win",
        default=os.environ.get("SEC_DOWNLOADED_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)),
    )
    parser.add_argument(
        "--manifest-artifact-root-win",
        default=os.environ.get("SEC_DOWNLOADED_ARCHIVE_VALIDATION_MANIFEST_ARTIFACT_ROOT_WIN", ""),
        help="Optional root prefix recorded in manifest artifact_path values, for path remapping.",
    )
    parser.add_argument(
        "--archive-root-win",
        default=os.environ.get("SEC_DOWNLOADED_ARCHIVE_VALIDATION_ARCHIVE_ROOT_WIN", ""),
        help="Optional local/archive-share root used to remap manifest artifact_path values.",
    )
    parser.add_argument("--status", default="downloaded", help="Downloader row status to validate. Default: downloaded.")
    parser.add_argument("--expected-count", type=int, default=0, help="Abort if selected manifest rows differ from this value.")
    parser.add_argument("--archive-workers", type=int, default=int(os.environ.get("SEC_DOWNLOADED_ARCHIVE_VALIDATION_WORKERS", "4")))
    parser.add_argument("--pending-multiplier", type=int, default=1, help="Maximum queued validation jobs per worker.")
    parser.add_argument("--limit-archives", type=int, default=0, help="Optional smoke-test cap after manifest filtering.")
    parser.add_argument("--max-filings-per-archive", type=int, default=0, help="Optional per-archive cap; 0 scans all filings.")
    parser.add_argument("--sample-limit", type=int, default=250)
    parser.add_argument("--sample-text-chars", type=int, default=600)
    parser.add_argument("--progress-every", type=int, default=5)
    parser.add_argument("--hash-archives", action="store_true", help="Compute archive SHA-256 prefixes during validation. Disabled by default.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=False)

    manifest_path = resolve_manifest_path(args)
    output_root = Path(args.output_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    manifest_rows = load_manifest_rows(manifest_path, args.status)
    if args.expected_count and len(manifest_rows) != args.expected_count:
        raise SystemExit(f"expected {args.expected_count:,} manifest rows but found {len(manifest_rows):,}")
    if args.limit_archives:
        manifest_rows = manifest_rows[: max(0, args.limit_archives)]
    if not manifest_rows:
        raise SystemExit(f"no manifest rows found with status={args.status!r}")

    archives = [resolve_archive_path(str(row["artifact_path"]), args) for row in manifest_rows]
    for archive in archives:
        if not archive.exists():
            raise SystemExit(f"selected archive does not exist: {archive}")

    validation_manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).resolve()),
        "downloader_manifest_jsonl": str(manifest_path),
        "manifest_artifact_root": args.manifest_artifact_root_win,
        "archive_root": args.archive_root_win,
        "output_root": str(output_root),
        "run_root": str(run_root),
        "status_filter": args.status,
        "selected_archive_count": len(archives),
        "archive_workers": max(1, args.archive_workers),
        "pending_multiplier": max(1, args.pending_multiplier),
        "max_filings_per_archive": max(0, args.max_filings_per_archive),
        "sample_limit": max(0, args.sample_limit),
        "sample_text_chars": max(0, args.sample_text_chars),
        "hash_archives": bool(args.hash_archives),
        "loaded_env_files": [str(path) for path in loaded_env_files],
    }
    (run_root / "validation_manifest.json").write_text(json.dumps(validation_manifest, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 96, flush=True)
    print("SEC downloaded archive validation", flush=True)
    print(f"downloader_manifest={manifest_path}", flush=True)
    print(f"status_filter={args.status} selected_archives={len(archives):,}", flush=True)
    print(f"workers={max(1, args.archive_workers)} run_root={run_root}", flush=True)
    print("=" * 96, flush=True)

    aggregate = empty_aggregate()
    sample_reservoir: list[dict[str, Any]] = []
    archive_summary_path = run_root / "archive_summary.jsonl"
    samples_path = run_root / "document_samples.jsonl"
    aggregate_path = run_root / "aggregate_summary.json"
    manifest_selection_path = run_root / "selected_downloader_rows.jsonl"
    write_jsonl(manifest_selection_path, manifest_rows)

    completed = validate_archives(args, archives, archive_summary_path, aggregate, sample_reservoir, started)
    sample_reservoir = rank_samples(sample_reservoir)[: max(0, args.sample_limit)]
    write_jsonl(samples_path, sample_reservoir)
    final_summary = finalize_aggregate(aggregate, validation_manifest, time.perf_counter() - started)
    final_summary["completed_archives"] = completed
    aggregate_path.write_text(json.dumps(final_summary, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 96, flush=True)
    print(f"completed_archives={completed:,}/{len(archives):,}", flush=True)
    print(f"failed_archives={aggregate['failed_archives']:,}", flush=True)
    print(f"archive_summary={archive_summary_path}", flush=True)
    print(f"aggregate_summary={aggregate_path}", flush=True)
    print("=" * 96, flush=True)
    if aggregate["failed_archives"]:
        raise SystemExit(2)


def validate_archives(
    args: argparse.Namespace,
    archives: list[Path],
    archive_summary_path: Path,
    aggregate: dict[str, Any],
    sample_reservoir: list[dict[str, Any]],
    started: float,
) -> int:
    workers = max(1, args.archive_workers)
    max_pending = max(workers, workers * max(1, args.pending_multiplier))
    archive_iter = iter(archives)
    completed = 0
    submitted = 0
    futures: dict[concurrent.futures.Future[dict[str, Any]], Path] = {}
    pool: concurrent.futures.ProcessPoolExecutor | None = None

    def submit_one() -> bool:
        nonlocal submitted
        try:
            path = next(archive_iter)
        except StopIteration:
            return False
        future = pool.submit(  # type: ignore[union-attr]
            scan_archive,
            str(path),
            max(0, args.max_filings_per_archive),
            max(0, args.sample_text_chars),
            max(0, args.sample_limit),
            bool(args.hash_archives),
        )
        futures[future] = path
        submitted += 1
        return True

    with archive_summary_path.open("w", encoding="utf-8") as archive_out:
        pool = concurrent.futures.ProcessPoolExecutor(max_workers=workers)
        try:
            while len(futures) < max_pending and submit_one():
                pass
            print(f"submitted_initial={submitted:,} max_pending={max_pending:,}", flush=True)

            while futures:
                done, _ = concurrent.futures.wait(futures, timeout=5.0, return_when=concurrent.futures.FIRST_COMPLETED)
                if not done:
                    elapsed = time.perf_counter() - started
                    print(
                        f"active={len(futures):,} submitted={submitted:,}/{len(archives):,} "
                        f"completed={completed:,} failed={aggregate['failed_archives']:,} "
                        f"filings={aggregate['filings']:,} documents={aggregate['documents']:,} "
                        f"elapsed={elapsed:.1f}s",
                        flush=True,
                    )
                    continue

                for future in done:
                    path = futures.pop(future)
                    completed += 1
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - worker exception report path
                        summary = failed_archive_summary(path, repr(exc))
                    else:
                        summary = result["summary"]
                        sample_reservoir.extend(result["samples"])
                        if len(sample_reservoir) > args.sample_limit * 3 and args.sample_limit:
                            sample_reservoir[:] = rank_samples(sample_reservoir)[: max(0, args.sample_limit)]

                    archive_out.write(json.dumps(summary, sort_keys=True) + "\n")
                    archive_out.flush()
                    merge_aggregate(aggregate, summary)

                    while len(futures) < max_pending and submit_one():
                        pass
                    if completed == 1 or completed % max(1, args.progress_every) == 0 or completed == len(archives):
                        elapsed = time.perf_counter() - started
                        print(
                            f"completed={completed:,}/{len(archives):,} submitted={submitted:,} "
                            f"active={len(futures):,} failed={aggregate['failed_archives']:,} "
                            f"filings={aggregate['filings']:,} documents={aggregate['documents']:,} "
                            f"elapsed={elapsed:.1f}s",
                            flush=True,
                        )
        except KeyboardInterrupt:
            print("KeyboardInterrupt received; terminating archive workers and writing partial outputs.", flush=True)
            aggregate["interrupted"] = 1
            terminate_process_pool(pool)
            return completed
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
    return completed


def resolve_manifest_path(args: argparse.Namespace) -> Path:
    if args.manifest_jsonl:
        path = Path(args.manifest_jsonl)
    else:
        path = latest_downloader_manifest(Path(args.downloader_output_root_win))
    if not path.exists():
        raise SystemExit(f"manifest does not exist: {path}")
    if not path.is_file():
        raise SystemExit(f"manifest is not a file: {path}")
    return path.resolve()


def latest_downloader_manifest(output_root: Path) -> Path:
    if not output_root.exists():
        raise SystemExit(f"downloader output root does not exist: {output_root}")
    candidates = [
        path
        for path in output_root.glob("sec_daily_feed_archives_*.jsonl")
        if not path.name.startswith("sec_daily_feed_archives_summary_")
    ]
    if not candidates:
        raise SystemExit(f"no downloader manifests found under: {output_root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_manifest_rows(manifest_path: Path, status: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    with manifest_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid JSON in {manifest_path} line {line_number}: {exc}") from exc
            if row.get("status") != status:
                continue
            artifact_path = str(row.get("artifact_path") or "")
            if not artifact_path:
                raise SystemExit(f"manifest row {line_number} has no artifact_path")
            path_key = os.path.normcase(artifact_path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            rows.append(row)
    return rows


def resolve_archive_path(artifact_path: str, args: argparse.Namespace) -> Path:
    path = Path(artifact_path)
    archive_root = str(args.archive_root_win or "")
    if not archive_root:
        return path

    manifest_root = str(args.manifest_artifact_root_win or "")
    if manifest_root:
        relative = relative_to_windows_root(PureWindowsPath(artifact_path), PureWindowsPath(manifest_root))
        if relative is not None:
            return Path(archive_root) / Path(*relative.parts)

    parts = PureWindowsPath(artifact_path).parts
    lowered = [part.lower() for part in parts]
    if "daily_archives" in lowered:
        index = lowered.index("daily_archives") + 1
        return Path(archive_root) / Path(*parts[index:])
    return path


def relative_to_windows_root(path: PureWindowsPath, root: PureWindowsPath) -> PureWindowsPath | None:
    path_parts = [part.lower() for part in path.parts]
    root_parts = [part.lower() for part in root.parts]
    if len(path_parts) < len(root_parts):
        return None
    if path_parts[: len(root_parts)] != root_parts:
        return None
    return PureWindowsPath(*path.parts[len(root_parts) :])


def failed_archive_summary(path: Path, error: str) -> dict[str, Any]:
    archive_date = path.name[:8]
    archive_date_iso = f"{archive_date[:4]}-{archive_date[4:6]}-{archive_date[6:8]}" if len(archive_date) == 8 else ""
    archive_bytes = path.stat().st_size if path.exists() else 0
    return {
        "archive_date": archive_date_iso,
        "archive_path": str(path),
        "archive_bytes": archive_bytes,
        "archive_sha256_prefix": "",
        "status": "failed",
        "error": error,
        "members": 0,
        "filings": 0,
        "documents": 0,
        "parse_errors": 0,
        "truncated_by_limit": False,
        "forms": {},
        "document_types": {},
        "content_formats": {},
        "file_extensions": {},
        "document_type_by_format": {},
        "form_by_document_type": {},
        "payload_chars_by_format": {},
        "clean_text_chars_by_format": {},
        "empty_text_documents": 0,
        "binary_like_documents": 0,
        "non_ascii_documents": 0,
        "replacement_char_documents": 0,
        "mojibake_suspect_documents": 0,
        "max_payload_chars": 0,
        "max_clean_text_chars": 0,
    }


if __name__ == "__main__":
    main()
