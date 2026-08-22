from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .provider_context import ROUTER_VERSION, classify_provider_context
from .provider_filter_analysis import (
    DEFAULT_METADATA_ROOT,
    _expected_input_paths,
    attach_ticker_history,
    canonical_json,
    extract_title,
    iter_jsonl,
    load_analysis_rows,
    load_current_labels,
    sha256_path,
    verify_inputs,
    wilson_interval,
)


EVALUATION_VERSION = "news_synthesis_provider_context_evaluation_v5"
DEFAULT_CORRECTED_AUTHORITY_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_provider_path_exceptions_v2"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_context_router_evaluation_v5_final"
)
DEFAULT_MARKET_CAP_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_market_cap_context_analysis_v3\ARTICLE_MARKET_CAP_FEATURES.jsonl"
)


def _metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    context_labels: Counter[str] = Counter()
    total = 0
    for row in rows:
        total += 1
        label = str(row["label"])
        route = str(row["provider_route"])
        counts[label] += 1
        route_counts[route] += 1
        if route == "context_only":
            context_labels[label] += 1
    context_total = sum(context_labels.values())
    false_rejections = context_labels["eligible"]
    eligible_total = counts["eligible"]
    precision_low, precision_high = wilson_interval(context_labels["ineligible"], context_total)
    return {
        "articles": total,
        "eligible": counts["eligible"],
        "ineligible": counts["ineligible"],
        "routes": dict(sorted(route_counts.items())),
        "context_only": context_total,
        "context_only_ineligible": context_labels["ineligible"],
        "context_only_eligible_false_rejections": false_rejections,
        "context_only_noise_precision": context_labels["ineligible"] / context_total if context_total else None,
        "context_only_noise_precision_ci95": [precision_low, precision_high] if context_total else None,
        "estimated_expensive_forecast_compute_reduction": context_total / total if total else 0.0,
        "retained_eligible_recall": 1 - false_rejections / eligible_total if eligible_total else 1.0,
        "semantic_rescue_share": route_counts["semantic_rescue_required"] / total if total else 0.0,
    }


def _flat_group_rows(rows: Sequence[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[field])].append(row)
    result = []
    for name, group in sorted(grouped.items()):
        result.append({field: name, **_metrics(group)})
    return result


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: canonical_json(value) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })


def _markdown(report: Mapping[str, Any]) -> str:
    overall = report["overall"]
    split_lines = []
    for row in report["split_metrics"]:
        split_lines.append(
            f"| {row['split']} | {row['articles']:,} | {row['context_only']:,} | "
            f"{100*row['estimated_expensive_forecast_compute_reduction']:.2f}% | "
            f"{row['context_only_eligible_false_rejections']:,} | {100*row['retained_eligible_recall']:.4f}% |"
        )
    family_lines = []
    for row in sorted(report["family_metrics"], key=lambda value: -int(value["articles"])):
        family_lines.append(
            f"| `{row['content_family']}` | {row['articles']:,} | {row['context_only']:,} | "
            f"{row['context_only_ineligible']:,} | {row['context_only_eligible_false_rejections']:,} |"
        )
    return "\n".join((
        "# News Synthesis Provider-Context Router Evaluation",
        "",
        f"Router: `{report['router_version']}`  ",
        f"Evaluation: `{report['evaluation_version']}`",
        "",
        "## Result",
        "",
        f"The conservative `context_only` gate routes {overall['context_only']:,} of "
        f"{overall['articles']:,} decisive articles away from expensive issuer-forecast synthesis "
        f"({100*overall['estimated_expensive_forecast_compute_reduction']:.2f}% estimated reduction). "
        f"It conflicts with {overall['context_only_eligible_false_rejections']:,} corrected eligible labels, "
        f"retaining {100*overall['retained_eligible_recall']:.4f}% eligible recall.",
        "",
        "| Split | Articles | Context only | Compute reduction | Eligible conflicts | Eligible recall |",
        "|---|---:|---:|---:|---:|---:|",
        *split_lines,
        "",
        "## Routed families",
        "",
        "| Family | Articles | Context only | Correct ineligible | Eligible conflicts |",
        "|---|---:|---:|---:|---:|",
        *family_lines,
        "",
        "## Interpretation boundary",
        "",
        "- `context_only` means excluded from issuer-forecast synthesis, not deleted; these rows remain inputs to the future market-context lane.",
        "- Mixed templates and text-only roundup detection require semantic rescue and are not rejected by metadata.",
        "- Generic material-language regexes cannot override an exact validated context family; mixed provider families always require semantic rescue.",
        "- Temporal novelty is calculated causally and stored in the decision trace, but is trace-only in v4 because it is not sufficiently precise alone.",
        "- This is an in-period development evaluation on corrected 2025-August 2026 authority, not a fresh post-August holdout or available-time live certification.",
        "",
    ))


def run_evaluation(
    *,
    authority_root: Path = DEFAULT_CORRECTED_AUTHORITY_ROOT,
    metadata_root: Path = DEFAULT_METADATA_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    market_cap_features: Path = DEFAULT_MARKET_CAP_FEATURES,
) -> dict[str, Any]:
    paths = _expected_input_paths(authority_root, metadata_root)
    verification = verify_inputs(paths)
    labels, authority_counts = load_current_labels(paths.labels)
    rows, load_summary = load_analysis_rows(paths.metadata, labels)
    history_summary = attach_ticker_history(rows)
    by_id = {str(row["source_id"]): row for row in rows}
    market_cap_seen: set[str] = set()
    for cap_row in iter_jsonl(market_cap_features):
        source_id = str(cap_row["source_id"])
        row = by_id.get(source_id)
        if row is None:
            continue
        if str(cap_row["label"]) != str(row["label"]):
            raise ValueError(f"market-cap/current-label mismatch: {source_id}")
        row["market_cap_tickers"] = list(cap_row.get("market_cap_tickers") or ())
        market_cap_seen.add(source_id)
    if market_cap_seen != set(by_id):
        raise ValueError(
            f"market-cap feature membership mismatch: {len(market_cap_seen)} != {len(by_id)}"
        )

    rendered_seen = 0
    routed = 0
    for rendered in iter_jsonl(paths.rendered_texts):
        rendered_seen += 1
        source_id = str(rendered["source_id"])
        row = by_id.get(source_id)
        if row is None:
            continue
        text = str(rendered.get("rendered_text") or "")
        expected_hash = str(rendered.get("rendered_text_hash") or "")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash:
            raise ValueError(f"rendered text hash mismatch: {source_id}")
        decision = classify_provider_context({
            **row,
            "title": extract_title(text),
            "rendered_text": text,
        })
        row["provider_route"] = decision["route"]
        row["content_family"] = decision["content_family"]
        row["provider_reason_codes"] = decision["reason_codes"]
        row["material_language_detected"] = decision["material_language_detected"]
        routed += 1
    if routed != len(rows):
        raise ValueError(f"rendered-text membership mismatch: routed={routed} expected={len(rows)}")

    overall = _metrics(rows)
    split_metrics = _flat_group_rows(rows, "split")
    month_metrics = _flat_group_rows(rows, "published_month")
    family_metrics = _flat_group_rows(rows, "content_family")
    exceptions = [
        {
            "source_id": row["source_id"],
            "split": row["split"],
            "published_at_utc": row["published_at_text"],
            "content_family": row["content_family"],
            "provider_tags": row["provider_tags"],
            "channels": row["channels"],
            "reason_codes": row["provider_reason_codes"],
        }
        for row in rows
        if row["provider_route"] == "context_only" and row["label"] == "eligible"
    ]
    report = {
        "evaluation_version": EVALUATION_VERSION,
        "router_version": ROUTER_VERSION,
        "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "corrected decisive 2025 through August 2026 forecast-eligibility authority",
        "inputs": {
            "labels": str(paths.labels),
            "metadata": str(paths.metadata),
            "rendered_texts": str(paths.rendered_texts),
            "verification": verification,
            "authority_label_counts": dict(authority_counts),
            "market_cap_features": str(market_cap_features),
            "market_cap_features_sha256": sha256_path(market_cap_features),
        },
        "load_summary": load_summary,
        "causal_ticker_history": history_summary,
        "rendered_authority_rows": rendered_seen,
        "overall": overall,
        "split_metrics": split_metrics,
        "month_metrics": month_metrics,
        "family_metrics": family_metrics,
        "eligible_exception_rows": len(exceptions),
        "limitations": [
            "In-period development evaluation; no fresh post-August 2026 holdout.",
            "Publication time is used for causal history because true available_at is absent from this authority.",
            "Compute reduction is routed-row share, not measured wall-clock or CPU savings.",
            "Context-only sources require durable preservation in the separate context lane before live skip activation.",
        ],
    }

    output_root.mkdir(parents=True, exist_ok=False)
    report_path = output_root / "REPORT.json"
    report_md_path = output_root / "REPORT.md"
    split_path = output_root / "SPLIT_METRICS.csv"
    month_path = output_root / "MONTH_METRICS.csv"
    family_path = output_root / "FAMILY_METRICS.csv"
    exception_path = output_root / "CONTEXT_ONLY_ELIGIBLE_EXCEPTIONS.jsonl"
    validation_path = output_root / "VALIDATION.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_md_path.write_text(_markdown(report), encoding="utf-8")
    _write_csv(split_path, split_metrics)
    _write_csv(month_path, month_metrics)
    _write_csv(family_path, family_metrics)
    with exception_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in exceptions:
            handle.write(canonical_json(row) + "\n")
    validation = {
        "status": "passed",
        "router_version": ROUTER_VERSION,
        "article_rows": len(rows),
        "unique_source_ids": len(by_id),
        "all_rows_routed": routed == len(rows),
        "route_partition_complete": sum(overall["routes"].values()) == len(rows),
        "eligible_exception_count_matches": len(exceptions) == overall["context_only_eligible_false_rejections"],
        "exact_splits_present": {row["split"] for row in split_metrics} == {
            "discovery_2025", "validation_2026_jan_apr", "final_2026_may_aug"
        },
    }
    if not all(value for key, value in validation.items() if isinstance(value, bool)):
        raise ValueError(f"provider-context evaluation validation failed: {validation}")
    validation_path.write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    outputs = (report_path, report_md_path, split_path, month_path, family_path, exception_path, validation_path)
    hashes = {
        "evaluation_version": EVALUATION_VERSION,
        "router_version": ROUTER_VERSION,
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            for path in outputs
        },
    }
    (output_root / "HASH_MANIFEST.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
