from __future__ import annotations

import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)

from .audit import write_audit
from .client import check_server, label_article
from .config import LabelingConfig
from .data import append_jsonl, fetch_stratified_sample, read_jsonl, write_jsonl
from .taxonomy import LABEL_VERSION, PROMPT_VERSION


def run(
    config: LabelingConfig,
    *,
    execute: bool,
    input_jsonl: Path | None = None,
) -> int:
    config.runtime_root.mkdir(parents=True, exist_ok=True)
    if config.sample_path.exists():
        sample = read_jsonl(config.sample_path)
        if len(sample) != config.sample_size and input_jsonl is None:
            raise RuntimeError(
                f"Existing sample has {len(sample)} rows, not requested {config.sample_size}; "
                "use a new runtime root rather than mutating an audit population."
            )
    elif input_jsonl:
        sample = read_jsonl(input_jsonl)
        if not sample:
            raise RuntimeError(f"Input JSONL is empty: {input_jsonl}")
        write_jsonl(config.sample_path, sample)
    else:
        client = ClickHouseHttpClient(
            default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
        )
        sample = fetch_stratified_sample(client, config)
        write_jsonl(config.sample_path, sample)

    print(
        f"GPT-OSS NEWS LABEL V1 | sample={len(sample):,} model={config.model} "
        f"workers={config.workers} execute={execute}",
        flush=True,
    )
    if not execute:
        print(f"PLANNED | sample={config.sample_path}", flush=True)
        return 0

    check_server(config)
    existing_rows = read_jsonl(config.results_path)
    completed: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        if row.get("status") == "completed":
            completed[str(row["canonical_news_id"])] = row
    for article in sample:
        prior = completed.get(str(article["canonical_news_id"]))
        if prior and (
            prior.get("text_sha256") != article.get("text_sha256")
            or prior.get("label_version") != LABEL_VERSION
            or prior.get("prompt_version") != PROMPT_VERSION
            or prior.get("model") != config.model
        ):
            raise RuntimeError(
                f"Completed-label contract drift for {article['canonical_news_id']}; "
                "use a new runtime root for a new prompt, model, or source representation."
            )

    pending = [row for row in sample if row["canonical_news_id"] not in completed]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, config.workers)) as pool:
        futures = {pool.submit(_label_one, article, config): article for article in pending}
        for index, future in enumerate(as_completed(futures), start=1):
            article = futures[future]
            try:
                result = future.result()
                completed[result["canonical_news_id"]] = result
                append_jsonl(config.results_path, result)
                status = "COMPLETED"
            except Exception as exc:
                failure = {
                    "label_version": LABEL_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "canonical_news_id": article["canonical_news_id"],
                    "text_sha256": article.get("text_sha256", ""),
                    "status": "failed",
                    "error": str(exc)[:2_000],
                    "updated_at_utc": _utc_now(),
                }
                append_jsonl(config.failures_path, failure)
                status = "FAILED"
            elapsed = time.monotonic() - started
            rate = index / elapsed if elapsed else 0.0
            eta = (len(pending) - index) / rate if rate else 0.0
            print(
                f"[{index}/{len(pending)}] {status} id={article['canonical_news_id']} "
                f"elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
                flush=True,
            )

    unresolved_failures: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(config.failures_path):
        identifier = str(row.get("canonical_news_id") or "")
        if identifier and identifier not in completed:
            unresolved_failures[identifier] = row
    final_rows = list(completed.values()) + list(unresolved_failures.values())
    report_path = write_audit(config.runtime_root, sample, final_rows)
    manifest = {
        "label_version": LABEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": config.model,
        "endpoint": config.endpoint,
        "renderer_version": config.renderer_version,
        "sample_rows": len(sample),
        "completed_rows": len(completed),
        "failed_rows": len(unresolved_failures),
        "sample_path": str(config.sample_path),
        "results_path": str(config.results_path),
        "audit_path": str(report_path),
        "generated_at_utc": _utc_now(),
    }
    (config.runtime_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(
        f"DONE | completed={len(completed):,}/{len(sample):,} audit={report_path}",
        flush=True,
    )
    return 0 if len(completed) == len(sample) else 2


def _label_one(article: dict[str, Any], config: LabelingConfig) -> dict[str, Any]:
    label, usage = label_article(article, config)
    return {
        "label_version": LABEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": config.model,
        "canonical_news_id": article["canonical_news_id"],
        "published_at_utc": article["published_at_utc"],
        "text_sha256": article["text_sha256"],
        "status": "completed",
        "label": label,
        "usage": usage,
        "updated_at_utc": _utc_now(),
    }


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
