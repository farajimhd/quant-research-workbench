from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    load_news_issuer_resolver,
)

from .candidate_contract import CANDIDATE_CONTRACT_VERSION, enrich_candidate_rows
from .sampling import (
    LABELING_VERSION,
    article_concepts,
    chunks,
    date_id_pairs,
    distribute_quota,
    group_by_month,
    hydrate_complete_v5_units,
    hydrate_event_metadata,
    json_rows,
    merge_candidates,
    revision_matches,
    sealed_v5_summary,
)
from .schema import stable_json_hash
from .storage import assert_runtime_root, read_json, write_json_atomic


CORPUS_VERSION = "news_sol_teacher_corpus_v1"
SAMPLING_SEED = "news-sol-teacher-10000-v1-20260801"
START_YEAR = 2010
END_YEAR = 2026
DEFAULT_SAMPLE_SIZE = 10_000
SCOPE_WEIGHTS = {"zero": 15, "single": 50, "multi": 35}
V5_MISSING_TARGET_PERCENT = 15


@dataclass(frozen=True, slots=True)
class TeacherCorpusResult:
    root: Path
    sample_count: int
    exclusion_count: int
    manifest_hash: str


def build_teacher_corpus(
    client: ClickHouseHttpClient,
    root: Path,
    *,
    ground_truth_root: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    report: Callable[[str], None] | None = None,
) -> TeacherCorpusResult:
    """Build one immutable Sol teacher corpus outside the human ground truth.

    V5 is a diversification hint only. It is sealed with the selection audit and
    is never sent to Sol. Canonical event metadata, rendered text, and the
    point-in-time issuer candidate authority form the model input.
    """
    emit = report or (lambda _message: None)
    assert_runtime_root(root)
    assert_runtime_root(ground_truth_root)
    excluded_ids, exclusion_contract = load_ground_truth_exclusion(ground_truth_root)
    manifest_path = root / "sample_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        _validate_existing_manifest(
            manifest,
            sample_size=sample_size,
            exclusion_contract=exclusion_contract,
        )
        return TeacherCorpusResult(
            root=root,
            sample_count=int(manifest["sample_count"]),
            exclusion_count=int(manifest["ground_truth_exclusion"]["source_count"]),
            manifest_hash=str(manifest["sample_manifest_sha256"]),
        )
    if sample_size < END_YEAR - START_YEAR + 1:
        raise ValueError("sample_size must permit at least one item per year")

    emit("CANDIDATES | reading year/category-balanced V5 pool")
    label_rows = fetch_teacher_label_candidates(client)
    emit(f"CANDIDATES | V5 scoped rows={len(label_rows):,}")
    baseline_rows = fetch_teacher_baseline_candidates(client)
    emit(f"CANDIDATES | source-independent rows={len(baseline_rows):,}")
    candidates = merge_candidates(label_rows, baseline_rows)
    for source_id in excluded_ids:
        candidates.pop(source_id, None)
    emit(
        f"EXCLUSION | ground_truth={len(excluded_ids):,} "
        f"remaining_candidates={len(candidates):,}"
    )
    emit(f"METADATA | hydrating candidates={len(candidates):,}")
    hydrate_event_metadata(client, candidates, report=emit)
    hydrate_complete_v5_units(client, candidates, report=emit)
    candidate_rows = [
        value
        for value in candidates.values()
        if value.get("event")
        and START_YEAR <= source_year(value) <= END_YEAR
        and value["source_id"] not in excluded_ids
    ]
    selected = select_teacher_candidates(candidate_rows, sample_size=sample_size)
    if len(selected) != sample_size:
        raise RuntimeError(
            f"teacher sampling produced {len(selected):,}; expected {sample_size:,}"
        )
    overlap = sorted({row["source_id"] for row in selected} & excluded_ids)
    if overlap:
        raise RuntimeError(f"teacher selection overlaps ground truth: {overlap[:5]}")

    emit(f"SELECTED | articles={len(selected):,}; loading rendered products")
    hydrate_teacher_rendered_products(client, selected, report=emit)
    emit("IDENTITY | loading point-in-time issuer authority")
    resolver = load_news_issuer_resolver(client)

    ordered = sorted(
        selected,
        key=lambda row: stable_json_hash(
            [SAMPLING_SEED, "teacher-order", row["source_id"]]
        ),
    )
    manifest_items: list[dict[str, Any]] = []
    sealed_rows: list[dict[str, Any]] = []
    for index, row in enumerate(ordered, 1):
        sample_id = f"S{index:05d}"
        item = build_teacher_item(sample_id, row, resolver=resolver)
        item_hash = stable_json_hash(item)
        item["teacher_item_sha256"] = item_hash
        write_json_atomic(root / "items" / f"{sample_id}.json", item)
        manifest_items.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "source_timestamp": row["source_timestamp"],
                "source_text_sha256": item["source_text_sha256"],
                "teacher_item_sha256": item_hash,
                "selection_year": source_year(row),
                "selection_stratum": teacher_selection_stratum(row),
            }
        )
        sealed_rows.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "selection_year": source_year(row),
                "selection_stratum": teacher_selection_stratum(row),
                "v5_diversification_hint": sealed_v5_summary(row),
            }
        )
        if index % 250 == 0 or index == sample_size:
            emit(f"WRITE | teacher items={index:,}/{sample_size:,}")

    distribution = teacher_distribution(selected)
    selection_ids = [str(row["source_id"]) for row in selected]
    selection_hash = stable_json_hash(selection_ids)
    manifest = {
        "corpus_version": CORPUS_VERSION,
        "sampling_seed": SAMPLING_SEED,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sample_count": sample_size,
        "year_start": START_YEAR,
        "year_end": END_YEAR,
        "selection_method": (
            "equal_calendar_year_quota_then_deterministic_round_robin_over_"
            "ticker_scope_v5_role_origin_direction_eligibility_and_concept"
        ),
        "selection_sha256": selection_hash,
        "ground_truth_exclusion": exclusion_contract,
        "distribution": distribution,
        "items": manifest_items,
    }
    manifest["sample_manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(manifest_path, manifest)
    sealed = {
        "corpus_version": CORPUS_VERSION,
        "selection_sha256": selection_hash,
        "warning": "V5 fields are selection hints only and were not sent to Sol.",
        "items": sealed_rows,
    }
    sealed["sealed_selection_sha256"] = stable_json_hash(sealed)
    write_json_atomic(root / "sealed" / "selection_audit.json", sealed)
    emit(
        f"READY | sample={sample_size:,} overlap=0 "
        f"years={START_YEAR}-{END_YEAR}"
    )
    return TeacherCorpusResult(
        root=root,
        sample_count=sample_size,
        exclusion_count=len(excluded_ids),
        manifest_hash=str(manifest["sample_manifest_sha256"]),
    )


def load_ground_truth_exclusion(root: Path) -> tuple[set[str], dict[str, Any]]:
    manifest = read_json(root / "sample_manifest.json")
    expected = int(manifest.get("sample_count") or 0)
    items = manifest.get("items") or ()
    source_ids = [str(row.get("source_id") or "") for row in items]
    if expected != 1_000 or len(items) != expected:
        raise RuntimeError(
            "The exclusion authority must be the complete 1,000-item ground truth."
        )
    if any(not value for value in source_ids) or len(set(source_ids)) != expected:
        raise RuntimeError("Ground-truth source identities are missing or duplicated.")
    contract = {
        "sample_version": str(manifest.get("sample_version") or ""),
        "sample_manifest_sha256": str(manifest.get("sample_manifest_sha256") or ""),
        "source_count": expected,
        "source_ids_sha256": stable_json_hash(sorted(source_ids)),
        "overlap_allowed": False,
    }
    return set(source_ids), contract


def fetch_teacher_label_candidates(
    client: ClickHouseHttpClient,
    *,
    sampling_seed: str = SAMPLING_SEED,
    per_stratum_limit: int = 32,
) -> list[dict[str, Any]]:
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
WHERE corpus='news'
  AND labeling_version={sql_string(LABELING_VERSION)}
  AND toUInt16OrZero(substring(source_timestamp, 1, 4)) BETWEEN 2010 AND 2026
ORDER BY cityHash64(concat(source_id, ticker, {sql_string(sampling_seed)}))
LIMIT {int(per_stratum_limit)} BY
 toUInt16OrZero(substring(source_timestamp, 1, 4)),
 content_role, source_origin, semantic_direction,
 forecast_trigger_eligible, reaction_evaluation_eligible
FORMAT JSONEachRow
"""
    return json_rows(client.execute(sql))


def fetch_teacher_baseline_candidates(
    client: ClickHouseHttpClient,
    *,
    sampling_seed: str = SAMPLING_SEED,
    per_stratum_limit: int = 320,
) -> list[dict[str, Any]]:
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
  AND published_at_utc < toDateTime64('2027-01-01 00:00:00', 9, 'UTC')
ORDER BY cityHash64(concat(canonical_news_id, {sql_string(sampling_seed)}))
LIMIT {int(per_stratum_limit)} BY
 toYear(published_at_utc),
 multiIf(length(tickers)=0, 'zero', length(tickers)=1, 'single', 'multi'),
 multiIf(length(title)+length(teaser)<160, 'short',
         length(title)+length(teaser)<500, 'medium', 'long')
FORMAT JSONEachRow
"""
    return json_rows(client.execute(sql))


def select_teacher_candidates(
    candidates: Iterable[dict[str, Any]],
    *,
    sample_size: int,
    sampling_seed: str = SAMPLING_SEED,
) -> list[dict[str, Any]]:
    rows_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        rows_by_year[source_year(row)].append(row)
    years = tuple(range(START_YEAR, END_YEAR + 1))
    quotas = distribute_quota(sample_size, tuple(str(year) for year in years))
    selected: list[dict[str, Any]] = []
    for year in years:
        rows = rows_by_year.get(year, [])
        target = quotas[str(year)]
        if len(rows) < target:
            raise RuntimeError(
                f"year {year} has {len(rows):,} candidates after exclusion; "
                f"needs {target:,}"
            )
        selected.extend(
            select_teacher_year(rows, target=target, sampling_seed=sampling_seed)
        )
    return selected


def select_teacher_year(
    rows: Sequence[dict[str, Any]],
    *,
    target: int,
    sampling_seed: str = SAMPLING_SEED,
) -> list[dict[str, Any]]:
    by_scope: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scope[ticker_scope(row)].append(row)
    quotas = weighted_quota(target, SCOPE_WEIGHTS)
    selected: list[dict[str, Any]] = []
    for scope in ("zero", "single", "multi"):
        scope_rows = by_scope.get(scope, [])
        scope_target = quotas[scope]
        if len(scope_rows) < scope_target:
            raise RuntimeError(
                f"year {source_year(rows[0])} scope {scope} has "
                f"{len(scope_rows):,} candidates; needs {scope_target:,}"
            )
        missing = [row for row in scope_rows if not row.get("v5_units")]
        labeled = [row for row in scope_rows if row.get("v5_units")]
        missing_target = min(
            len(missing),
            round(scope_target * V5_MISSING_TARGET_PERCENT / 100),
        )
        missing_selected = round_robin_teacher_strata(
            missing, target=missing_target, sampling_seed=sampling_seed
        ) if missing_target else []
        used = {str(row["source_id"]) for row in missing_selected}
        remainder_rows = [
            row
            for row in (*labeled, *missing)
            if str(row["source_id"]) not in used
        ]
        selected.extend(missing_selected)
        selected.extend(
            round_robin_teacher_strata(
                remainder_rows,
                target=scope_target - len(missing_selected),
                sampling_seed=sampling_seed,
            )
        )
    return selected


def round_robin_teacher_strata(
    rows: Sequence[dict[str, Any]],
    *,
    target: int,
    sampling_seed: str = SAMPLING_SEED,
) -> list[dict[str, Any]]:
    concept_frequency = Counter(
        concept for row in rows for concept in article_concepts(row)
    )
    buckets: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for row in rows:
        concepts = article_concepts(row)
        rarest = (
            min(concepts, key=lambda value: (concept_frequency[value], value))
            if concepts
            else "none"
        )
        key = teacher_selection_stratum(row) + f"|concept={rarest}"
        buckets[key].append(row)
    for key, values in tuple(buckets.items()):
        buckets[key] = deque(
            sorted(
                values,
                key=lambda row: stable_json_hash(
                    [sampling_seed, "within-stratum", row["source_id"]]
                ),
            )
        )
    keys = sorted(
        buckets,
        key=lambda key: stable_json_hash([sampling_seed, "stratum", key]),
    )
    output: list[dict[str, Any]] = []
    while len(output) < target:
        progressed = False
        for key in keys:
            if buckets[key]:
                output.append(buckets[key].popleft())
                progressed = True
                if len(output) == target:
                    break
        if not progressed:
            break
    if len(output) != target:
        raise RuntimeError(f"stratified selection produced {len(output)} of {target}")
    return output


def teacher_selection_stratum(row: Mapping[str, Any]) -> str:
    event = row.get("event") or {}
    scope = ticker_scope(row)
    units = row.get("v5_units") or ()
    roles = "+".join(sorted({str(unit.get("content_role") or "") for unit in units}))
    origins = "+".join(sorted({str(unit.get("source_origin") or "") for unit in units}))
    directions = "+".join(
        sorted({str(unit.get("semantic_direction") or "") for unit in units})
    )
    flags = "".join(
        (
            "F" if any(bool(unit.get("forecast_trigger_eligible")) for unit in units) else "-",
            "R" if any(bool(unit.get("reaction_evaluation_eligible")) for unit in units) else "-",
            "H" if any(bool(unit.get("issuer_history_context_eligible")) for unit in units) else "-",
        )
    )
    headline_chars = len(str(event.get("title") or "")) + len(
        str(event.get("teaser") or "")
    )
    length_bucket = (
        "short" if headline_chars < 160 else "medium" if headline_chars < 500 else "long"
    )
    return "|".join(
        (
            f"year={source_year(row)}",
            f"scope={scope}",
            f"role={roles or 'unlabeled'}",
            f"origin={origins or 'unlabeled'}",
            f"direction={directions or 'unlabeled'}",
            f"eligibility={flags}",
            f"text_length={length_bucket}",
        )
    )


def ticker_scope(row: Mapping[str, Any]) -> str:
    event = row.get("event") or {}
    ticker_count = len(event.get("tickers") or ())
    return "zero" if ticker_count == 0 else "single" if ticker_count == 1 else "multi"


def weighted_quota(total: int, weights: Mapping[str, int]) -> dict[str, int]:
    weight_total = sum(weights.values())
    raw = {key: total * value / weight_total for key, value in weights.items()}
    quota = {key: int(value) for key, value in raw.items()}
    remainder = total - sum(quota.values())
    order = {key: index for index, key in enumerate(weights)}
    for key in sorted(
        weights,
        key=lambda item: (-(raw[item] - quota[item]), order[item]),
    )[:remainder]:
        quota[key] += 1
    return quota


def hydrate_teacher_rendered_products(
    client: ClickHouseHttpClient,
    selected: Sequence[dict[str, Any]],
    *,
    report: Callable[[str], None] | None = None,
) -> None:
    emit = report or (lambda _message: None)
    by_id = {str(row["source_id"]): row for row in selected}
    grouped = group_by_month(list(selected))
    completed = 0
    for month, month_rows in grouped.items():
        for chunk in chunks(month_rows, 300):
            sql = f"""
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
WHERE (published_date, canonical_news_id) IN ({date_id_pairs(chunk)})
FORMAT JSONEachRow
"""
            for value in json_rows(client.execute(sql)):
                row = by_id.get(str(value["source_id"]))
                if row is not None and revision_matches(row, value):
                    row["rendered"] = value
        completed += len(month_rows)
        emit(f"TEXT | articles={completed:,}/{len(selected):,} through {month}")
    missing = [str(row["source_id"]) for row in selected if not row.get("rendered")]
    if missing:
        raise RuntimeError(
            f"teacher selection contains {len(missing):,} missing rendered products; "
            f"first={missing[:5]}"
        )


def build_teacher_item(
    sample_id: str, row: Mapping[str, Any], *, resolver: Any
) -> dict[str, Any]:
    event = dict(row["event"])
    rendered = dict(row["rendered"])
    rendered_text = str(rendered.get("rendered_text") or "")
    linked_tickers = tuple(str(value).upper() for value in event.get("tickers") or ())
    matches = resolver.with_article_identities(rendered_text).resolve(
        rendered_text,
        timestamp=str(row["source_timestamp"]),
        linked_tickers=linked_tickers,
    )
    candidates = enrich_candidate_rows(
        (
            {"ticker": match.ticker, "identity_evidence": match.evidence}
            for match in matches
        ),
        title=str(event.get("title") or ""),
        teaser=str(event.get("teaser") or ""),
        rendered_text=rendered_text,
        authoritative_identifiers=linked_tickers,
    )
    source_hash = stable_json_hash(
        {
            "title": event.get("title") or "",
            "teaser": event.get("teaser") or "",
            "rendered_text": rendered_text,
        }
    )
    return {
        "corpus_version": CORPUS_VERSION,
        "sample_id": sample_id,
        "source_id": row["source_id"],
        "source_timestamp": row["source_timestamp"],
        "source_text_sha256": source_hash,
        "publication": {
            "title": event.get("title") or "",
            "teaser": event.get("teaser") or "",
            "author": event.get("author") or "",
            "provider": event.get("provider") or "",
            "provider_tickers": linked_tickers,
            "channels": tuple(event.get("channels") or ()),
            "provider_tags": tuple(event.get("provider_tags") or ()),
            "content_quality_flags": tuple(event.get("content_quality_flags") or ()),
        },
        "point_in_time_issuer_candidates": candidates,
        "issuer_candidate_contract_version": CANDIDATE_CONTRACT_VERSION,
        "rendered_product": {
            "text": rendered_text,
            "renderer_version": rendered.get("renderer_version") or "",
            "text_contract": rendered.get("text_contract") or "",
            "quality_flags": tuple(rendered.get("quality_flags") or ()),
            "source_count": int(rendered.get("source_count") or 0),
            "block_count": int(rendered.get("block_count") or 0),
        },
    }


def teacher_distribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    year_counts = Counter(str(source_year(row)) for row in rows)
    scope_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    origin_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    concept_counts: Counter[str] = Counter()
    label_presence: Counter[str] = Counter()
    for row in rows:
        scope_counts[ticker_scope(row)] += 1
        units = row.get("v5_units") or ()
        label_presence["v5_present" if units else "v5_missing"] += 1
        role_counts.update({str(unit.get("content_role") or "unknown") for unit in units})
        origin_counts.update({str(unit.get("source_origin") or "unknown") for unit in units})
        direction_counts.update(
            {str(unit.get("semantic_direction") or "unknown") for unit in units}
        )
        concept_counts.update(article_concepts(row))
    return {
        "calendar_year": dict(sorted(year_counts.items())),
        "ticker_scope": dict(sorted(scope_counts.items())),
        "v5_label_presence": dict(sorted(label_presence.items())),
        "v5_content_role": dict(sorted(role_counts.items())),
        "v5_source_origin": dict(sorted(origin_counts.items())),
        "v5_semantic_direction": dict(sorted(direction_counts.items())),
        "v5_event_concepts": dict(sorted(concept_counts.items())),
    }


def source_year(row: Mapping[str, Any]) -> int:
    value = str(row.get("source_timestamp") or "")
    if len(value) < 4 or not value[:4].isdigit():
        raise ValueError(f"candidate lacks a valid source year: {row.get('source_id')}")
    return int(value[:4])


def load_teacher_items(root: Path) -> tuple[dict[str, Any], ...]:
    manifest = read_json(root / "sample_manifest.json")
    output: list[dict[str, Any]] = []
    for row in manifest.get("items") or ():
        sample_id = str(row["sample_id"])
        item = read_json(root / "items" / f"{sample_id}.json")
        if stable_json_hash({key: value for key, value in item.items() if key != "teacher_item_sha256"}) != str(
            row["teacher_item_sha256"]
        ):
            raise RuntimeError(f"teacher item drift: {sample_id}")
        output.append(item)
    if len(output) != int(manifest["sample_count"]):
        raise RuntimeError("teacher corpus item count drift")
    return tuple(output)


def _validate_existing_manifest(
    manifest: Mapping[str, Any],
    *,
    sample_size: int,
    exclusion_contract: Mapping[str, Any],
) -> None:
    if manifest.get("corpus_version") != CORPUS_VERSION:
        raise RuntimeError("existing teacher corpus version drift")
    if int(manifest.get("sample_count") or 0) != sample_size:
        raise RuntimeError("existing teacher corpus sample-size drift")
    if manifest.get("ground_truth_exclusion") != exclusion_contract:
        raise RuntimeError("existing teacher corpus ground-truth exclusion drift")
    source_ids = [str(row.get("source_id") or "") for row in manifest.get("items") or ()]
    if len(source_ids) != sample_size or len(set(source_ids)) != sample_size:
        raise RuntimeError("existing teacher corpus identities are missing or duplicated")
