from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from research.mlops.clickhouse import ClickHouseHttpClient
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    load_news_issuer_resolver,
)

from .annotation_template import annotation_template
from .comparison import load_collection
from .sampling import (
    build_blinded_item,
    hydrate_complete_v5_units,
    hydrate_event_metadata,
    hydrate_text_products,
    merge_candidates,
    sealed_v5_summary,
)
from .schema import ANNOTATION_VERSION_V3, SAMPLE_VERSION, stable_json_hash
from .sol_teacher_corpus import (
    END_YEAR,
    START_YEAR,
    fetch_teacher_baseline_candidates,
    fetch_teacher_label_candidates,
    select_teacher_candidates,
    source_year,
    teacher_distribution,
    teacher_selection_stratum,
)
from .storage import (
    annotation_directory,
    assert_runtime_root,
    read_json,
    write_json_atomic,
)


ACCEPTANCE_VERSION = "news_semantic_fresh_acceptance_v1"
ACCEPTANCE_SEED = "news-fresh-acceptance-100-v1-20260801"
DEFAULT_SAMPLE_SIZE = 100
DEFAULT_SAMPLE_ID_START = 1_001


@dataclass(frozen=True, slots=True)
class AcceptanceBuildResult:
    root: Path
    sample_count: int
    excluded_count: int
    manifest_hash: str


def build_acceptance_sample(
    client: ClickHouseHttpClient,
    root: Path,
    *,
    human_root: Path,
    teacher_root: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    report: Callable[[str], None] | None = None,
) -> AcceptanceBuildResult:
    """Build a prediction-blind acceptance set outside all prior supervision.

    Existing human and Sol teacher source identities are immutable exclusions.
    V5 is used only as a sealed diversification hint. It is never exposed in
    reviewer-visible articles or used as an annotation suggestion.
    """
    emit = report or (lambda _message: None)
    assert_runtime_root(root)
    exclusions, exclusion_contract = load_prior_supervision_exclusion(
        human_root=human_root,
        teacher_root=teacher_root,
    )
    manifest_path = root / "sample_manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        _validate_existing_acceptance_manifest(
            manifest,
            sample_size=sample_size,
            exclusion_contract=exclusion_contract,
        )
        return AcceptanceBuildResult(
            root=root,
            sample_count=int(manifest["sample_count"]),
            excluded_count=int(manifest["prior_supervision_exclusion"]["source_count"]),
            manifest_hash=str(manifest["sample_manifest_sha256"]),
        )
    if sample_size != DEFAULT_SAMPLE_SIZE:
        raise ValueError("fresh acceptance v1 is frozen at exactly 100 articles")

    emit("CANDIDATES | reading fresh year/category-balanced V5 pool")
    labels = fetch_teacher_label_candidates(
        client,
        sampling_seed=ACCEPTANCE_SEED,
        per_stratum_limit=96,
    )
    baseline = fetch_teacher_baseline_candidates(
        client,
        sampling_seed=ACCEPTANCE_SEED,
        per_stratum_limit=640,
    )
    candidates = merge_candidates(labels, baseline)
    for source_id in exclusions:
        candidates.pop(source_id, None)
    emit(
        f"EXCLUSION | prior_supervision={len(exclusions):,} "
        f"remaining_candidates={len(candidates):,}"
    )
    hydrate_event_metadata(client, candidates, report=emit)
    hydrate_complete_v5_units(client, candidates, report=emit)
    eligible = [
        row
        for row in candidates.values()
        if row.get("event")
        and START_YEAR <= source_year(row) <= END_YEAR
        and str(row["source_id"]) not in exclusions
    ]
    selected = select_teacher_candidates(
        eligible,
        sample_size=sample_size,
        sampling_seed=ACCEPTANCE_SEED,
    )
    selected_ids = {str(row["source_id"]) for row in selected}
    overlap = sorted(selected_ids & exclusions)
    if overlap:
        raise RuntimeError(f"fresh acceptance overlaps prior supervision: {overlap[:5]}")
    if len(selected_ids) != sample_size:
        raise RuntimeError("fresh acceptance contains duplicate source identities")

    emit(f"SELECTED | articles={len(selected):,}; loading complete text products")
    hydrate_text_products(client, selected, report=emit)
    emit("IDENTITY | loading point-in-time issuer authority")
    resolver = load_news_issuer_resolver(client)
    ordered = sorted(
        selected,
        key=lambda row: stable_json_hash(
            [ACCEPTANCE_SEED, "review-order", row["source_id"]]
        ),
    )
    manifest_items: list[dict[str, Any]] = []
    sealed_items: list[dict[str, Any]] = []
    for offset, row in enumerate(ordered):
        sample_id = f"N{DEFAULT_SAMPLE_ID_START + offset:04d}"
        blinded = build_blinded_item(
            sample_id,
            row,
            resolver=resolver,
            pilot=False,
        )
        blinded["sample_version"] = SAMPLE_VERSION
        blinded["reviewer_warning"] = (
            "Fresh acceptance review: do not consult V5/V9/V10 output, Sol labels, "
            "subsequent reaction, or the sealed selection metadata before locking."
        )
        blinded_hash = stable_json_hash(blinded)
        blinded["blinded_item_sha256"] = blinded_hash
        write_json_atomic(root / "blinded_articles" / f"{sample_id}.json", blinded)
        write_json_atomic(
            root / "annotation_templates" / f"{sample_id}.json",
            annotation_template(blinded),
        )
        manifest_items.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "source_timestamp": row["source_timestamp"],
                "source_text_sha256": blinded["source_text_sha256"],
                "blinded_item_sha256": blinded_hash,
                "pilot": False,
            }
        )
        sealed_items.append(
            {
                "sample_id": sample_id,
                "source_id": row["source_id"],
                "locked_split": "fresh_acceptance",
                "selection_year": source_year(row),
                "selection_stratum": teacher_selection_stratum(row),
                "v5_diversification_hint": sealed_v5_summary(row),
            }
        )
        if (offset + 1) % 20 == 0:
            emit(f"WRITE | fresh blinded items={offset + 1:,}/{sample_size:,}")

    selection_ids = [str(row["source_id"]) for row in ordered]
    manifest = {
        "sample_version": SAMPLE_VERSION,
        "collection_version": ACCEPTANCE_VERSION,
        "sampling_seed": ACCEPTANCE_SEED,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sample_count": sample_size,
        "pilot_count": 0,
        "selection_method": (
            "equal_calendar_year_quota_then_deterministic_round_robin_over_"
            "ticker_scope_v5_role_origin_direction_eligibility_and_concept"
        ),
        "selection_sha256": stable_json_hash(selection_ids),
        "prior_supervision_exclusion": exclusion_contract,
        "distribution": teacher_distribution(selected),
        "blinding": {
            "visible": [
                "source and rendered text",
                "publication time",
                "provider metadata and ticker links",
                "point-in-time issuer identity matches",
            ],
            "sealed": [
                "V5 labels and selection hints",
                "V9 and V10 predictions",
                "Sol labels",
                "subsequent price reaction",
            ],
        },
        "items": manifest_items,
    }
    manifest["sample_manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(manifest_path, manifest)
    sealed = {
        "sample_version": SAMPLE_VERSION,
        "collection_version": ACCEPTANCE_VERSION,
        "selection_sha256": manifest["selection_sha256"],
        "warning": "Do not open before all 100 manual annotations are frozen.",
        "items": sealed_items,
    }
    sealed["sealed_comparison_sha256"] = stable_json_hash(sealed)
    write_json_atomic(root / "sealed" / "v5_comparison_and_splits.json", sealed)
    write_json_atomic(
        root / "annotation_state_v3.json",
        {
            "annotation_version": ANNOTATION_VERSION_V3,
            "sample_manifest_sha256": manifest["sample_manifest_sha256"],
            "expected": sample_size,
            "completed": 0,
            "remaining": sample_size,
            "unexpected": [],
        },
    )
    emit(
        f"READY | fresh={sample_size:,} excluded={len(exclusions):,} overlap=0 "
        f"years={START_YEAR}-{END_YEAR}"
    )
    return AcceptanceBuildResult(
        root=root,
        sample_count=sample_size,
        excluded_count=len(exclusions),
        manifest_hash=str(manifest["sample_manifest_sha256"]),
    )


def load_prior_supervision_exclusion(
    *, human_root: Path, teacher_root: Path
) -> tuple[set[str], dict[str, Any]]:
    human = read_json(human_root / "sample_manifest.json")
    teacher = read_json(teacher_root / "sample_manifest.json")
    human_ids = _manifest_source_ids(human, expected=1_000, name="human")
    teacher_ids = _manifest_source_ids(teacher, expected=10_000, name="Sol teacher")
    overlap = human_ids & teacher_ids
    if overlap:
        raise RuntimeError(f"existing human and teacher authorities overlap: {len(overlap):,}")
    source_ids = human_ids | teacher_ids
    return source_ids, {
        "human_manifest_sha256": str(human.get("sample_manifest_sha256") or ""),
        "human_source_count": len(human_ids),
        "teacher_manifest_sha256": str(teacher.get("sample_manifest_sha256") or ""),
        "teacher_source_count": len(teacher_ids),
        "source_count": len(source_ids),
        "source_ids_sha256": stable_json_hash(sorted(source_ids)),
        "overlap_allowed": False,
    }


def build_combined_human_authority(
    *, original_root: Path, acceptance_root: Path, combined_root: Path
) -> Path:
    """Materialize a self-contained 1,100-item human authority after review."""
    assert_runtime_root(combined_root)
    original_items = load_collection(original_root, annotation_version=ANNOTATION_VERSION_V3)
    acceptance_items = load_collection(
        acceptance_root,
        annotation_version=ANNOTATION_VERSION_V3,
    )
    if len(original_items) != 1_000 or len(acceptance_items) != 100:
        raise RuntimeError("combined authority requires complete 1,000 + 100 collections")
    all_items = (*original_items, *acceptance_items)
    source_ids = [str(item.blinded["source_id"]) for item in all_items]
    if len(set(source_ids)) != 1_100:
        raise RuntimeError("combined authority source identities overlap")

    original_manifest = read_json(original_root / "sample_manifest.json")
    acceptance_manifest = read_json(acceptance_root / "sample_manifest.json")
    original_sealed = read_json(original_root / "sealed" / "v5_comparison_and_splits.json")
    acceptance_sealed = read_json(
        acceptance_root / "sealed" / "v5_comparison_and_splits.json"
    )
    component_contract = {
        "original_manifest_sha256": str(original_manifest["sample_manifest_sha256"]),
        "acceptance_manifest_sha256": str(acceptance_manifest["sample_manifest_sha256"]),
        "original_annotations_sha256": stable_json_hash(sorted(
            (item.sample_id, str(item.truth.get("annotation_sha256") or ""))
            for item in original_items
        )),
        "acceptance_annotations_sha256": stable_json_hash(sorted(
            (item.sample_id, str(item.truth.get("annotation_sha256") or ""))
            for item in acceptance_items
        )),
        "source_ids_sha256": stable_json_hash(sorted(source_ids)),
    }
    manifest_path = combined_root / "sample_manifest.json"
    rebuilding_annotations = False
    if manifest_path.exists():
        existing = read_json(manifest_path)
        existing_contract = existing.get("component_contract") or {}
        if (
            existing_contract.get("original_manifest_sha256")
            != component_contract["original_manifest_sha256"]
            or existing_contract.get("acceptance_manifest_sha256")
            != component_contract["acceptance_manifest_sha256"]
            or existing_contract.get("source_ids_sha256")
            != component_contract["source_ids_sha256"]
        ):
            raise RuntimeError("existing combined authority identity/manifest drift")
        if existing_contract == component_contract:
            return combined_root
        rebuilding_annotations = True

    annotation_target = annotation_directory(combined_root, ANNOTATION_VERSION_V3)
    original_ids = {item.sample_id for item in original_items}
    for item in all_items:
        sample_id = item.sample_id
        source_root = original_root if sample_id in original_ids else acceptance_root
        _copy_exact(
            source_root / "blinded_articles" / f"{sample_id}.json",
            combined_root / "blinded_articles" / f"{sample_id}.json",
        )
        _copy_exact(
            annotation_directory(source_root, ANNOTATION_VERSION_V3)
            / f"{sample_id}.json",
            annotation_target / f"{sample_id}.json",
            allow_review_update=rebuilding_annotations,
        )
    manifest = {
        "sample_version": "news_semantic_ground_truth_1100_v1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sample_count": 1_100,
        "pilot_count": int(original_manifest.get("pilot_count") or 0),
        "component_contract": component_contract,
        "items": [*original_manifest["items"], *acceptance_manifest["items"]],
    }
    manifest["sample_manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(manifest_path, manifest)
    sealed = {
        "sample_version": manifest["sample_version"],
        "component_contract": component_contract,
        "items": [*original_sealed["items"], *acceptance_sealed["items"]],
    }
    sealed["sealed_selection_sha256"] = stable_json_hash(sealed)
    write_json_atomic(combined_root / "sealed" / "v5_comparison_and_splits.json", sealed)
    return combined_root


def _copy_exact(
    source: Path, target: Path, *, allow_review_update: bool = False
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != source.read_bytes():
            if not allow_review_update:
                raise RuntimeError(f"combined authority file drift: {target}")
            write_json_atomic(target, read_json(source))
        return
    shutil.copy2(source, target)


def _manifest_source_ids(
    manifest: Mapping[str, Any], *, expected: int, name: str
) -> set[str]:
    rows = manifest.get("items") or ()
    values = [str(row.get("source_id") or "") for row in rows]
    if int(manifest.get("sample_count") or 0) != expected or len(values) != expected:
        raise RuntimeError(f"{name} manifest must contain exactly {expected:,} rows")
    if any(not value for value in values) or len(set(values)) != expected:
        raise RuntimeError(f"{name} source identities are missing or duplicated")
    return set(values)


def _validate_existing_acceptance_manifest(
    manifest: Mapping[str, Any],
    *,
    sample_size: int,
    exclusion_contract: Mapping[str, Any],
) -> None:
    if manifest.get("sample_version") != SAMPLE_VERSION:
        raise RuntimeError("existing fresh acceptance sample-contract drift")
    if manifest.get("collection_version") != ACCEPTANCE_VERSION:
        raise RuntimeError("existing fresh acceptance version drift")
    if int(manifest.get("sample_count") or 0) != sample_size:
        raise RuntimeError("existing fresh acceptance sample-size drift")
    if manifest.get("prior_supervision_exclusion") != exclusion_contract:
        raise RuntimeError("existing fresh acceptance exclusion drift")
    ids = [str(row.get("source_id") or "") for row in manifest.get("items") or ()]
    if len(ids) != sample_size or len(set(ids)) != sample_size:
        raise RuntimeError("existing fresh acceptance identities are missing or duplicated")
