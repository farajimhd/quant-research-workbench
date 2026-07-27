from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from pipelines.news.benzinga.core.clickhouse_writer_v2 import NewsV2TargetConfig, assert_v2_ready
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string
from src.backend.news_classification import classify_news

from .config import LabelingConfig
from .prompt import build_messages


def fetch_stratified_sample(
    client: ClickHouseHttpClient,
    config: LabelingConfig,
) -> list[dict[str, Any]]:
    assert_v2_ready(
        client,
        NewsV2TargetConfig(
            database=config.database,
            event_table=config.event_table,
            rendered_table=config.rendered_table,
            authority_table=config.authority_table,
        ),
    )
    db, event, rendered = map(
        quote_ident,
        (config.database, config.event_table, config.rendered_table),
    )
    sql = f"""
SELECT
 e.canonical_news_id,
 toString(e.published_at_utc) AS published_at_utc,
 e.title,
 e.author,
 e.url_domain,
 e.tickers,
 e.channels,
 e.provider_tags,
 e.links,
 arrayDistinct(arrayConcat(e.content_quality_flags, r.quality_flags)) AS quality_flags,
 substring(r.rendered_text, 1, {int(config.max_input_chars)}) AS rendered_text,
 r.rendered_text_hash
FROM {db}.{event} AS e FINAL
INNER JOIN {db}.{rendered} AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.renderer_version={sql_string(config.renderer_version)}
 AND r.renderer_version={sql_string(config.renderer_version)}
 AND e.published_at_utc >= toDateTime64({sql_string(config.start_date)}, 9, 'UTC')
 AND e.published_at_utc < toDateTime64({sql_string(config.end_date_exclusive)}, 9, 'UTC')
ORDER BY cityHash64(concat(e.canonical_news_id, {sql_string(config.renderer_version)}))
LIMIT {int(config.candidate_size)}
FORMAT JSONEachRow
"""
    candidates = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    return fit_sample_to_context(stratify(candidates, config.sample_size), config)


def stratify(candidates: Iterable[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
    for row in candidates:
        rendered = str(row.get("rendered_text") or "")
        classification = classify_news(
            {
                **row,
                "text": rendered,
                "normalized_full_text": rendered,
                "links": row.get("links") or [],
            },
            len(row.get("tickers") or []),
        )
        row["deterministic"] = classification.as_dict()
        row["text_sha256"] = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        length_bucket = "short" if len(rendered) < 800 else "medium" if len(rendered) < 4_000 else "long"
        quality_bucket = "flagged" if row.get("quality_flags") else "clean"
        key = (classification.kind, classification.scope, length_bucket, quality_bucket)
        buckets[key].append(row)
    selected: list[dict[str, Any]] = []
    keys = deque(sorted(buckets))
    while keys and len(selected) < sample_size:
        key = keys.popleft()
        bucket = buckets[key]
        if bucket:
            selected.append(bucket.popleft())
        if bucket:
            keys.append(key)
    if len(selected) < sample_size:
        raise RuntimeError(
            f"Only {len(selected):,} candidates were available for a requested sample of {sample_size:,}."
        )
    return selected


def fit_sample_to_context(
    sample: list[dict[str, Any]],
    config: LabelingConfig,
) -> list[dict[str, Any]]:
    """Fit each complete Harmony request to the exact served-model tokenizer."""
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "transformers is required for exact gpt-oss context budgeting; "
            "the workflow will not use character-count guessing."
        ) from exc
    if not config.tokenizer_path.exists():
        raise RuntimeError(f"Local gpt-oss tokenizer path does not exist: {config.tokenizer_path}")
    tokenizer = AutoTokenizer.from_pretrained(config.tokenizer_path, local_files_only=True)
    prompt_limit = config.max_model_len - config.max_output_tokens
    if prompt_limit <= 0:
        raise RuntimeError("max_output_tokens must be smaller than max_model_len")
    fitted: list[dict[str, Any]] = []
    for original in sample:
        row = dict(original)
        body = str(row.get("rendered_text") or "")
        token_count = _message_token_count(tokenizer, row)
        truncated = False
        if token_count > prompt_limit:
            low, high = 0, len(body)
            while low < high:
                midpoint = (low + high + 1) // 2
                row["rendered_text"] = body[:midpoint]
                if _message_token_count(tokenizer, row) <= prompt_limit:
                    low = midpoint
                else:
                    high = midpoint - 1
            row["rendered_text"] = body[:low]
            token_count = _message_token_count(tokenizer, row)
            truncated = True
        if token_count > prompt_limit:
            raise RuntimeError(
                f"Invariant prompt exceeds context budget for {row['canonical_news_id']}: "
                f"{token_count:,}>{prompt_limit:,}"
            )
        row["prompt_tokens"] = token_count
        row["truncated_for_context"] = truncated
        row["text_sha256"] = hashlib.sha256(
            str(row["rendered_text"]).encode("utf-8")
        ).hexdigest()
        fitted.append(row)
    return fitted


def _message_token_count(tokenizer: Any, article: dict[str, Any]) -> int:
    tokens = tokenizer.apply_chat_template(
        build_messages(article),
        tokenize=True,
        add_generation_prompt=True,
    )
    return len(tokens)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
    temporary.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
