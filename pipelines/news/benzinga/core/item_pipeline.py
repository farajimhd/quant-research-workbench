from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.news.benzinga.core.contracts import NewsPipelineResult, UrlResolution
from pipelines.news.benzinga.core.url_policy import policy_version
from pipelines.news.benzinga.news_benzinga_build_normalized_rows import apply_enrichments, now_clickhouse_dt64
from pipelines.news.benzinga.news_benzinga_normalize import (
    BENZINGA_PROVIDER,
    NewsExtractionOptions,
    normalize_benzinga_payload,
    stable_hash,
)
from pipelines.news.benzinga.news_benzinga_url_fetch_plan import (
    ACTIONABLE_ACTIONS,
    apply_domain_policy,
    compact_attachment_row,
    compact_candidate_row,
)
from pipelines.news.benzinga.news_benzinga_url_inventory import inventory_payload


@dataclass(frozen=True, slots=True)
class ItemPipelineOptions:
    text_limit_chars: int = 50_000
    max_enriched_text_chars_per_url: int = 24_000
    max_enriched_urls_per_article: int = 5
    include_enrichment_rows: bool = True


def process_benzinga_news_item(
    payload: dict[str, Any],
    *,
    policy: dict[str, Any],
    raw_artifact_path: str = "",
    raw_payload_hash: str = "",
    downloaded_at_utc: datetime | None = None,
    enrichment_rows: list[dict[str, Any]] | None = None,
    options: ItemPipelineOptions | None = None,
) -> NewsPipelineResult:
    opts = options or ItemPipelineOptions()
    raw_hash = raw_payload_hash or stable_hash(json.dumps(payload, sort_keys=True, default=str))
    row = normalize_benzinga_payload(
        payload,
        raw_artifact_path=raw_artifact_path,
        raw_payload_hash=raw_hash,
        downloaded_at_utc=downloaded_at_utc,
        options=NewsExtractionOptions(fetch_external=False, extract_pdfs=False, text_limit_chars=opts.text_limit_chars),
        diagnostics=[],
    )
    row["updated_at_utc"] = now_clickhouse_dt64()

    url_resolution = resolve_news_url_tasks(
        payload,
        policy=policy,
        raw_artifact_path=raw_artifact_path,
        raw_payload_hash=raw_hash,
    )

    warnings: list[str] = []
    if opts.include_enrichment_rows and enrichment_rows:
        args = enrichment_args(opts)
        by_hash = {str(item.get("url_hash") or ""): item for item in enrichment_rows if item.get("url_hash")}
        enrichments = [by_hash[item["url_hash"]] for item in url_resolution.attachments if item.get("url_hash") in by_hash]
        row, _summary = apply_enrichments(args, row, url_resolution.attachments, enrichments)
        row["updated_at_utc"] = now_clickhouse_dt64()
    elif url_resolution.fetch_tasks:
        warnings.append("enrichment_pending")

    ticker_links = build_ticker_links(row)
    return NewsPipelineResult(
        provider_article_id=str(row.get("provider_article_id") or ""),
        canonical_news_id=str(row.get("canonical_news_id") or ""),
        policy_version=policy_version(policy),
        normalized_row=row,
        ticker_links=ticker_links,
        url_resolution=url_resolution,
        warnings=warnings,
    )


def resolve_news_url_tasks(
    payload: dict[str, Any],
    *,
    policy: dict[str, Any],
    raw_artifact_path: str = "",
    raw_payload_hash: str = "",
) -> UrlResolution:
    raw_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    raw_path = Path(raw_artifact_path) if raw_artifact_path else Path("")
    rows = inventory_payload(payload, raw_path=raw_path, raw_text=raw_text)
    candidates: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    fetch_tasks: list[dict[str, Any]] = []
    action_counts: Counter[str] = Counter()
    seen_fetch_tasks: set[str] = set()
    for row in rows:
        if raw_payload_hash:
            row["raw_payload_hash"] = raw_payload_hash
        decision = apply_domain_policy(row, policy)
        final_action = decision["final_action"]
        action_counts[final_action] += 1
        candidate = compact_candidate_row(row, decision)
        candidate["url_policy_version"] = policy_version(policy)
        candidate["url_ordinal"] = int(row.get("url_ordinal") or 0)
        candidates.append(candidate)
        attachment = compact_attachment_row(row, decision)
        attachment["url_policy_version"] = policy_version(policy)
        attachments.append(attachment)
        if final_action in ACTIONABLE_ACTIONS:
            url_hash = str(candidate.get("url_hash") or "")
            if url_hash and url_hash not in seen_fetch_tasks:
                task = dict(candidate)
                task["fetch_url"] = task.get("normalized_url") or ""
                task["status"] = "pending"
                fetch_tasks.append(task)
                seen_fetch_tasks.add(url_hash)
    return UrlResolution(
        policy_version=policy_version(policy),
        url_candidates=candidates,
        fetch_tasks=fetch_tasks,
        attachments=attachments,
        action_counts=dict(action_counts),
    )


def build_ticker_links(row: dict[str, Any]) -> list[dict[str, Any]]:
    tickers = []
    seen: set[str] = set()
    for ticker in row.get("tickers") or []:
        text = str(ticker or "").strip().upper()
        if text and text not in seen:
            seen.add(text)
            tickers.append(text)
    count = len(tickers)
    return [
        {
            "canonical_news_id": row.get("canonical_news_id") or "",
            "provider": row.get("provider") or BENZINGA_PROVIDER,
            "provider_article_id": row.get("provider_article_id") or "",
            "published_date": row.get("published_date") or "",
            "published_at_utc": row.get("published_at_utc") or "",
            "ticker": ticker,
            "ticker_index": index,
            "ticker_count": count,
            "text_hash": row.get("text_hash") or "",
            "content_quality_flags": row.get("content_quality_flags") or [],
            "normalizer_version": row.get("normalizer_version") or "",
            "updated_at_utc": row.get("updated_at_utc") or now_clickhouse_dt64(),
        }
        for index, ticker in enumerate(tickers, start=1)
    ]


def enrichment_args(options: ItemPipelineOptions) -> Any:
    class Args:
        text_limit_chars = options.text_limit_chars
        max_enriched_text_chars_per_url = options.max_enriched_text_chars_per_url
        max_enriched_urls_per_article = options.max_enriched_urls_per_article

    return Args()
