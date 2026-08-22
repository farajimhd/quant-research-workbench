from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .provider_filter_analysis import (
    SPLITS,
    Counts,
    canonical_json,
    feature_names,
    feature_report_rows,
    iter_jsonl,
    sha256_path,
    write_csv_new,
    write_json_new,
)


ANALYSIS_VERSION = "news_synthesis_provider_filter_residual_analysis_v2"
DEFAULT_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v3_corrected\ARTICLE_FEATURES.jsonl"
)
DEFAULT_SEMANTIC_LABELS = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_candidate_blind_semantic_audit_v1\BLIND_SEMANTIC_LABELS.jsonl"
)
DEFAULT_MISMATCH_CONTROLLER = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_v1\mismatch_audit_controller.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_residual_analysis_v2"
)
EXPECTED_ARTICLES = 346_107
EXPECTED_PRIOR_PATHS = 709
EXPECTED_RESIDUAL_ARTICLES = 160_360
MIN_TOTAL_SUPPORT = 300
MIN_SPLIT_SUPPORT = 30
EXACT_METADATA_PREFIXES = (
    "metadata_signature=",
    "tag=",
    "tag_set=",
    "channel=",
    "channel_set=",
    "tag_channel=",
    "channel_pair=",
)


def load_prior_paths(path: Path) -> set[str]:
    rows = list(iter_jsonl(path))
    paths = {str(row["feature"]) for row in rows}
    if len(rows) != EXPECTED_PRIOR_PATHS or len(paths) != EXPECTED_PRIOR_PATHS:
        raise ValueError(f"expected {EXPECTED_PRIOR_PATHS} unique prior paths")
    if any(row.get("semantic_label") not in {"likely_eligible", "likely_ineligible"} for row in rows):
        raise ValueError("prior path authority contains an invalid semantic label")
    return paths


def candidate_class(row: Mapping[str, Any]) -> str | None:
    supports = [int(row.get(f"{prefix}_support") or 0) for prefix in ("discovery", "validation", "final")]
    rates = [row.get(f"{prefix}_eligible_rate") for prefix in ("discovery", "validation", "final")]
    if int(row.get("support") or 0) < MIN_TOTAL_SUPPORT or min(supports) < MIN_SPLIT_SUPPORT:
        return None
    if any(rate is None for rate in rates):
        return None
    numeric = [float(rate) for rate in rates]
    if max(numeric) <= 0.05:
        return "stable_ineligible"
    if min(numeric) >= 0.95:
        return "stable_eligible"
    if max(numeric) - min(numeric) >= 0.15:
        return "temporal_drift"
    overall = float(row["eligible_rate"])
    if 0.10 <= overall <= 0.90:
        return "stable_mixed"
    return "directional_context"


def select_new_paths(
    reports: Sequence[Mapping[str, Any]], prior_paths: set[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in reports:
        feature = str(row["feature"])
        if feature in prior_paths or not feature.startswith(EXACT_METADATA_PREFIXES):
            continue
        classification = candidate_class(row)
        if classification is None:
            continue
        rates = [float(row[f"{prefix}_eligible_rate"]) for prefix in ("discovery", "validation", "final")]
        result.append({
            **dict(row),
            "path_class": classification,
            "temporal_rate_range": max(rates) - min(rates),
            "blind_review_exception_label": (
                "eligible" if classification == "stable_ineligible"
                else "ineligible" if classification == "stable_eligible"
                else "stratified_sample"
            ),
        })
    order = {
        "stable_ineligible": 0,
        "stable_eligible": 1,
        "stable_mixed": 2,
        "temporal_drift": 3,
        "directional_context": 4,
    }
    result.sort(key=lambda row: (order[str(row["path_class"])], -int(row["support"]), str(row["feature"])))
    return result


def _aggregate_residual(
    article_features: Path,
    prior_paths: set[str],
    residual_ids_path: Path,
) -> tuple[
    dict[str, dict[str, Counts]],
    dict[str, dict[str, Counts]],
    dict[str, Counts],
    Counter[str],
]:
    by_feature: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    by_feature_month: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    split_totals: dict[str, Counts] = defaultdict(Counts)
    population = Counter()
    with residual_ids_path.open("x", encoding="utf-8", newline="\n") as residual_handle:
        for row in iter_jsonl(article_features):
            population["articles"] += 1
            features = feature_names(row)
            if prior_paths.intersection(features):
                population["matched_prior_path"] += 1
                continue
            population["residual_articles"] += 1
            label = str(row["label"])
            split = str(row["split"])
            month = str(row["published_month"])
            population[f"residual_{label}"] += 1
            split_totals[split].add(label)
            residual_handle.write(canonical_json({
                "source_id": str(row["source_id"]),
                "label": label,
                "split": split,
                "published_month": month,
            }) + "\n")
            for feature in features:
                by_feature[feature][split].add(label)
                by_feature_month[feature][month].add(label)
    return by_feature, by_feature_month, split_totals, population


def _write_exception_candidates(
    *,
    article_features: Path,
    prior_paths: set[str],
    candidates: Sequence[Mapping[str, Any]],
    mismatch_by_id: Mapping[str, Mapping[str, Any]],
    output_path: Path,
) -> Counter[str]:
    stable_ineligible = {
        str(row["feature"]) for row in candidates if row["path_class"] == "stable_ineligible"
    }
    stable_eligible = {
        str(row["feature"]) for row in candidates if row["path_class"] == "stable_eligible"
    }
    counts: Counter[str] = Counter()
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(article_features):
            features = set(feature_names(row))
            if prior_paths.intersection(features):
                continue
            label = str(row["label"])
            if label == "eligible":
                matches = sorted(features.intersection(stable_ineligible))
                direction = "eligible_under_stable_ineligible_path"
            else:
                matches = sorted(features.intersection(stable_eligible))
                direction = "ineligible_under_stable_eligible_path"
            if not matches:
                continue
            counts[direction] += 1
            mismatch = mismatch_by_id.get(str(row["source_id"]))
            if mismatch is not None:
                counts["also_combined_model_mismatch"] += 1
                counts[f"combined_model_predicts_{mismatch['combined_model_prediction']}"] += 1
                counts[f"model_audit_state:{mismatch['blind_review_state']}"] += 1
            handle.write(canonical_json({
                "source_id": str(row["source_id"]),
                "current_label": label,
                "split": str(row["split"]),
                "published_at_utc": str(row["published_at_text"]),
                "candidate_reason": direction,
                "matched_new_paths": matches,
                "authority_class": str(row.get("authority_class") or ""),
                "certification_level": str(row.get("certification_level") or ""),
                "source_dataset": str(row.get("source_dataset") or ""),
                "combined_model_mismatch": mismatch is not None,
                "combined_model_prediction": (
                    str(mismatch["combined_model_prediction"]) if mismatch is not None else None
                ),
                "model_audit_state": str(mismatch["blind_review_state"]) if mismatch is not None else None,
            }) + "\n")
    return counts


def _write_drift_provenance(
    *,
    article_features: Path,
    prior_paths: set[str],
    candidates: Sequence[Mapping[str, Any]],
    output_path: Path,
) -> None:
    drift_paths = {
        str(row["feature"])
        for row in sorted(
            (row for row in candidates if row["path_class"] == "temporal_drift"),
            key=lambda row: (-int(row["support"]), str(row["feature"])),
        )[:25]
    }
    counts: Counter[tuple[str, str, str, str, str, str]] = Counter()
    for row in iter_jsonl(article_features):
        features = set(feature_names(row))
        if prior_paths.intersection(features):
            continue
        for feature in features.intersection(drift_paths):
            counts[(
                feature,
                str(row["split"]),
                str(row["label"]),
                str(row.get("source_dataset") or ""),
                str(row.get("authority_class") or ""),
                str(row.get("certification_level") or ""),
            )] += 1
    write_csv_new(output_path, [
        {
            "feature": key[0],
            "split": key[1],
            "label": key[2],
            "source_dataset": key[3],
            "authority_class": key[4],
            "certification_level": key[5],
            "articles": value,
        }
        for key, value in sorted(counts.items())
    ])


def _top_rows(
    rows: Iterable[Mapping[str, Any]], *, path_class: str | None = None, limit: int = 25
) -> list[dict[str, Any]]:
    selected = [dict(row) for row in rows if path_class is None or row.get("path_class") == path_class]
    selected.sort(key=lambda row: (-int(row["support"]), str(row["feature"])))
    return selected[:limit]


def _markdown(report: Mapping[str, Any]) -> str:
    population = report["population"]
    classes = report["candidate_path_classes"]
    exceptions = report["blind_review_exception_candidates"]
    lines = [
        "# Provider-Filter Residual Metadata Analysis V2",
        "",
        "## Population",
        "",
        f"The exact residual contains {population['residual_articles']:,} of {population['articles']:,} corrected decisive articles. "
        f"It excludes every article matching any of the 709 previously labeled candidate paths.",
        "",
        f"- Eligible: {population['residual_eligible']:,}",
        f"- Ineligible: {population['residual_ineligible']:,}",
        f"- Newly observed feature paths: {report['residual_feature_count']:,}",
        f"- Forward-supported new exact metadata paths: {report['new_candidate_path_count']:,}",
        "",
        "## New path classes",
        "",
        *[f"- `{name}`: {count:,}" for name, count in sorted(classes.items())],
        "",
        "## Blind-review exception queue",
        "",
        f"- Eligible articles under stable-ineligible new paths: {exceptions.get('eligible_under_stable_ineligible_path', 0):,}",
        f"- Ineligible articles under stable-eligible new paths: {exceptions.get('ineligible_under_stable_eligible_path', 0):,}",
        f"- Also independently flagged by the combined-model mismatch inventory: {exceptions.get('also_combined_model_mismatch', 0):,}",
        f"- Still unreviewed in that inventory: {exceptions.get('model_audit_state:unreviewed', 0):,}",
        "",
        "These are controller candidates only. A path is not label authority, and reviewers must not see the current label or matched metadata.",
        "",
        "## Interpretation boundary",
        "",
        "- Selection is conditioned on the existing corrected labels and therefore discovers audit candidates, not corrected truth.",
        "- Stable paths require at least 300 residual articles and 30 articles in every temporal split.",
        "- Exact provider tags, channels, sets, pairs, tag-channel interactions, and full metadata signatures are eligible for candidate-path classification.",
        "- Authority class and human-certification fields are measured but excluded from rule discovery.",
        "- Mixed and drifted paths require stratified blind samples; they are never hard rejection rules.",
        "",
    ]
    return "\n".join(lines)


def run_residual_analysis(
    *,
    article_features: Path = DEFAULT_ARTICLE_FEATURES,
    semantic_labels: Path = DEFAULT_SEMANTIC_LABELS,
    mismatch_controller: Path = DEFAULT_MISMATCH_CONTROLLER,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite analysis output: {output_root}")
    output_root.mkdir(parents=True)
    prior_paths = load_prior_paths(semantic_labels)
    mismatch_rows = list(iter_jsonl(mismatch_controller))
    mismatch_by_id = {str(row["source_id"]): row for row in mismatch_rows}
    if len(mismatch_rows) != 35_995 or len(mismatch_by_id) != 35_995:
        raise ValueError("expected 35,995 unique combined-model mismatch rows")
    residual_ids_path = output_root / "RESIDUAL_SOURCE_IDS.jsonl"
    by_feature, by_month, split_totals, population = _aggregate_residual(
        article_features, prior_paths, residual_ids_path
    )
    if population["articles"] != EXPECTED_ARTICLES:
        raise ValueError(f"expected {EXPECTED_ARTICLES:,} articles, found {population['articles']:,}")
    if population["residual_articles"] != EXPECTED_RESIDUAL_ARTICLES:
        raise ValueError(
            f"expected {EXPECTED_RESIDUAL_ARTICLES:,} residual articles, "
            f"found {population['residual_articles']:,}"
        )
    reports = feature_report_rows(by_feature, by_month, split_totals)
    candidates = select_new_paths(reports, prior_paths)
    feature_path = output_root / "RESIDUAL_FEATURE_STRENGTH.csv"
    candidate_path = output_root / "NEW_PATH_CANDIDATES.csv"
    write_csv_new(feature_path, reports)
    write_csv_new(candidate_path, candidates)
    exception_path = output_root / "BLIND_REVIEW_EXCEPTION_CANDIDATES.jsonl"
    exceptions = _write_exception_candidates(
        article_features=article_features,
        prior_paths=prior_paths,
        candidates=candidates,
        mismatch_by_id=mismatch_by_id,
        output_path=exception_path,
    )
    drift_provenance_path = output_root / "TEMPORAL_DRIFT_PROVENANCE.csv"
    _write_drift_provenance(
        article_features=article_features,
        prior_paths=prior_paths,
        candidates=candidates,
        output_path=drift_provenance_path,
    )
    class_counts = Counter(str(row["path_class"]) for row in candidates)
    report = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "population": dict(population),
        "split_population": {
            split: {
                "articles": split_totals[split].total,
                "eligible": split_totals[split].eligible,
                "ineligible": split_totals[split].ineligible,
            }
            for split in SPLITS
        },
        "prior_candidate_paths": len(prior_paths),
        "residual_feature_count": len(reports),
        "new_candidate_path_count": len(candidates),
        "candidate_path_classes": dict(class_counts),
        "blind_review_exception_candidates": dict(exceptions),
        "top_prevalent_metadata_paths": _top_rows(
            (row for row in reports if str(row["feature"]).startswith(EXACT_METADATA_PREFIXES)), limit=50
        ),
        "top_stable_ineligible_paths": _top_rows(candidates, path_class="stable_ineligible"),
        "top_stable_eligible_paths": _top_rows(candidates, path_class="stable_eligible"),
        "top_stable_mixed_paths": _top_rows(candidates, path_class="stable_mixed"),
        "top_temporal_drift_paths": _top_rows(candidates, path_class="temporal_drift"),
        "thresholds": {
            "minimum_total_support": MIN_TOTAL_SUPPORT,
            "minimum_each_split_support": MIN_SPLIT_SUPPORT,
            "stable_ineligible_max_rate_each_split": 0.05,
            "stable_eligible_min_rate_each_split": 0.95,
            "temporal_drift_min_rate_range": 0.15,
        },
        "inputs": {
            "article_features": str(article_features),
            "article_features_sha256": sha256_path(article_features),
            "semantic_labels": str(semantic_labels),
            "semantic_labels_sha256": sha256_path(semantic_labels),
            "mismatch_controller": str(mismatch_controller),
            "mismatch_controller_sha256": sha256_path(mismatch_controller),
        },
    }
    report_path = output_root / "REPORT.json"
    report_md_path = output_root / "REPORT.md"
    write_json_new(report_path, report)
    report_md_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    output_paths = (
        residual_ids_path, feature_path, candidate_path, exception_path, drift_provenance_path,
        report_path, report_md_path,
    )
    validation = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "valid",
        "checks": {
            "exact_article_count": population["articles"] == EXPECTED_ARTICLES,
            "exact_prior_path_count": len(prior_paths) == EXPECTED_PRIOR_PATHS,
            "exact_residual_count": population["residual_articles"] == EXPECTED_RESIDUAL_ARTICLES,
            "population_reconciles": (
                population["matched_prior_path"] + population["residual_articles"] == population["articles"]
            ),
            "candidate_paths_are_new": all(str(row["feature"]) not in prior_paths for row in candidates),
            "candidate_paths_are_exact_metadata": all(
                str(row["feature"]).startswith(EXACT_METADATA_PREFIXES) for row in candidates
            ),
        },
    }
    validation_path = output_root / "VALIDATION.json"
    write_json_new(validation_path, validation)
    hash_manifest = {
        "analysis_version": ANALYSIS_VERSION,
        "inputs": report["inputs"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in (*output_paths, validation_path)
        },
    }
    write_json_new(output_root / "HASH_MANIFEST.json", hash_manifest)
    return report
