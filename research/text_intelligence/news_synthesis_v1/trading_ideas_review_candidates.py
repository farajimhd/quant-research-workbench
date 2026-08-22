from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .provider_filter_analysis import (
    SPLITS,
    Counts,
    canonical_json,
    feature_report_rows,
    iter_jsonl,
    sha256_path,
    write_csv_new,
    write_json_new,
)


ANALYSIS_VERSION = "news_synthesis_trading_ideas_review_candidates_v2"
DEFAULT_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v3_corrected\ARTICLE_FEATURES.jsonl"
)
DEFAULT_MISMATCH_CONTROLLER = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_v1\mismatch_audit_controller.jsonl"
)
DEFAULT_PRIOR_REVIEW = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_contradiction_review_v1\CONTROLLER_POPULATION.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\trading_ideas_review_candidates_v2"
)
EXPECTED_TRADING_IDEAS = 42_567
EXPECTED_ELIGIBLE = 6_999
EXPECTED_PREVIOUSLY_REVIEWED_ELIGIBLE = 103
EVENT_CHANNELS = frozenset({
    "buybacks", "clinical trials", "contracts", "dividends", "earnings",
    "earnings beats", "earnings misses", "fda", "guidance", "ipos", "m&a",
    "management", "offerings",
})
NOISE_CHANNELS = frozenset({
    "analyst ratings", "long ideas", "markets", "movers", "opinion",
    "previews", "price target", "short ideas", "short sellers", "technicals",
})
NOISE_TAGS = frozenset({
    "earnings conference call transcripts", "expert ideas", "why it's moving",
})
NOISE_FLAGS = (
    "analyst_rating", "earnings_preview", "list_or_screener", "market_recap",
    "price_target", "short_interest", "technical_or_valuation", "why_moving",
)
PRIORITY_ORDER = {
    "p0_model_disagreement_no_event": 0,
    "p1_model_disagreement_event_overlap": 1,
    "p1_no_event_explicit_noise": 2,
    "p2_no_event_other": 3,
    "p2_event_overlap_explicit_noise": 4,
    "p3_event_overlap_only": 5,
}


def normalized_values(row: Mapping[str, Any], field: str) -> set[str]:
    return {str(value).strip().casefold() for value in row.get(field) or () if str(value).strip()}


def is_trading_idea(row: Mapping[str, Any]) -> bool:
    return "trading ideas" in normalized_values(row, "channels") or "trading ideas" in normalized_values(row, "provider_tags")


def event_evidence(row: Mapping[str, Any]) -> tuple[str, ...]:
    evidence = {f"channel:{value}" for value in normalized_values(row, "channels").intersection(EVENT_CHANNELS)}
    if bool(row.get("material_event")):
        evidence.add("text:material_event")
    return tuple(sorted(evidence))


def noise_evidence(row: Mapping[str, Any]) -> tuple[str, ...]:
    evidence = {f"channel:{value}" for value in normalized_values(row, "channels").intersection(NOISE_CHANNELS)}
    evidence.update(f"tag:{value}" for value in normalized_values(row, "provider_tags").intersection(NOISE_TAGS))
    evidence.update(f"text:{name}" for name in NOISE_FLAGS if bool(row.get(name)))
    return tuple(sorted(evidence))


def review_priority(*, model_disagreement: bool, has_event: bool, has_noise: bool) -> str:
    if model_disagreement and not has_event:
        return "p0_model_disagreement_no_event"
    if model_disagreement:
        return "p1_model_disagreement_event_overlap"
    if not has_event and has_noise:
        return "p1_no_event_explicit_noise"
    if not has_event:
        return "p2_no_event_other"
    if has_noise:
        return "p2_event_overlap_explicit_noise"
    return "p3_event_overlap_only"


def review_id(source_id: str) -> str:
    digest = hashlib.sha256(f"{ANALYSIS_VERSION}|{source_id}".encode()).hexdigest()
    return f"TI{digest[:20]}"


def strict_low_rate_channel_sets(family_reports: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, Mapping[str, Any]] = {}
    for row in family_reports:
        feature = str(row["feature"])
        if not feature.startswith("channel_set=") or int(row["support"]) < 300:
            continue
        supports = [int(row.get(f"{prefix}_support") or 0) for prefix in ("discovery", "validation", "final")]
        rates = [row.get(f"{prefix}_eligible_rate") for prefix in ("discovery", "validation", "final")]
        if min(supports) < 30 or any(rate is None for rate in rates):
            continue
        if max(float(rate) for rate in rates) <= 0.05:
            selected[feature] = row
    return selected


def _family_features(row: Mapping[str, Any], event: tuple[str, ...], noise: tuple[str, ...]) -> tuple[str, ...]:
    channels = sorted(normalized_values(row, "channels"))
    tags = sorted(normalized_values(row, "provider_tags"))
    result = {"channel_set=" + "|".join(channels), f"evidence_profile=event:{bool(event)}|noise:{bool(noise)}"}
    if tags:
        result.add("tag_set=" + "|".join(tags))
        result.update(f"tag={tag}" for tag in tags)
    return tuple(sorted(result))


def _markdown(report: Mapping[str, Any]) -> str:
    population = report["population"]
    priorities = report["review_candidates_by_priority"]
    return "\n".join((
        "# Trading-Ideas Eligibility Review Candidates V2",
        "",
        "## Outcome",
        "",
        f"The corrected 2025-August 2026 authority contains {population['trading_ideas_articles']:,} trading-idea articles. "
        f"Of {population['eligible']:,} currently eligible articles, {population['previously_reviewed_eligible']:,} already received correction-grade blind review and {population['unreviewed_eligible_candidates']:,} remain designated for review.",
        "",
        "## Controller priority strata",
        "",
        *[f"- `{name}`: {count:,}" for name, count in sorted(priorities.items(), key=lambda item: PRIORITY_ORDER[item[0]])],
        "",
        f"The strict first tranche contains {report['strict_low_rate_path_exceptions']:,} eligible exceptions from "
        f"{report['strict_low_rate_channel_sets']:,} exact channel sets that remain at or below 5% eligible in every temporal split.",
        "",
        "The review hypothesis is that an investment idea, recommendation, valuation, technical setup, or price-movement narrative is forecast-ineligible unless the supplied article independently reports a new or current material issuer event. Metadata and model signals select and prioritize candidates but are never exposed to semantic reviewers.",
        "",
        "## Blindness boundary",
        "",
        "Worker packets must contain only opaque review ID, publication time, complete rendered source text, and verified source hash. They must exclude current label, channels, tags, event/noise flags, model output, priority, provenance, and prior-review state.",
        "",
    ))


def run_trading_ideas_analysis(
    *,
    article_features: Path = DEFAULT_ARTICLE_FEATURES,
    mismatch_controller: Path = DEFAULT_MISMATCH_CONTROLLER,
    prior_review: Path = DEFAULT_PRIOR_REVIEW,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite output: {output_root}")
    output_root.mkdir(parents=True)
    mismatches = {str(row["source_id"]): row for row in iter_jsonl(mismatch_controller)}
    reviewed_ids = {str(row["source_id"]) for row in iter_jsonl(prior_review)}
    if len(mismatches) != 35_995:
        raise ValueError("expected 35,995 unique mismatch-controller rows")
    if len(reviewed_ids) != 2_767:
        raise ValueError("expected 2,767 unique prior contradiction-review rows")

    population: Counter[str] = Counter()
    priority_counts: Counter[str] = Counter()
    split_counts: Counter[tuple[str, str]] = Counter()
    by_family: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    by_family_month: dict[str, dict[str, Counts]] = defaultdict(lambda: defaultdict(Counts))
    split_totals: dict[str, Counts] = defaultdict(Counts)
    candidates_path = output_root / "CONTROLLER_REVIEW_CANDIDATES.jsonl"
    reviewed_path = output_root / "PREVIOUSLY_REVIEWED_ELIGIBLE.jsonl"
    with (
        candidates_path.open("x", encoding="utf-8", newline="\n") as candidate_handle,
        reviewed_path.open("x", encoding="utf-8", newline="\n") as reviewed_handle,
    ):
        for row in iter_jsonl(article_features):
            if not is_trading_idea(row):
                continue
            population["trading_ideas_articles"] += 1
            label = str(row["label"])
            split = str(row["split"])
            month = str(row["published_month"])
            population[label] += 1
            split_counts[(split, label)] += 1
            split_totals[split].add(label)
            event = event_evidence(row)
            noise = noise_evidence(row)
            for feature in _family_features(row, event, noise):
                by_family[feature][split].add(label)
                by_family_month[feature][month].add(label)
            if label != "eligible":
                continue
            source_id = str(row["source_id"])
            common = {
                "review_id": review_id(source_id),
                "source_id": source_id,
                "published_at_utc": str(row["published_at_text"]),
                "rendered_text_sha256": str(row["rendered_text_hash"]),
                "current_label": label,
                "split": split,
                "channels": sorted(normalized_values(row, "channels")),
                "provider_tags": sorted(normalized_values(row, "provider_tags")),
                "ticker_count": int(row["ticker_count"]),
                "event_evidence": event,
                "noise_evidence": noise,
                "authority_class": str(row.get("authority_class") or ""),
                "certification_level": str(row.get("certification_level") or ""),
                "source_dataset": str(row.get("source_dataset") or ""),
            }
            if source_id in reviewed_ids:
                population["previously_reviewed_eligible"] += 1
                reviewed_handle.write(canonical_json({**common, "review_status": "correction_grade_complete"}) + "\n")
                continue
            mismatch = mismatches.get(source_id)
            model_disagreement = bool(mismatch and mismatch.get("combined_model_prediction") == "ineligible")
            priority = review_priority(
                model_disagreement=model_disagreement,
                has_event=bool(event),
                has_noise=bool(noise),
            )
            priority_counts[priority] += 1
            population["unreviewed_eligible_candidates"] += 1
            candidate_handle.write(canonical_json({
                **common,
                "priority": priority,
                "combined_model_disagreement": model_disagreement,
                "combined_model_probability": float(mismatch["eligible_probability"]) if model_disagreement else None,
                "combined_model_audit_state": str(mismatch["blind_review_state"]) if model_disagreement else None,
            }) + "\n")

    if population["trading_ideas_articles"] != EXPECTED_TRADING_IDEAS:
        raise ValueError("trading-idea population changed")
    if population["eligible"] != EXPECTED_ELIGIBLE:
        raise ValueError("eligible trading-idea population changed")
    if population["previously_reviewed_eligible"] != EXPECTED_PREVIOUSLY_REVIEWED_ELIGIBLE:
        raise ValueError("prior reviewed eligible population changed")
    family_reports = feature_report_rows(by_family, by_family_month, split_totals)
    family_path = output_root / "FAMILY_STATS.csv"
    write_csv_new(family_path, family_reports)
    strict_sets = strict_low_rate_channel_sets(family_reports)
    strict_path = output_root / "STRICT_LOW_RATE_PATH_EXCEPTIONS.jsonl"
    strict_count = 0
    with strict_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(candidates_path):
            feature = "channel_set=" + "|".join(row["channels"])
            statistics = strict_sets.get(feature)
            if statistics is None:
                continue
            strict_count += 1
            handle.write(canonical_json({
                **row,
                "strict_path": feature,
                "strict_path_support": int(statistics["support"]),
                "strict_path_eligible_rate": float(statistics["eligible_rate"]),
                "strict_path_split_eligible_rates": {
                    prefix: float(statistics[f"{prefix}_eligible_rate"])
                    for prefix in ("discovery", "validation", "final")
                },
            }) + "\n")
    report = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "policy_hypothesis": (
            "Trading ideas are ineligible when they are recommendations, valuation/technical setups, or price narratives; "
            "co-tagged articles remain reviewable when complete text independently reports a new material issuer event."
        ),
        "population": dict(population),
        "split_label_counts": {
            f"{split}|{label}": count for (split, label), count in sorted(split_counts.items())
        },
        "review_candidates_by_priority": dict(priority_counts),
        "family_feature_rows": len(family_reports),
        "strict_low_rate_channel_sets": len(strict_sets),
        "strict_low_rate_path_exceptions": strict_count,
        "inputs": {
            "article_features": str(article_features),
            "article_features_sha256": sha256_path(article_features),
            "mismatch_controller": str(mismatch_controller),
            "mismatch_controller_sha256": sha256_path(mismatch_controller),
            "prior_review": str(prior_review),
            "prior_review_sha256": sha256_path(prior_review),
        },
    }
    report_path = output_root / "REPORT.json"
    report_md_path = output_root / "REPORT.md"
    write_json_new(report_path, report)
    report_md_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    validation = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "valid",
        "checks": {
            "exact_population": population["trading_ideas_articles"] == EXPECTED_TRADING_IDEAS,
            "exact_eligible": population["eligible"] == EXPECTED_ELIGIBLE,
            "eligible_reconciles": (
                population["previously_reviewed_eligible"] + population["unreviewed_eligible_candidates"]
                == population["eligible"]
            ),
            "priority_reconciles": sum(priority_counts.values()) == population["unreviewed_eligible_candidates"],
            "strict_exceptions_are_subset": strict_count <= population["unreviewed_eligible_candidates"],
            "unique_candidate_review_ids": len({
                str(row["review_id"]) for row in iter_jsonl(candidates_path)
            }) == population["unreviewed_eligible_candidates"],
        },
    }
    validation_path = output_root / "VALIDATION.json"
    write_json_new(validation_path, validation)
    output_paths = (
        candidates_path, reviewed_path, family_path, strict_path, report_path, report_md_path, validation_path,
    )
    write_json_new(output_root / "HASH_MANIFEST.json", {
        "analysis_version": ANALYSIS_VERSION,
        "inputs": report["inputs"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in output_paths
        },
    })
    return report
