from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.core.item_pipeline import ItemPipelineOptions, process_benzinga_news_item  # noqa: E402
from pipelines.news.benzinga.core.url_policy import load_policy  # noqa: E402
from pipelines.news.benzinga.news_benzinga_normalize import stable_hash  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


DEFAULT_RAW_ROOT_WIN = Path("D:/market-data/news-benzinga/raw")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_item_pipeline_smoke")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the reusable per-item Benzinga news pipeline on raw historical JSON files.")
    parser.add_argument("--raw-root-win", default=os.environ.get("NEWS_BENZINGA_RAW_ROOT_WIN") or str(DEFAULT_RAW_ROOT_WIN))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_ITEM_PIPELINE_SMOKE_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--policy-json", default=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "")
    parser.add_argument("--limit-files", type=int, default=1000)
    parser.add_argument("--processes", type=int, default=max(1, min(8, os.cpu_count() or 4)))
    parser.add_argument("--text-limit-chars", type=int, default=50_000)
    parser.add_argument("--progress-interval", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    policy = load_policy(args.policy_json)
    raw_root = Path(args.raw_root_win)
    files = discover_raw_files(raw_root, args.limit_files)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    result_path = run_root / "benzinga_item_pipeline_results.jsonl"
    error_path = run_root / "benzinga_item_pipeline_errors.jsonl"
    summary_path = run_root / "benzinga_item_pipeline_summary.json"

    print("=" * 96, flush=True)
    print("Benzinga item pipeline smoke", flush=True)
    print(f"raw_root={raw_root}", flush=True)
    print(f"files={len(files):,} processes={args.processes}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    counters: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    processed = 0
    with result_path.open("w", encoding="utf-8") as results, error_path.open("w", encoding="utf-8") as errors:
        if args.processes <= 1:
            for path in files:
                row = process_one(path, policy, args.text_limit_chars)
                processed += handle_result(row, results, errors, counters, action_counts)
                print_progress(processed, len(files), started, args.progress_interval)
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.processes)) as executor:
                futures = [executor.submit(process_one, path, policy, args.text_limit_chars) for path in files]
                for future in concurrent.futures.as_completed(futures):
                    row = future.result()
                    processed += handle_result(row, results, errors, counters, action_counts)
                    print_progress(processed, len(files), started, args.progress_interval)

    summary = {
        "run_id": run_id,
        "raw_root": str(raw_root),
        "result_path": str(result_path),
        "error_path": str(error_path),
        "files": len(files),
        "counters": dict(counters),
        "url_action_counts": dict(action_counts),
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("summary_path=" + str(summary_path), flush=True)
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)


def process_one(path: Path, policy: dict[str, Any], text_limit_chars: int) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TypeError(f"payload type={type(payload).__name__}")
        result = process_benzinga_news_item(
            payload,
            policy=policy,
            raw_artifact_path=str(path),
            raw_payload_hash=stable_hash(json.dumps(payload, sort_keys=True, default=str)),
            options=ItemPipelineOptions(text_limit_chars=text_limit_chars),
        )
        normalized = result.normalized_row
        return {
            "status": "ok",
            "raw_path": str(path),
            "provider_article_id": result.provider_article_id,
            "canonical_news_id": result.canonical_news_id,
            "published_at_utc": normalized.get("published_at_utc") or "",
            "title": normalized.get("title") or "",
            "ticker_count": len(normalized.get("tickers") or []),
            "ticker_link_count": len(result.ticker_links),
            "url_candidate_count": len(result.url_resolution.url_candidates),
            "fetch_task_count": len(result.url_resolution.fetch_tasks),
            "url_action_counts": result.url_resolution.action_counts,
            "content_quality_flags": normalized.get("content_quality_flags") or [],
            "has_body": normalized.get("has_body") or 0,
            "has_external_text": normalized.get("has_external_text") or 0,
            "has_pdf": normalized.get("has_pdf") or 0,
            "is_title_only": normalized.get("is_title_only") or 0,
            "text_hash": normalized.get("text_hash") or "",
            "normalizer_version": normalized.get("normalizer_version") or "",
            "url_policy_version": result.policy_version,
            "warnings": result.warnings,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "raw_path": str(path), "exception": repr(exc)}


def discover_raw_files(raw_root: Path, limit_files: int) -> list[Path]:
    files: list[Path] = []
    limit = max(0, limit_files)
    for path in raw_root.rglob("*.json"):
        files.append(path)
        if limit and len(files) >= limit:
            break
    return files


def handle_result(row: dict[str, Any], results: Any, errors: Any, counters: Counter[str], action_counts: Counter[str]) -> int:
    if row.get("status") == "ok":
        results.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
        counters["ok"] += 1
        counters["ticker_links"] += int(row.get("ticker_link_count") or 0)
        counters["url_candidates"] += int(row.get("url_candidate_count") or 0)
        counters["fetch_tasks"] += int(row.get("fetch_task_count") or 0)
        for key, value in (row.get("url_action_counts") or {}).items():
            action_counts[str(key)] += int(value or 0)
        for flag in row.get("content_quality_flags") or []:
            counters[f"quality:{flag}"] += 1
    else:
        errors.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
        counters["failed"] += 1
    return 1


def print_progress(processed: int, total: int, started: float, interval: int) -> None:
    if processed == total or (interval > 0 and processed % interval == 0):
        elapsed = time.perf_counter() - started
        rate = processed / elapsed if elapsed else 0.0
        print(f"processed={processed:,}/{total:,} rate={rate:.1f}/s elapsed={elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
