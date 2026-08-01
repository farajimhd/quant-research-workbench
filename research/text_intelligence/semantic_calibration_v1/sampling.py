from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    load_news_issuer_resolver,
)

from .annotation_template import annotation_template
from .schema import SAMPLE_VERSION, stable_json_hash
from .storage import assert_runtime_root, write_json_atomic


LABELING_VERSION = "scoped_text_labeling_v5"
SAMPLING_SEED = "news-semantic-calibration-v1-20260731"
DEFAULT_SAMPLE_SIZE = 1_000
DEFAULT_PILOT_SIZE = 100
DEFAULT_RARE_SUPPLEMENT = 150


@dataclass(frozen=True, slots=True)
class SampleBuildResult:
    root: Path
    sample_count: int
    pilot_count: int
    manifest_hash: str


def build_sample(
    client: ClickHouseHttpClient,
    root: Path,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    pilot_size: int = DEFAULT_PILOT_SIZE,
    rare_supplement: int = DEFAULT_RARE_SUPPLEMENT,
    report: Callable[[str], None] | None = None,
) -> SampleBuildResult:
    """Build one immutable blinded News calibration collection.

    V5 is used only to diversify selection and is persisted in the sealed
    comparison file. It is never copied into reviewer-visible articles.
    """
    emit = report or (lambda _message: None)
    assert_runtime_root(root)
    manifest_path = root / "sample_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return SampleBuildResult(
            root=root,
            sample_count=int(manifest["sample_count"]),
            pilot_count=int(manifest["pilot_count"]),
            manifest_hash=str(manifest["sample_manifest_sha256"]),
        )
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    if not 0 <= pilot_size <= sample_size:
        raise ValueError("pilot_size must be between zero and sample_size")
    if not 0 <= rare_supplement <= sample_size:
        raise ValueError("rare_supplement must be between zero and sample_size")

    emit("CANDIDATES | reading stratified V5 selection pool")
    label_rows = fetch_label_candidates(client)
    emit(f"CANDIDATES | V5 rows={len(label_rows):,}")
    baseline_rows = fetch_baseline_candidates(client)
    emit(f"CANDIDATES | source-independent rows={len(baseline_rows):,}")
    candidates = merge_candidates(label_rows, baseline_rows)
    emit(f"METADATA | hydrating articles={len(candidates):,} by source month")
    hydrate_event_metadata(client, candidates, report=emit)
    hydrate_complete_v5_units(client, candidates, report=emit)
    candidates = {
        key: value
        for key, value in candidates.items()
        if value.get("event") and value.get("source_timestamp")
    }
    selected = select_candidates(
        candidates.values(),
        sample_size=sample_size,
        rare_supplement=rare_supplement,
    )
    if len(selected) != sample_size:
        raise RuntimeError(
            f"sampling produced {len(selected):,} rows; expected {sample_size:,}"
        )
    emit(f"SELECTED | articles={len(selected):,}; loading source and rendered text")
    hydrate_text_products(client, selected, report=emit)
    emit("IDENTITY | loading point-in-time issuer authority")
    resolver = load_news_issuer_resolver(client)

    # Reviewer order is independent of selection order and sealed split.
    ordered = sorted(
        selected,
        key=lambda row: stable_json_hash(
            [SAMPLING_SEED, "review-order", row["source_id"]]
        ),
    )
    manifest_items: list[dict[str, Any]] = []
    sealed_items: list[dict[str, Any]] = []
    for index, row in enumerate(ordered, 1):
        sample_id = f"N{index:04d}"
        blinded = build_blinded_item(
            sample_id,
            row,
            resolver=resolver,
            pilot=index <= pilot_size,
        )
        blinded_hash = stable_json_hash(blinded)
        blinded["blinded_item_sha256"] = blinded_hash
        write_json_atomic(root / "blinded_articles" / f"{sample_id}.json", blinded)
        write_json_atomic(
            root / "annotation_templates" / f"{sample_id}.json",
            annotation_template(blinded),
        )
        split = locked_split(row)
        manifest_items.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "source_timestamp": row["source_timestamp"],
                "source_text_sha256": blinded["source_text_sha256"],
                "blinded_item_sha256": blinded_hash,
                "pilot": index <= pilot_size,
            }
        )
        sealed_items.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "source_timestamp": row["source_timestamp"],
                "source_text_sha256": blinded["source_text_sha256"],
                "locked_split": split,
                "selection_stratum": selection_stratum(row),
                "v5": sealed_v5_summary(row),
            }
        )
        if index % 100 == 0 or index == sample_size:
            emit(f"WRITE | durable blinded items={index:,}/{sample_size:,}")

    sealed = {
        "sample_version": SAMPLE_VERSION,
        "sampling_seed": SAMPLING_SEED,
        "warning": "Do not open during blinded annotation.",
        "items": sealed_items,
    }
    sealed["sealed_comparison_sha256"] = stable_json_hash(sealed)
    write_json_atomic(root / "sealed" / "v5_comparison_and_splits.json", sealed)

    manifest = {
        "sample_version": SAMPLE_VERSION,
        "sampling_seed": SAMPLING_SEED,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sample_count": sample_size,
        "pilot_count": pilot_size,
        "rare_supplement_target": rare_supplement,
        "blinding": {
            "visible": [
                "source and rendered text",
                "publication time",
                "provider metadata and ticker links",
                "point-in-time issuer identity matches",
            ],
            "sealed": [
                "V5 labels, concepts, scores and eligibility",
                "train/calibration/test assignment",
                "subsequent price reaction",
            ],
        },
        "items": manifest_items,
    }
    manifest["sample_manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(manifest_path, manifest)
    write_json_atomic(
        root / "annotation_state.json",
        {
            "sample_version": SAMPLE_VERSION,
            "sample_manifest_sha256": manifest["sample_manifest_sha256"],
            "expected": sample_size,
            "completed": 0,
            "remaining": sample_size,
            "unexpected": [],
        },
    )
    emit("READY | immutable manifest and sealed comparison persisted")
    return SampleBuildResult(
        root=root,
        sample_count=sample_size,
        pilot_count=pilot_size,
        manifest_hash=str(manifest["sample_manifest_sha256"]),
    )


def fetch_label_candidates(client: ClickHouseHttpClient) -> list[dict[str, Any]]:
    # Each issuer-scoped label is a candidate. Python then aggregates all units
    # for an article so no one issuer's V5 output becomes the article authority.
    sql = f"""
SELECT
 source_id,
 toString(source_timestamp) AS source_timestamp,
 ticker,
 content_role,
 source_origin,
 semantic_direction,
 semantic_score,
 event_concepts,
 forecast_trigger_eligible,
 reaction_evaluation_eligible,
 issuer_history_context_eligible
FROM q_live.scoped_text_labels_v5 AS label FINAL
WHERE corpus='news' AND labeling_version={sql_string(LABELING_VERSION)}
ORDER BY cityHash64(concat(source_id, ticker, {sql_string(SAMPLING_SEED)}))
LIMIT 28 BY
 multiIf(toYear(label.source_timestamp)<2015, '2010_2014',
         toYear(label.source_timestamp)<2020, '2015_2019',
         toYear(label.source_timestamp)<2024, '2020_2023', '2024_2026'),
 content_role,
 semantic_direction
FORMAT JSONEachRow
"""
    return json_rows(client.execute(sql))


def fetch_baseline_candidates(client: ClickHouseHttpClient) -> list[dict[str, Any]]:
    # This pool is independent of V5 and guarantees that missing-label articles
    # and provider-link edge cases can enter the sample.
    sql = f"""
SELECT
 canonical_news_id AS source_id,
 toString(published_at_utc) AS source_timestamp,
 source_revision_key,
 provider_article_id,
 provider,
 title,
 teaser,
 author,
 article_url,
 url_domain,
 tickers,
 channels,
 provider_tags,
 content_quality_flags,
 raw_artifact_path,
 raw_payload_hash
FROM q_live.benzinga_news_event_v2 FINAL
WHERE published_at_utc >= toDateTime64('2010-01-01 00:00:00', 9, 'UTC')
ORDER BY cityHash64(concat(canonical_news_id, {sql_string(SAMPLING_SEED)}))
LIMIT 48 BY
 multiIf(toYear(published_at_utc)<2015, '2010_2014',
         toYear(published_at_utc)<2020, '2015_2019',
         toYear(published_at_utc)<2024, '2020_2023', '2024_2026'),
 multiIf(length(tickers)=0, 'zero', length(tickers)=1, 'single', 'multi')
FORMAT JSONEachRow
"""
    return json_rows(client.execute(sql))


def merge_candidates(
    labels: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for raw in baseline:
        source_id = str(raw["source_id"])
        candidate = candidates.setdefault(source_id, new_candidate(source_id))
        candidate["source_timestamp"] = str(raw.get("source_timestamp") or "")
        candidate["event"] = dict(raw)
        candidate["baseline_pool"] = True
    for raw in labels:
        source_id = str(raw["source_id"])
        candidate = candidates.setdefault(source_id, new_candidate(source_id))
        candidate["source_timestamp"] = str(raw.get("source_timestamp") or "")
        candidate["v5_units"].append(dict(raw))
    return candidates


def new_candidate(source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_timestamp": "",
        "event": None,
        "v5_units": [],
        "baseline_pool": False,
        "source_lanes": [],
        "rendered": None,
    }


def hydrate_event_metadata(
    client: ClickHouseHttpClient,
    candidates: Mapping[str, dict[str, Any]],
    *,
    report: Callable[[str], None] | None = None,
) -> None:
    emit = report or (lambda _message: None)
    missing = [value for value in candidates.values() if not value.get("event")]
    grouped = group_by_month(missing)
    completed = 0
    for month, month_rows in grouped.items():
        for chunk in chunks(month_rows, 500):
            pairs = date_id_pairs(chunk)
            sql = f"""
SELECT
 canonical_news_id AS source_id,
 toString(published_at_utc) AS source_timestamp,
 source_revision_key,
 provider_article_id,
 provider,
 title,
 teaser,
 author,
 article_url,
 url_domain,
 tickers,
 channels,
 provider_tags,
 content_quality_flags,
 raw_artifact_path,
 raw_payload_hash
FROM q_live.benzinga_news_event_v2 FINAL
WHERE (published_date, canonical_news_id) IN ({pairs})
FORMAT JSONEachRow
"""
            for row in json_rows(client.execute(sql)):
                candidate = candidates.get(str(row["source_id"]))
                if candidate is not None:
                    candidate["event"] = row
                    candidate["source_timestamp"] = str(row["source_timestamp"])
        completed += len(month_rows)
        if completed == len(missing) or len(grouped) <= 12 or completed % 500 < len(month_rows):
            emit(f"METADATA | event rows={completed:,}/{len(missing):,} through {month}")


def hydrate_complete_v5_units(
    client: ClickHouseHttpClient,
    candidates: Mapping[str, dict[str, Any]],
    *,
    report: Callable[[str], None] | None = None,
) -> None:
    """Replace pool fragments with every current issuer-scoped V5 unit.

    The candidate query is intentionally quota-limited and therefore cannot be
    treated as an article summary. This second bounded lookup prevents a
    multi-issuer publication from being misrepresented in sealed comparisons.
    """
    emit = report or (lambda _message: None)
    for candidate in candidates.values():
        candidate["v5_units"] = []
    grouped = group_by_month(list(candidates.values()))
    completed = 0
    for month, month_rows in grouped.items():
        for chunk in chunks(month_rows, 400):
            pairs = date_id_pairs(chunk)
            sql = f"""
SELECT
 source_id,
 toString(source_timestamp) AS source_timestamp,
 ticker,
 content_role,
 source_origin,
 semantic_direction,
 semantic_score,
 event_concepts,
 forecast_trigger_eligible,
 reaction_evaluation_eligible,
 issuer_history_context_eligible
FROM q_live.scoped_text_labels_v5 FINAL
WHERE corpus='news'
  AND labeling_version={sql_string(LABELING_VERSION)}
  AND (toDate(source_timestamp), source_id) IN ({pairs})
FORMAT JSONEachRow
"""
            for row in json_rows(client.execute(sql)):
                candidate = candidates.get(str(row["source_id"]))
                if candidate is not None:
                    candidate["v5_units"].append(row)
        completed += len(month_rows)
        if completed == len(candidates) or len(grouped) <= 12 or completed % 500 < len(month_rows):
            emit(f"METADATA | V5 articles={completed:,}/{len(candidates):,} through {month}")


def select_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    sample_size: int,
    rare_supplement: int,
) -> list[dict[str, Any]]:
    rows = list(candidates)
    if len(rows) < sample_size:
        raise RuntimeError(f"only {len(rows):,} candidates available")
    concept_frequency = Counter(
        concept
        for row in rows
        for concept in article_concepts(row)
    )
    rows_by_era: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_era[era(row["source_timestamp"])].append(row)
    eras = ("2010_2014", "2015_2019", "2020_2023", "2024_2026")
    sample_quotas = distribute_quota(sample_size, eras)
    rare_quotas = distribute_quota(rare_supplement, eras)
    selected: list[dict[str, Any]] = []
    for era_name in eras:
        era_rows = rows_by_era.get(era_name, [])
        target = sample_quotas[era_name]
        if len(era_rows) < target:
            raise RuntimeError(
                f"era {era_name} has {len(era_rows):,} candidates; needs {target:,}"
            )
        rare_ranked = sorted(
            (row for row in era_rows if article_concepts(row)),
            key=lambda row: (
                min(concept_frequency[value] for value in article_concepts(row)),
                stable_json_hash([SAMPLING_SEED, "rare", row["source_id"]]),
            ),
        )
        rare_selected = rare_ranked[: rare_quotas[era_name]]
        selected_ids = {row["source_id"] for row in rare_selected}
        balanced = round_robin_strata(
            [row for row in era_rows if row["source_id"] not in selected_ids],
            target=target - len(rare_selected),
        )
        selected.extend((*rare_selected, *balanced))
    return selected


def round_robin_strata(
    rows: Sequence[dict[str, Any]],
    *,
    target: int,
) -> list[dict[str, Any]]:
    buckets: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        buckets[selection_stratum(row)].append(row)
    for key, values in list(buckets.items()):
        buckets[key] = deque(sorted(
            values,
            key=lambda row: stable_json_hash(
                [SAMPLING_SEED, "within-stratum", row["source_id"]]
            ),
        ))
    selected: list[dict[str, Any]] = []
    keys = sorted(buckets)
    while len(selected) < target:
        progressed = False
        for key in keys:
            if buckets[key]:
                selected.append(buckets[key].popleft())
                progressed = True
                if len(selected) == target:
                    break
        if not progressed:
            break
    if len(selected) != target:
        raise RuntimeError(f"stratified selection produced {len(selected)} of {target}")
    return selected


def distribute_quota(total: int, keys: Sequence[str]) -> dict[str, int]:
    base, remainder = divmod(total, len(keys))
    return {
        key: base + (1 if index < remainder else 0)
        for index, key in enumerate(keys)
    }


def hydrate_text_products(
    client: ClickHouseHttpClient,
    selected: Sequence[dict[str, Any]],
    *,
    report: Callable[[str], None] | None = None,
) -> None:
    emit = report or (lambda _message: None)
    by_id = {row["source_id"]: row for row in selected}
    grouped = group_by_month(list(selected))
    completed = 0
    for month, month_rows in grouped.items():
        for chunk in chunks(month_rows, 250):
            pairs = date_id_pairs(chunk)
            rendered_sql = f"""
SELECT
 canonical_news_id AS source_id,
 provider_article_id,
 toString(published_at_utc) AS published_at_utc,
 title,
 rendered_text,
 rendered_text_hash,
 source_revision_key,
 source_count,
 block_count,
 renderer_version,
 text_contract,
 quality_flags
FROM q_live.benzinga_news_rendered_v2 FINAL
WHERE (published_date, canonical_news_id) IN ({pairs})
FORMAT JSONEachRow
"""
            for value in json_rows(client.execute(rendered_sql)):
                row = by_id.get(str(value["source_id"]))
                if row is not None and revision_matches(row, value):
                    row["rendered"] = value
            source_sql = f"""
SELECT
 canonical_news_id AS source_id,
 source_kind,
 source_ordinal,
 source_url,
 artifact_path,
 content_format,
 source_hash,
 source_chars,
 rendered_text,
 rendered_hash,
 block_count,
 table_block_count,
 quality_flags,
 renderer_version,
 source_revision_key
FROM q_live.benzinga_news_source_v2 FINAL
WHERE (published_date, canonical_news_id) IN ({pairs})
ORDER BY canonical_news_id, source_kind, source_ordinal
FORMAT JSONEachRow
"""
            for value in json_rows(client.execute(source_sql)):
                row = by_id.get(str(value["source_id"]))
                if row is not None and revision_matches(row, value):
                    row["source_lanes"].append(value)
        completed += len(month_rows)
        if completed == len(selected) or len(grouped) <= 12 or completed % 100 < len(month_rows):
            emit(f"TEXT | articles={completed:,}/{len(selected):,} through {month}")
    missing = [row["source_id"] for row in selected if not row.get("rendered")]
    if missing:
        raise RuntimeError(
            f"selected sample contains {len(missing)} missing rendered products; "
            f"first={missing[:5]}"
        )


def build_blinded_item(
    sample_id: str,
    row: Mapping[str, Any],
    *,
    resolver: Any,
    pilot: bool,
) -> dict[str, Any]:
    event = dict(row["event"])
    rendered = dict(row["rendered"])
    source_lanes = [clean_source_lane(value) for value in row["source_lanes"]]
    rendered_text = str(rendered.get("rendered_text") or "")
    linked_tickers = tuple(str(value).upper() for value in event.get("tickers") or ())
    identity_matches = resolver.with_article_identities(rendered_text).resolve(
        rendered_text,
        timestamp=str(row["source_timestamp"]),
        linked_tickers=linked_tickers,
    )
    source_contract = {
        "title": event.get("title") or "",
        "teaser": event.get("teaser") or "",
        "rendered_text": rendered_text,
        "source_lanes": source_lanes,
    }
    source_hash = stable_json_hash(source_contract)
    from .candidate_contract import CANDIDATE_CONTRACT_VERSION, enrich_candidate_rows

    candidate_rows = enrich_candidate_rows(
        (
            {"ticker": match.ticker, "identity_evidence": match.evidence}
            for match in identity_matches
        ),
        title=str(event.get("title") or ""),
        teaser=str(event.get("teaser") or ""),
        rendered_text=rendered_text,
        authoritative_identifiers=linked_tickers,
    )
    return {
        "sample_version": SAMPLE_VERSION,
        "sample_id": sample_id,
        "pilot": pilot,
        "source_id": row["source_id"],
        "source_timestamp": row["source_timestamp"],
        "source_text_sha256": source_hash,
        "publication": {
            "title": event.get("title") or "",
            "teaser": event.get("teaser") or "",
            "author": event.get("author") or "",
            "provider": event.get("provider") or "",
            "provider_article_id": event.get("provider_article_id") or "",
            "article_url": event.get("article_url") or "",
            "url_domain": event.get("url_domain") or "",
            "provider_tickers": linked_tickers,
            "channels": tuple(event.get("channels") or ()),
            "provider_tags": tuple(event.get("provider_tags") or ()),
            "content_quality_flags": tuple(event.get("content_quality_flags") or ()),
        },
        "point_in_time_issuer_candidates": candidate_rows,
        "issuer_candidate_contract_version": CANDIDATE_CONTRACT_VERSION,
        "source_lanes": source_lanes,
        "rendered_product": {
            "text": rendered_text,
            "renderer_version": rendered.get("renderer_version") or "",
            "text_contract": rendered.get("text_contract") or "",
            "quality_flags": tuple(rendered.get("quality_flags") or ()),
            "source_count": int(rendered.get("source_count") or 0),
            "block_count": int(rendered.get("block_count") or 0),
        },
        "reviewer_warning": (
            "Do not consult V5 output, subsequent market reaction, or the sealed "
            "dataset split before this annotation is locked."
        ),
    }


def clean_source_lane(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_kind": value.get("source_kind") or "",
        "source_ordinal": int(value.get("source_ordinal") or 0),
        "source_url": value.get("source_url") or "",
        "content_format": value.get("content_format") or "",
        "source_hash": value.get("source_hash") or "",
        "source_chars": int(value.get("source_chars") or 0),
        "text": value.get("rendered_text") or "",
        "block_count": int(value.get("block_count") or 0),
        "table_block_count": int(value.get("table_block_count") or 0),
        "quality_flags": tuple(value.get("quality_flags") or ()),
        "renderer_version": value.get("renderer_version") or "",
    }


def selection_stratum(row: Mapping[str, Any]) -> str:
    event = row.get("event") or {}
    ticker_count = len(event.get("tickers") or ())
    scope = "zero" if ticker_count == 0 else "single" if ticker_count == 1 else "multi"
    roles = sorted({str(value.get("content_role") or "") for value in row["v5_units"]})
    directions = sorted({
        str(value.get("semantic_direction") or "") for value in row["v5_units"]
    })
    role = "+".join(roles) if roles else "unlabeled"
    direction = "+".join(directions) if directions else "unlabeled"
    return "|".join((era(row["source_timestamp"]), scope, role, direction))


def article_concepts(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({
        str(concept)
        for unit in row["v5_units"]
        for concept in unit.get("event_concepts") or ()
        if str(concept)
    }))


def sealed_v5_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    units = list(row["v5_units"])
    return {
        "present": bool(units),
        "unit_count": len(units),
        "content_roles": sorted({str(value.get("content_role") or "") for value in units}),
        "source_origins": sorted({str(value.get("source_origin") or "") for value in units}),
        "directions": sorted({str(value.get("semantic_direction") or "") for value in units}),
        "scores": [float(value.get("semantic_score") or 0.0) for value in units],
        "concepts": list(article_concepts(row)),
        "forecast_trigger_eligible": any(
            bool(value.get("forecast_trigger_eligible")) for value in units
        ),
        "reaction_evaluation_eligible": any(
            bool(value.get("reaction_evaluation_eligible")) for value in units
        ),
        "issuer_history_context_eligible": any(
            bool(value.get("issuer_history_context_eligible")) for value in units
        ),
    }


def locked_split(row: Mapping[str, Any]) -> str:
    value = int(stable_json_hash([SAMPLING_SEED, "split", row["source_id"]])[:8], 16) % 10
    if value < 6:
        return "fit"
    if value < 8:
        return "calibration"
    return "holdout"


def era(timestamp: str) -> str:
    year = int(str(timestamp)[:4])
    if year < 2015:
        return "2010_2014"
    if year < 2020:
        return "2015_2019"
    if year < 2024:
        return "2020_2023"
    return "2024_2026"


def revision_matches(row: Mapping[str, Any], value: Mapping[str, Any]) -> bool:
    expected = str((row.get("event") or {}).get("source_revision_key") or "")
    actual = str(value.get("source_revision_key") or "")
    return not expected or not actual or expected == actual


def group_by_month(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        timestamp = str(row.get("source_timestamp") or "")
        if len(timestamp) < 10:
            raise ValueError(f"candidate lacks source date: {row.get('source_id')}")
        grouped[timestamp[:7]].append(row)
    return dict(sorted(grouped.items()))


def date_id_pairs(rows: Sequence[Mapping[str, Any]]) -> str:
    return ",".join(
        "(" + sql_string(str(row["source_timestamp"])[:10]) + ","
        + sql_string(str(row["source_id"])) + ")"
        for row in rows
    )


def chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def json_rows(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]
