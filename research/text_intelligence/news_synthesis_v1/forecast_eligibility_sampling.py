from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string

from .engine import ENGINE_VERSION, NewsSynthesisEngine, _sentence_spans
from .storage import load_identity_index


SAMPLING_VERSION = "news_synthesis_forecast_eligibility_sampling_v1"
SESSION_BUCKETS = ("premarket", "regular", "after_hours")
REVIEW_BATCH_SIZE = 80


def build_sampling_run(
    output_root: Path,
    *,
    client: ClickHouseHttpClient,
    database: str,
    start: str,
    end_exclusive: str,
    sample_size: int,
    seed: str,
    excluded_source_ids: Iterable[str],
) -> dict[str, Any]:
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    start_value = datetime.fromisoformat(start)
    end_value = datetime.fromisoformat(end_exclusive)
    start_year = start_value.year
    last_year = end_value.year - (end_value.month == 1 and end_value.day == 1)
    years = tuple(range(start_year, last_year + 1))
    if not years:
        raise ValueError("sampling range contains no years")

    output_root.mkdir(parents=True, exist_ok=False)
    exclusions = {str(value) for value in excluded_source_ids if str(value)}
    strata = [(year, session) for year in years for session in SESSION_BUCKETS]
    quotas = _balanced_quotas(sample_size, strata)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    candidate_counts: dict[str, int] = {}

    for year, session in strata:
        quota = quotas[(year, session)]
        rows = _query_stratum(
            client,
            database=database,
            year=year,
            start=start,
            end_exclusive=end_exclusive,
            session=session,
            seed=seed,
            limit=max(1000, quota * 4),
        )
        key = f"{year}:{session}"
        candidate_counts[key] = len(rows)
        accepted = []
        for row in rows:
            source_id = str(row.get("source_id") or "")
            if not source_id or source_id in exclusions or source_id in selected_ids:
                continue
            if not str(row.get("title") or "").strip():
                continue
            row = dict(row)
            row["year"] = year
            row["session_bucket"] = session
            row["first_substantive_sentence"] = first_substantive_sentence(
                str(row.get("text") or ""), str(row.get("title") or "")
            )
            accepted.append(row)
            selected_ids.add(source_id)
            if len(accepted) == quota:
                break
        if len(accepted) != quota:
            raise RuntimeError(
                f"insufficient eligible candidates for {key}: needed={quota} accepted={len(accepted)} "
                f"queried={len(rows)} exclusions={len(exclusions)}"
            )
        selected.extend(accepted)

    selected.sort(key=lambda row: (int(row["year"]), str(row["session_bucket"]), str(row["source_id"])))
    engine = NewsSynthesisEngine(load_identity_index(client, database))
    predictions: list[dict[str, Any]] = []
    engine_failures: list[dict[str, str]] = []
    for row in selected:
        try:
            document = engine.synthesize(row)
            eligible_rows = [
                item for item in document.get("eligibility", [])
                if item.get("product") == "forecast_trigger" and item.get("eligible") is True
            ]
            predictions.append({
                "source_id": row["source_id"],
                "forecast_eligible_predicted": bool(eligible_rows),
                "eligible_entity_ids": sorted(str(item.get("entity_id") or "") for item in eligible_rows),
                "engine_version": ENGINE_VERSION,
            })
        except Exception as exc:  # preserve every sampled source and expose failures
            predictions.append({
                "source_id": row["source_id"],
                "forecast_eligible_predicted": None,
                "eligible_entity_ids": [],
                "engine_version": ENGINE_VERSION,
            })
            engine_failures.append({"source_id": str(row["source_id"]), "error": f"{type(exc).__name__}: {exc}"})

    review_rows = [_review_row(row, seed=seed) for row in selected]
    review_rows.sort(key=lambda row: hashlib.sha256(f"{seed}|order|{row['review_id']}".encode()).hexdigest())
    batches = [review_rows[index:index + REVIEW_BATCH_SIZE] for index in range(0, len(review_rows), REVIEW_BATCH_SIZE)]

    _write_jsonl(output_root / "sampled_articles.jsonl", selected)
    _write_jsonl(output_root / "engine_predictions.jsonl", predictions)
    _write_jsonl(output_root / "review_answer_key.jsonl", (
        {"review_id": _review_id(str(source["source_id"]), seed), "source_id": source["source_id"]}
        for source in sorted(selected, key=lambda row: _review_id(str(row["source_id"]), seed))
    ))
    batch_root = output_root / "blind_review_batches"
    batch_root.mkdir()
    for index, batch in enumerate(batches, 1):
        _write_jsonl(batch_root / f"batch_{index:03d}.jsonl", batch)
    (output_root / "REVIEW_INSTRUCTIONS.md").write_text(_review_instructions(), encoding="utf-8")

    population_hash = _sha256_json(sorted(selected_ids))
    article_hash = _sha256_json([
        {
            "source_id": row["source_id"],
            "source_timestamp": row["source_timestamp"],
            "title": row["title"],
            "tickers": row.get("tickers") or [],
            "first_substantive_sentence": row["first_substantive_sentence"],
        }
        for row in selected
    ])
    manifest = {
        "version": SAMPLING_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "awaiting_blind_reviews",
        "sampling": {
            "database": database,
            "start": start,
            "end_exclusive": end_exclusive,
            "timezone": "America/New_York",
            "weekday_only": True,
            "session_definitions": {
                "premarket": "04:00 <= local time < 09:30",
                "regular": "09:30 <= local time < 16:00",
                "after_hours": "16:00 <= local time < 20:00",
            },
            "sample_size": len(selected),
            "seed": seed,
            "excluded_source_ids": len(exclusions),
            "quotas": {f"{year}:{session}": quota for (year, session), quota in quotas.items()},
            "candidate_rows_queried": candidate_counts,
        },
        "population": {
            "articles": len(selected),
            "by_year": dict(sorted(Counter(str(row["year"]) for row in selected).items())),
            "by_session": dict(sorted(Counter(str(row["session_bucket"]) for row in selected).items())),
            "population_ids_sha256": population_hash,
            "article_content_sha256": article_hash,
        },
        "prediction": {
            "engine_version": ENGINE_VERSION,
            "forecast_eligible": sum(item["forecast_eligible_predicted"] is True for item in predictions),
            "forecast_ineligible": sum(item["forecast_eligible_predicted"] is False for item in predictions),
            "engine_failures": len(engine_failures),
        },
        "review": {
            "unit": "article_any_listed_issuer",
            "sentiment_requested": False,
            "batch_size": REVIEW_BATCH_SIZE,
            "batch_count": len(batches),
            "reviewers_see_engine_prediction": False,
            "reviewers_see_existing_labels": False,
            "first_pass": "one independent reviewer per article",
            "second_pass": "engine disagreement, abstention, and deterministic QA sample",
            "third_pass": "unresolved human disagreement only",
        },
        "engine_failures": engine_failures,
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def first_substantive_sentence(text: str, title: str) -> str:
    normalized_title = " ".join(title.split()).strip(" .!?;:").casefold()
    for _start, _end, sentence in _sentence_spans(text):
        candidate = " ".join(sentence.split()).strip()
        if not candidate or candidate.strip(" .!?;:").casefold() == normalized_title:
            continue
        if len(candidate) < 24:
            continue
        return candidate
    return ""


def _balanced_quotas(sample_size: int, strata: list[tuple[int, str]]) -> dict[tuple[int, str], int]:
    base, remainder = divmod(sample_size, len(strata))
    return {stratum: base + (index < remainder) for index, stratum in enumerate(strata)}


def _query_stratum(
    client: ClickHouseHttpClient,
    *,
    database: str,
    year: int,
    start: str,
    end_exclusive: str,
    session: str,
    seed: str,
    limit: int,
) -> list[dict[str, Any]]:
    session_sql = {
        "premarket": "local_minute >= 240 AND local_minute < 570",
        "regular": "local_minute >= 570 AND local_minute < 960",
        "after_hours": "local_minute >= 960 AND local_minute < 1200",
    }[session]
    query = f"""
WITH
  toTimeZone(e.published_at_utc, 'America/New_York') AS local_ts,
  toHour(local_ts) * 60 + toMinute(local_ts) AS local_minute
SELECT
  e.canonical_news_id AS source_id,
  toString(e.published_at_utc) AS source_timestamp,
  e.title,
  e.author,
  e.article_url,
  e.url_domain,
  if(empty(r.rendered_text), e.title, r.rendered_text) AS text,
  e.tickers,
  e.channels,
  e.provider_tags,
  e.content_quality_flags,
  r.quality_flags,
  e.source_revision_key,
  multiIf(empty(r.canonical_news_id), 'unrendered', r.source_count=0, 'title_only', 'rendered') AS render_status,
  if(empty(r.rendered_text_hash), hex(SHA256(e.title)), r.rendered_text_hash) AS rendered_text_hash
FROM `{database}`.`benzinga_news_event_v2` e FINAL
LEFT JOIN `{database}`.`benzinga_news_rendered_v2` r FINAL
  ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.published_at_utc >= toDateTime64({sql_string(start)}, 9, 'UTC')
  AND e.published_at_utc < toDateTime64({sql_string(end_exclusive)}, 9, 'UTC')
  AND toYear(local_ts) = {year}
  AND toDayOfWeek(local_ts) BETWEEN 1 AND 5
  AND {session_sql}
  AND length(e.tickers) > 0
ORDER BY cityHash64(concat(e.canonical_news_id, {sql_string(seed)}))
LIMIT {int(limit)}
FORMAT JSONEachRow
"""
    return list(client.iter_json_each_row(query))


def _review_row(row: Mapping[str, Any], *, seed: str) -> dict[str, Any]:
    return {
        "review_id": _review_id(str(row["source_id"]), seed),
        "publication_time_utc": row["source_timestamp"],
        "session_bucket_et": row["session_bucket"],
        "tickers": row.get("tickers") or [],
        "author": row.get("author") or "",
        "provider_domain": row.get("url_domain") or "",
        "channels": row.get("channels") or [],
        "provider_tags": row.get("provider_tags") or [],
        "title": row.get("title") or "",
        "first_substantive_sentence": row.get("first_substantive_sentence") or "",
    }


def _review_id(source_id: str, seed: str) -> str:
    return "R" + hashlib.sha256(f"{seed}|review|{source_id}".encode()).hexdigest()[:20]


def _review_instructions() -> str:
    return """# Blind forecast-eligibility screening

For each row, decide whether the title, metadata, and first substantive sentence show that at least one listed ticker has forecast-eligible news.

Eligible requires a resolved listed issuer, substantive supported evidence, a current event or issuer forward guidance, a positive or negative economic implication, report/news purpose, and non-analyst origin. Company announcements reported through a news service may qualify. Neutral routine communications, historical-only material, analyst opinions, unresolved identity, or evidence without a directional economic implication do not qualify.

Do not infer missing facts. Use `insufficient_context` when the compact row cannot support either decision. Do not label sentiment. Return exactly one JSON object per input row with: `review_id`, `eligibility` (`eligible`, `ineligible`, or `insufficient_context`), `eligible_tickers` (possibly empty), `confidence` (`high`, `medium`, or `low`), and a concise `reason`.
"""


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
