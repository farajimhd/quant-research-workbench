from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .provider_filter_analysis import (
    SPLITS,
    Counts,
    feature_names,
    feature_report_rows,
    iter_jsonl,
    sha256_path,
    write_csv_new,
    write_json_new,
)
from .provider_filter_residual_analysis import candidate_class


ANALYSIS_VERSION = "news_synthesis_provider_filter_merged_path_analysis_v3"
DEFAULT_BASELINE_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v3_corrected\ARTICLE_FEATURES.jsonl"
)
DEFAULT_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v4_trading_ideas_corrected\ARTICLE_FEATURES.jsonl"
)
DEFAULT_SEMANTIC_PATHS = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_candidate_blind_semantic_audit_v1\SEMANTIC_LABELS_WITH_RESULTS.csv"
)
DEFAULT_RESIDUAL_PATHS = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_residual_analysis_v2\NEW_PATH_CANDIDATES.csv"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_merged_path_analysis_v3_trading_ideas_corrected"
)
EXPECTED_SEMANTIC_PATHS = 709
EXPECTED_RESIDUAL_PATHS = 423


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_article_features(path: Path) -> dict[str, str]:
    manifest_path = path.parent / "HASH_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = str(manifest["outputs"][path.name]["sha256"])
    actual = sha256_path(path)
    if actual != expected:
        raise ValueError("article feature SHA-256 mismatch")
    return {"manifest": str(manifest_path), "expected_sha256": expected, "actual_sha256": actual}


def merge_path_catalogs(
    semantic_rows: Sequence[Mapping[str, Any]],
    residual_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if len(semantic_rows) != EXPECTED_SEMANTIC_PATHS:
        raise ValueError(f"expected {EXPECTED_SEMANTIC_PATHS} semantic paths")
    if len(residual_rows) != EXPECTED_RESIDUAL_PATHS:
        raise ValueError(f"expected {EXPECTED_RESIDUAL_PATHS} residual paths")
    semantic_features = [str(row["feature"]) for row in semantic_rows]
    residual_features = [str(row["feature"]) for row in residual_rows]
    if len(set(semantic_features)) != len(semantic_features):
        raise ValueError("semantic path catalog contains duplicate features")
    if len(set(residual_features)) != len(residual_features):
        raise ValueError("residual path catalog contains duplicate features")
    overlap = set(semantic_features).intersection(residual_features)
    if overlap:
        raise ValueError(f"path catalogs overlap on {len(overlap)} features")

    merged: list[dict[str, Any]] = []
    for row in semantic_rows:
        semantic_label = str(row["semantic_label"])
        if semantic_label not in {"likely_eligible", "likely_ineligible"}:
            raise ValueError(f"invalid semantic label: {semantic_label}")
        merged.append({
            "feature": str(row["feature"]),
            "category": str(row["category"]),
            "catalog_origin": "blind_semantic_709",
            "blind_id": str(row.get("blind_id") or ""),
            "semantic_label": semantic_label,
            "semantic_confidence": row.get("confidence"),
            "semantic_rationale": str(row.get("rationale") or ""),
            "prior_path_class": "",
            "prior_support": row.get("support"),
            "prior_eligible_rate": row.get("eligible_rate"),
            "prior_discovery_eligible_rate": row.get("discovery_eligible_rate"),
            "prior_validation_eligible_rate": row.get("validation_eligible_rate"),
            "prior_final_eligible_rate": row.get("final_eligible_rate"),
        })
    for row in residual_rows:
        merged.append({
            "feature": str(row["feature"]),
            "category": str(row["category"]),
            "catalog_origin": "residual_candidate_423",
            "blind_id": "",
            "semantic_label": "unreviewed",
            "semantic_confidence": "",
            "semantic_rationale": "",
            "prior_path_class": str(row["path_class"]),
            "prior_support": row.get("support"),
            "prior_eligible_rate": row.get("eligible_rate"),
            "prior_discovery_eligible_rate": row.get("discovery_eligible_rate"),
            "prior_validation_eligible_rate": row.get("validation_eligible_rate"),
            "prior_final_eligible_rate": row.get("final_eligible_rate"),
        })
    return merged


def expected_direction(row: Mapping[str, Any]) -> str | None:
    semantic_label = str(row.get("semantic_label") or "")
    if semantic_label == "likely_eligible":
        return "eligible"
    if semantic_label == "likely_ineligible":
        return "ineligible"
    path_class = str(row.get("prior_path_class") or "")
    if path_class == "stable_eligible":
        return "eligible"
    if path_class == "stable_ineligible":
        return "ineligible"
    return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _aggregate_catalog(
    article_features: Path,
    catalog_features: set[str],
) -> tuple[
    dict[str, dict[str, Counts]],
    dict[str, dict[str, Counts]],
    dict[str, Counts],
    Counter[str],
]:
    by_feature: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    by_month: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    split_totals: dict[str, Counts] = defaultdict(Counts)
    population: Counter[str] = Counter()
    source_ids: set[str] = set()
    for row in iter_jsonl(article_features):
        source_id = str(row["source_id"])
        if source_id in source_ids:
            raise ValueError(f"duplicate article feature source_id: {source_id}")
        source_ids.add(source_id)
        label = str(row["label"])
        if label not in {"eligible", "ineligible"}:
            raise ValueError(f"non-decisive article feature label: {label}")
        split = str(row["split"])
        if split not in SPLITS:
            raise ValueError(f"unexpected split: {split}")
        month = str(row["published_month"])
        population["articles"] += 1
        population[label] += 1
        split_totals[split].add(label)
        matches = catalog_features.intersection(feature_names(row))
        if matches:
            population["matched_articles"] += 1
            population[f"matched_{label}"] += 1
        else:
            population["unmatched_articles"] += 1
            population[f"unmatched_{label}"] += 1
        for feature in matches:
            by_feature[feature][split].add(label)
            by_month[feature][month].add(label)
    return by_feature, by_month, split_totals, population


def build_merged_stats(
    catalog: Sequence[Mapping[str, Any]],
    baseline_reports: Sequence[Mapping[str, Any]],
    updated_reports: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    baseline_by_feature = {str(row["feature"]): row for row in baseline_reports}
    report_by_feature = {str(row["feature"]): row for row in updated_reports}
    result: list[dict[str, Any]] = []
    for catalog_row in catalog:
        feature = str(catalog_row["feature"])
        baseline = baseline_by_feature.get(feature)
        updated = report_by_feature.get(feature)
        if baseline is None:
            raise ValueError(f"catalog feature absent from baseline full population: {feature}")
        if updated is None:
            raise ValueError(f"catalog feature absent from updated article population: {feature}")
        baseline_rate = _float_or_none(baseline.get("eligible_rate"))
        current_rate = _float_or_none(updated.get("eligible_rate"))
        baseline_class = candidate_class(baseline) or "insufficient_forward_support"
        updated_class = candidate_class(updated) or "insufficient_forward_support"
        expected = expected_direction(catalog_row)
        if expected == "eligible":
            exceptions = int(updated["ineligible"])
        elif expected == "ineligible":
            exceptions = int(updated["eligible"])
        else:
            exceptions = None
        result.append({
            **dict(catalog_row),
            "expected_direction": expected or "review_required",
            "baseline_full_path_class": baseline_class,
            "updated_path_class": updated_class,
            "support_delta": int(updated["support"]) - int(baseline["support"]),
            "eligible_rate_delta": (
                current_rate - baseline_rate
                if current_rate is not None and baseline_rate is not None else None
            ),
            "exception_articles": exceptions,
            **{f"baseline_full_{key}": value for key, value in baseline.items() if key not in {"feature", "category"}},
            **{f"updated_{key}": value for key, value in updated.items() if key not in {"feature", "category"}},
        })
    result.sort(key=lambda row: (
        0 if row["catalog_origin"] == "blind_semantic_709" else 1,
        -int(row["updated_support"]),
        str(row["feature"]),
    ))
    return result


def write_updated_exception_candidates(
    *,
    article_features: Path,
    merged_stats: Sequence[Mapping[str, Any]],
    output_path: Path,
) -> Counter[str]:
    stable_ineligible = {
        str(row["feature"]) for row in merged_stats if row["updated_path_class"] == "stable_ineligible"
    }
    stable_eligible = {
        str(row["feature"]) for row in merged_stats if row["updated_path_class"] == "stable_eligible"
    }
    counts: Counter[str] = Counter()
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(article_features):
            label = str(row["label"])
            features = set(feature_names(row))
            if label == "eligible":
                matches = sorted(features.intersection(stable_ineligible))
                reason = "eligible_under_updated_stable_ineligible_path"
            else:
                matches = sorted(features.intersection(stable_eligible))
                reason = "ineligible_under_updated_stable_eligible_path"
            if not matches:
                continue
            counts[reason] += 1
            handle.write(json.dumps({
                "source_id": str(row["source_id"]),
                "current_label": label,
                "split": str(row["split"]),
                "published_at_utc": str(row["published_at_text"]),
                "candidate_reason": reason,
                "matched_updated_paths": matches,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return counts


def _markdown(report: Mapping[str, Any]) -> str:
    population = report["population"]
    lines = [
        "# Merged Provider-Filter Path Analysis",
        "",
        "## Outcome",
        "",
        f"The fixed union contains {report['catalog']['merged_unique_paths']:,} exact paths: "
        f"{report['catalog']['semantic_paths']:,} blind semantic paths and "
        f"{report['catalog']['residual_candidate_paths']:,} later residual candidates.",
        "",
        f"Statistics were recomputed from {population['articles']:,} decisive 2025-August 2026 "
        "articles using the trading-ideas-corrected successor labels.",
        f"The same fixed catalog was also scored on {report['baseline_population']['articles']:,} "
        "baseline decisive articles, so deltas use identical full-population scope.",
        "",
        f"- Eligible: {population['eligible']:,}",
        f"- Ineligible: {population['ineligible']:,}",
        f"- Matched by at least one merged path: {population['matched_articles']:,}",
        f"- Unmatched by every merged path: {population['unmatched_articles']:,}",
        f"- Eligible exceptions under updated stable-ineligible paths: "
        f"{report['updated_exception_candidates'].get('eligible_under_updated_stable_ineligible_path', 0):,}",
        f"- Ineligible exceptions under updated stable-eligible paths: "
        f"{report['updated_exception_candidates'].get('ineligible_under_updated_stable_eligible_path', 0):,}",
        "",
        "## Updated path classes",
        "",
        *[
            f"- `{name}`: {count:,}"
            for name, count in sorted(report["updated_path_classes"].items())
        ],
        "",
        "## Interpretation boundary",
        "",
        "- The 709 semantic labels and the 423 statistical path classes remain distinct provenance.",
        "- Updated rates measure the current supervision labels; they do not make metadata a semantic authority.",
        "- Overlapping paths are retained as features, while article coverage is deduplicated.",
        "- Insufficient labels are excluded from binary rates and reported through the upstream authority counts.",
        "",
    ]
    return "\n".join(lines)


def run_merged_path_analysis(
    *,
    baseline_article_features: Path = DEFAULT_BASELINE_ARTICLE_FEATURES,
    article_features: Path = DEFAULT_ARTICLE_FEATURES,
    semantic_paths: Path = DEFAULT_SEMANTIC_PATHS,
    residual_paths: Path = DEFAULT_RESIDUAL_PATHS,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite analysis output: {output_root}")
    output_root.mkdir(parents=True)
    baseline_feature_verification = verify_article_features(baseline_article_features)
    article_feature_verification = verify_article_features(article_features)
    semantic_rows = _read_csv(semantic_paths)
    residual_rows = _read_csv(residual_paths)
    catalog = merge_path_catalogs(semantic_rows, residual_rows)
    catalog_features = {str(row["feature"]) for row in catalog}
    baseline_by_feature, baseline_by_month, baseline_split_totals, baseline_population = _aggregate_catalog(
        baseline_article_features, catalog_features
    )
    by_feature, by_month, split_totals, population = _aggregate_catalog(
        article_features, catalog_features
    )
    baseline_reports = feature_report_rows(
        baseline_by_feature, baseline_by_month, baseline_split_totals
    )
    updated_reports = feature_report_rows(by_feature, by_month, split_totals)
    merged_stats = build_merged_stats(catalog, baseline_reports, updated_reports)

    stats_path = output_root / "MERGED_PATH_STATS.csv"
    write_csv_new(stats_path, merged_stats)
    exception_path = output_root / "UPDATED_PATH_EXCEPTION_CANDIDATES.jsonl"
    exception_counts = write_updated_exception_candidates(
        article_features=article_features,
        merged_stats=merged_stats,
        output_path=exception_path,
    )
    full_population_transitions = Counter(
        f"{row['baseline_full_path_class']}->{row['updated_path_class']}"
        for row in merged_stats
    )
    semantic_updated_classes = Counter(
        f"{row['semantic_label']}->{row['updated_path_class']}"
        for row in merged_stats
        if row["catalog_origin"] == "blind_semantic_709"
    )
    report = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "catalog": {
            "semantic_paths": len(semantic_rows),
            "residual_candidate_paths": len(residual_rows),
            "overlap": 0,
            "merged_unique_paths": len(catalog),
        },
        "population": dict(population),
        "baseline_population": dict(baseline_population),
        "split_population": {
            split: {
                "articles": split_totals[split].total,
                "eligible": split_totals[split].eligible,
                "ineligible": split_totals[split].ineligible,
            }
            for split in SPLITS
        },
        "catalog_origins": dict(Counter(str(row["catalog_origin"]) for row in merged_stats)),
        "semantic_labels": dict(Counter(str(row["semantic_label"]) for row in merged_stats)),
        "prior_path_classes": dict(Counter(str(row["prior_path_class"]) for row in merged_stats if row["prior_path_class"])),
        "updated_path_classes": dict(Counter(str(row["updated_path_class"]) for row in merged_stats)),
        "full_population_path_class_transitions": dict(full_population_transitions),
        "semantic_label_to_updated_class": dict(semantic_updated_classes),
        "updated_exception_candidates": dict(exception_counts),
        "largest_absolute_rate_changes": sorted(
            merged_stats,
            key=lambda row: (-abs(float(row["eligible_rate_delta"] or 0.0)), str(row["feature"])),
        )[:50],
        "highest_support_paths": sorted(
            merged_stats,
            key=lambda row: (-int(row["updated_support"]), str(row["feature"])),
        )[:50],
        "inputs": {
            "baseline_article_features": str(baseline_article_features),
            "baseline_article_features_sha256": baseline_feature_verification["actual_sha256"],
            "baseline_article_feature_verification": baseline_feature_verification,
            "article_features": str(article_features),
            "article_features_sha256": article_feature_verification["actual_sha256"],
            "semantic_paths": str(semantic_paths),
            "semantic_paths_sha256": sha256_path(semantic_paths),
            "residual_paths": str(residual_paths),
            "residual_paths_sha256": sha256_path(residual_paths),
            "article_feature_verification": article_feature_verification,
        },
    }
    report_path = output_root / "REPORT.json"
    report_md_path = output_root / "REPORT.md"
    write_json_new(report_path, report)
    report_md_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    validation = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "passed",
        "checks": {
            "exact_semantic_path_count": len(semantic_rows) == EXPECTED_SEMANTIC_PATHS,
            "exact_residual_path_count": len(residual_rows) == EXPECTED_RESIDUAL_PATHS,
            "catalogs_disjoint": len(catalog_features) == len(catalog),
            "exact_merged_path_count": len(merged_stats) == EXPECTED_SEMANTIC_PATHS + EXPECTED_RESIDUAL_PATHS,
            "all_catalog_paths_observed": len(updated_reports) == len(catalog),
            "all_catalog_paths_observed_in_baseline": len(baseline_reports) == len(catalog),
            "baseline_article_feature_hash_matches_manifest": (
                baseline_feature_verification["actual_sha256"]
                == baseline_feature_verification["expected_sha256"]
            ),
            "article_feature_hash_matches_manifest": (
                article_feature_verification["actual_sha256"]
                == article_feature_verification["expected_sha256"]
            ),
            "population_reconciles": (
                population["matched_articles"] + population["unmatched_articles"] == population["articles"]
            ),
            "labels_reconcile": population["eligible"] + population["ineligible"] == population["articles"],
        },
    }
    validation_path = output_root / "VALIDATION.json"
    write_json_new(validation_path, validation)
    output_paths = (stats_path, exception_path, report_path, report_md_path, validation_path)
    write_json_new(output_root / "HASH_MANIFEST.json", {
        "analysis_version": ANALYSIS_VERSION,
        "inputs": report["inputs"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in output_paths
        },
    })
    return report
