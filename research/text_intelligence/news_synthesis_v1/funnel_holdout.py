from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research.mlops.clickhouse import ClickHouseHttpClient

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path


HOLDOUT_VERSION = "news_synthesis_funnel_fresh_holdout_v1"
HOLDOUT_SEED = "news-synthesis-funnel-fresh-holdout-20260821-v1"
START_EXCLUSIVE = "2026-08-13 21:04:05.000000000"
END_EXCLUSIVE = "2026-08-22 00:00:00.000000000"
DEFAULT_SAMPLE_SIZE = 1_000
DEFAULT_CORRECTED_LABELS = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_provider_filter_v1"
    r"\article_forecast_eligibility_labels.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\funnel_fresh_holdout_v1"
)


SOURCE_QUERY = f"""
SELECT
 e.canonical_news_id AS source_id,
 e.provider AS provider,
 e.provider_article_id AS provider_article_id,
 toString(e.published_at_utc) AS published_at_utc,
 toString(e.published_date) AS published_date,
 e.title AS title,
 e.author AS author,
 e.article_url AS article_url,
 e.url_domain AS url_domain,
 e.tickers AS tickers,
 e.channels AS channels,
 e.provider_tags AS provider_tags,
 e.content_quality_flags AS content_quality_flags,
 e.raw_artifact_path AS raw_artifact_path,
 e.raw_payload_hash AS raw_payload_hash,
 e.source_revision_key AS source_revision_key,
 e.renderer_version AS event_renderer_version,
 r.renderer_version AS renderer_version,
 r.text_contract AS text_contract,
 r.rendered_text AS rendered_text,
 r.rendered_text_hash AS rendered_text_hash,
 r.source_count AS source_count,
 r.block_count AS block_count,
 r.quality_flags AS renderer_quality_flags
FROM q_live.benzinga_news_event_v2 AS e FINAL
LEFT JOIN q_live.benzinga_news_rendered_v2 AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
PREWHERE e.published_at_utc > toDateTime64('{START_EXCLUSIVE}', 9, 'UTC')
 AND e.published_at_utc < toDateTime64('{END_EXCLUSIVE}', 9, 'UTC')
ORDER BY e.published_at_utc, e.canonical_news_id
FORMAT JSONEachRow
"""


def selection_key(source_id: str, *, seed: str = HOLDOUT_SEED) -> str:
    return hashlib.sha256(f"{seed}\0{source_id}".encode("utf-8")).hexdigest()


def select_uniform_holdout(
    rows: Sequence[Mapping[str, Any]],
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: str = HOLDOUT_SEED,
) -> list[dict[str, Any]]:
    if sample_size <= 0 or sample_size > len(rows):
        raise ValueError("sample_size must be positive and no larger than the population")
    ids = [str(row.get("source_id") or "") for row in rows]
    if "" in ids or len(ids) != len(set(ids)):
        raise ValueError("source population has missing or duplicate source IDs")
    selected = sorted(rows, key=lambda row: (selection_key(str(row["source_id"]), seed=seed), str(row["source_id"])))[:sample_size]
    return [dict(row) for row in sorted(selected, key=lambda row: (str(row["published_at_utc"]), str(row["source_id"])))]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
            count += 1
    return count


def _validate_source_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
        source_id = str(row.get("source_id") or "")
        rendered = str(row.get("rendered_text") or "")
        rendered_hash = str(row.get("rendered_text_hash") or "")
        if not source_id or not str(row.get("published_at_utc") or ""):
            raise ValueError("source row lacks identity or timestamp")
        if not rendered or len(rendered_hash) != 64:
            raise ValueError(f"fresh holdout lacks rendered text authority: {source_id}")
        if hashlib.sha256(rendered.encode("utf-8")).hexdigest() != rendered_hash:
            raise ValueError(f"fresh holdout rendered hash mismatch: {source_id}")


def _authority_ids(path: Path) -> set[str]:
    return {str(row["source_id"]) for row in iter_jsonl(path)}


def freeze_fresh_holdout(
    client: ClickHouseHttpClient,
    *,
    corrected_labels: Path = DEFAULT_CORRECTED_LABELS,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> dict[str, Any]:
    rows = list(client.iter_json_each_row(SOURCE_QUERY))
    _validate_source_rows(rows)
    authority_ids = _authority_ids(corrected_labels)
    overlap = sorted(authority_ids & {str(row["source_id"]) for row in rows})
    if overlap:
        raise ValueError(f"fresh holdout overlaps corrected development authority: {len(overlap)}")
    selected = select_uniform_holdout(rows, sample_size=sample_size)

    output_root.mkdir(parents=True, exist_ok=False)
    population_path = output_root / "SOURCE_POPULATION.jsonl"
    sample_path = output_root / "SEALED_SAMPLE.jsonl"
    population_rows = _write_jsonl(population_path, rows)
    sample_rows = _write_jsonl(sample_path, selected)
    manifest = {
        "holdout_version": HOLDOUT_VERSION,
        "status": "sealed_unlabeled",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "selection": {
            "method": "uniform_without_replacement_by_sha256_rank",
            "seed": HOLDOUT_SEED,
            "sample_size": sample_size,
            "prediction_blind": True,
            "labels_absent_at_freeze": True,
            "development_authority_overlap": 0,
        },
        "source_window": {
            "start_exclusive": START_EXCLUSIVE,
            "end_exclusive": END_EXCLUSIVE,
            "query_sha256": hashlib.sha256(SOURCE_QUERY.encode("utf-8")).hexdigest(),
            "population_rows": population_rows,
            "sample_rows": sample_rows,
            "min_published_at_utc": min(str(row["published_at_utc"]) for row in rows),
            "max_published_at_utc": max(str(row["published_at_utc"]) for row in rows),
        },
        "inputs": {
            "corrected_labels": str(corrected_labels),
            "corrected_labels_sha256": sha256_path(corrected_labels),
        },
        "files": {
            population_path.name: {
                "rows": population_rows,
                "bytes": population_path.stat().st_size,
                "sha256": sha256_path(population_path),
            },
            sample_path.name: {
                "rows": sample_rows,
                "bytes": sample_path.stat().st_size,
                "sha256": sha256_path(sample_path),
            },
        },
        "sample_source_ids_sha256": hashlib.sha256(
            canonical_json([str(row["source_id"]) for row in selected]).encode("utf-8")
        ).hexdigest(),
        "release_policy": {
            "development_use": "forbidden",
            "prediction_before_gold": "forbidden",
            "gold_review": "prediction_blind_two_reader_with_adjudication",
            "evaluation": "one_final_release_after_rules_and_engine_are_frozen",
        },
    }
    manifest_path = output_root / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validation = {
        "status": "passed",
        "holdout_version": HOLDOUT_VERSION,
        "population_rows": population_rows,
        "sample_rows": sample_rows,
        "unique_population_ids": len({str(row["source_id"]) for row in rows}),
        "unique_sample_ids": len({str(row["source_id"]) for row in selected}),
        "development_authority_overlap": 0,
        "rendered_hashes_verified": True,
        "sample_is_population_subset": {str(row["source_id"]) for row in selected}.issubset(
            {str(row["source_id"]) for row in rows}
        ),
    }
    (output_root / "VALIDATION.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
