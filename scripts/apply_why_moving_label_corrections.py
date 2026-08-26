from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "services" / "text-intelligence"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.text_intelligence.news_synthesis_v1.engine import (  # noqa: E402
    ENGINE_VERSION,
    _envelope,
    why_moving_title_pattern,
)
from research.text_intelligence.news_synthesis_v1.provider_filter_analysis import (  # noqa: E402
    canonical_json,
    iter_jsonl,
)
from text_intelligence.config import IntelligenceConfig  # noqa: E402


RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence")
NEWS_ROOT = RUNTIME_ROOT / "news_synthesis_v1"
PARENT_TRAINING = (
    RUNTIME_ROOT
    / "llm_issuer_labeling_v4"
    / "forecast_eligibility_sentiment_authority_why_moving_human_review_v1"
)
PARENT_HOLDOUT = (
    NEWS_ROOT
    / "forecast_eligibility_august_2026_temporal_holdout_why_moving_human_review_v1"
)
FEATURES = (
    NEWS_ROOT
    / "provider_filter_feature_audit_v6_provider_path_exceptions_final"
    / "ARTICLE_FEATURES.jsonl"
)
AUDIT_MARKDOWN = (
    NEWS_ROOT
    / "why_moving_eligible_audit_v1"
    / "WHY_MOVING_ELIGIBLE_AUDIT.md"
)
DEFAULT_TRAINING_OUTPUT = (
    RUNTIME_ROOT
    / "llm_issuer_labeling_v4"
    / "forecast_eligibility_sentiment_authority_why_moving_human_review_v2"
)
DEFAULT_HOLDOUT_OUTPUT = (
    NEWS_ROOT
    / "forecast_eligibility_august_2026_temporal_holdout_why_moving_human_review_v2"
)
REVIEW_AUTHORITY = "operator_manual_title_review_2026_08_26_v2"
CORRECTION_REASON = "why_moving_or_price_reaction_followup"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
            count += 1
    return count


def _audit_rows() -> list[dict[str, str]]:
    text = AUDIT_MARKDOWN.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    pattern = re.compile(
        r"^\| (?P<index>\d+) \| (?P<partition>[^|]+) \| "
        r"(?P<title>.*?) \| source_id=(?P<source_id>[0-9a-f]+)<br>",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        title = match.group("title").replace(r"\|", "|").strip()
        family = why_moving_title_pattern(title)
        if family is None:
            raise ValueError(f"reviewed title is not covered by a title family: {title}")
        rows.append({
            "audit_index": match.group("index"),
            "partition": match.group("partition").strip(),
            "source_id": match.group("source_id"),
            "title": title,
            "title_pattern": family,
        })
    if len(rows) != 2_572 or len({row["source_id"] for row in rows}) != len(rows):
        raise ValueError(f"unexpected reviewed audit population: {len(rows)}")
    return rows


def _titles_in_scope(client: ClickHouseHttpClient) -> dict[str, str]:
    """Load the bounded title population; the engine owns pattern semantics.

    Do not maintain a second ClickHouse/RE2 approximation of
    ``why_moving_title_pattern``.  The previous prefilter omitted inflections
    such as ``rising`` and allowed gold labels to drift from the v55 gate.
    """
    result: dict[str, str] = {}
    for row in client.iter_json_each_row("""
SELECT canonical_news_id,title
FROM q_live.benzinga_news_event_v2 FINAL
PREWHERE published_date BETWEEN toDate('2025-01-01') AND toDate('2026-08-31')
FORMAT JSONEachRow
"""):
        result[str(row["canonical_news_id"])] = str(row.get("title") or "").strip()
    return result


def _manifest(root: Path, version: str) -> None:
    files = {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    }
    _write_json(root / "HASH_MANIFEST.json", {"authority_version": version, "files": files})


def _correct_training(
    output: Path,
    reviewed: dict[str, dict[str, str]],
) -> tuple[dict[str, str], dict[str, int]]:
    output.mkdir(parents=True)
    parent_labels = PARENT_TRAINING / "article_forecast_eligibility_labels.jsonl"
    corrected_path = output / "article_forecast_eligibility_labels.jsonl"
    labels: dict[str, str] = {}
    counts: Counter[str] = Counter()
    changed = 0
    with corrected_path.open("x", encoding="utf-8", newline="\n") as handle:
        for original in iter_jsonl(parent_labels):
            row = dict(original)
            source_id = str(row["source_id"])
            if source_id in reviewed:
                audit = reviewed[source_id]
                old_label = str(row["forecast_eligibility_label"])
                if old_label != audit["old_label"] or old_label == "ineligible":
                    raise ValueError(f"unexpected reviewed training label: {source_id}={old_label}")
                direct_review = audit["adjudication_basis"] == "direct_manual_review"
                row.update({
                    "forecast_eligibility_label": "ineligible",
                    "forecast_eligible": False,
                    "authority_class": (
                        "operator_manual_title_review"
                        if direct_review
                        else "operator_approved_title_pattern_policy"
                    ),
                    "authority_detail": REVIEW_AUTHORITY,
                    "certification_level": (
                        "human_title_adjudicated"
                        if direct_review
                        else "human_pattern_policy_adjudicated"
                    ),
                    "human_certified": True,
                    "source_dataset": "why_moving_human_review_v2",
                    "usage_policy": "model_development_human_policy_adjudicated",
                    "manual_review_scope": audit["review_scope"],
                    "manual_review_reason": CORRECTION_REASON,
                    "manual_review_title_pattern": audit["title_pattern"],
                    "superseded_forecast_eligibility_label": old_label,
                })
                changed += 1
            label = str(row["forecast_eligibility_label"])
            labels[source_id] = label
            counts[label] += 1
            handle.write(canonical_json(row) + "\n")
    if changed != len(reviewed):
        raise ValueError(f"training corrections did not reconcile: {changed} != {len(reviewed)}")
    shutil.copyfile(
        PARENT_TRAINING / "gold_issuer_sentiment_labels.jsonl",
        output / "gold_issuer_sentiment_labels.jsonl",
    )
    return labels, dict(counts)


def _correct_holdout(
    output: Path,
    reviewed: dict[str, dict[str, str]],
) -> tuple[dict[str, str], dict[str, int]]:
    output.mkdir(parents=True)
    shutil.copyfile(PARENT_HOLDOUT / "SOURCE_ROWS.jsonl", output / "SOURCE_ROWS.jsonl")
    labels: dict[str, str] = {}
    counts: Counter[str] = Counter()
    changed = 0
    target = output / "FINAL_LABELS_V2.jsonl"
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        for original in iter_jsonl(PARENT_HOLDOUT / "FINAL_LABELS_V2.jsonl"):
            row = dict(original)
            source_id = str(row["source_id"])
            if source_id in reviewed:
                audit = reviewed[source_id]
                old_label = str(row["final_label"])
                if old_label != audit["old_label"] or old_label == "ineligible":
                    raise ValueError(f"unexpected reviewed holdout label: {source_id}={old_label}")
                row.update({
                    "final_label": "ineligible",
                    "decision_path": "operator_manual_title_review_correction",
                    "superseded_final_label": old_label,
                    "manual_correction": {
                        "authority": REVIEW_AUTHORITY,
                        "adjudication_basis": audit["adjudication_basis"],
                        "review_scope": audit["review_scope"],
                        "reason": CORRECTION_REASON,
                        "title_pattern": audit["title_pattern"],
                    },
                })
                changed += 1
            label = str(row["final_label"])
            labels[source_id] = label
            counts[label] += 1
            handle.write(canonical_json(row) + "\n")
    if changed != len(reviewed):
        raise ValueError(f"holdout corrections did not reconcile: {changed} != {len(reviewed)}")
    return labels, dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish reviewed why-moving label corrections without mutating parent authorities"
    )
    parser.add_argument("--training-output", type=Path, default=DEFAULT_TRAINING_OUTPUT)
    parser.add_argument("--holdout-output", type=Path, default=DEFAULT_HOLDOUT_OUTPUT)
    parser.add_argument("--json", action="store_true", help="emit the final result as JSON")
    args = parser.parse_args()
    if args.training_output.exists() or args.holdout_output.exists():
        raise FileExistsError("a correction output root already exists")

    audit_rows = _audit_rows()
    training_review = {
        row["source_id"]: row
        for row in audit_rows
        if row["partition"] == "training_2025_2026"
    }
    holdout_review = {
        row["source_id"]: row
        for row in audit_rows
        if row["partition"] == "holdout_august_2026"
    }
    if len(training_review) != 2_557 or len(holdout_review) != 15:
        raise ValueError("reviewed partition counts changed")
    for row in audit_rows:
        row["adjudication_basis"] = "direct_manual_review"
        row["review_scope"] = "title"
        row["old_label"] = "eligible"

    parent_training_labels = {
        str(row["source_id"]): str(row["forecast_eligibility_label"])
        for row in iter_jsonl(PARENT_TRAINING / "article_forecast_eligibility_labels.jsonl")
    }
    parent_holdout_labels = {
        str(row["source_id"]): str(row["final_label"])
        for row in iter_jsonl(PARENT_HOLDOUT / "FINAL_LABELS_V2.jsonl")
    }
    why_training = {
        str(row["source_id"]): row
        for row in iter_jsonl(FEATURES)
        if bool(row.get("why_moving"))
    }
    holdout_sources = {
        str(row["source_id"]): row
        for row in iter_jsonl(PARENT_HOLDOUT / "SOURCE_ROWS.jsonl")
    }
    IntelligenceConfig.from_env()
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=60,
    )
    try:
        titles_in_scope = _titles_in_scope(client)
    finally:
        client.close()

    training_pattern_rows = {
        source_id: {
            "audit_index": "",
            "partition": "training_pattern_extension",
            "source_id": source_id,
            "title": title,
            "title_pattern": family,
            "adjudication_basis": "operator_approved_pattern_extension",
            "review_scope": "approved_title_pattern",
            "old_label": parent_training_labels[source_id],
        }
        for source_id, title in titles_in_scope.items()
        if source_id in parent_training_labels
        and (family := why_moving_title_pattern(title)) is not None
    }
    holdout_pattern_rows = {
        source_id: {
            "audit_index": "",
            "partition": "holdout_pattern_extension",
            "source_id": source_id,
            "title": str(row.get("title") or ""),
            "title_pattern": family,
            "adjudication_basis": "operator_approved_pattern_extension",
            "review_scope": "approved_title_pattern",
            "old_label": parent_holdout_labels[source_id],
        }
        for source_id, row in holdout_sources.items()
        if (family := why_moving_title_pattern(str(row.get("title") or ""))) is not None
    }
    if not set(training_review).issubset(training_pattern_rows):
        raise ValueError("one or more directly reviewed training titles are absent from the full pattern scan")
    if not set(holdout_review).issubset(holdout_pattern_rows):
        raise ValueError("one or more directly reviewed holdout titles are absent from the full pattern scan")

    if any(parent_training_labels[source_id] != "ineligible" for source_id in training_review):
        raise ValueError("one or more directly reviewed training labels regressed in the parent")
    if any(parent_holdout_labels[source_id] != "ineligible" for source_id in holdout_review):
        raise ValueError("one or more directly reviewed holdout labels regressed in the parent")

    training_corrections = {
        source_id: row
        for source_id, row in training_pattern_rows.items()
        if parent_training_labels[source_id] != "ineligible"
    }
    holdout_corrections = {
        source_id: row
        for source_id, row in holdout_pattern_rows.items()
        if parent_holdout_labels[source_id] != "ineligible"
    }

    training_labels, training_counts = _correct_training(
        args.training_output, training_corrections,
    )
    holdout_labels, holdout_counts = _correct_holdout(
        args.holdout_output, holdout_corrections,
    )
    training_pattern_ids = set(training_pattern_rows)
    holdout_pattern_ids = set(holdout_pattern_rows)
    training_purposes = Counter(
        str(_envelope(row["title"], row["title"], {})["communication_purpose"]["value"])
        for row in training_pattern_rows.values()
    )
    holdout_purposes = Counter(
        str(_envelope(row["title"], row["title"], {})["communication_purpose"]["value"])
        for row in holdout_pattern_rows.values()
    )
    if training_purposes.get("report", 0) or holdout_purposes.get("report", 0):
        raise ValueError(
            "one or more title-pattern matches remain News Synthesis forecast-eligible"
        )
    remaining_eligible = sorted(
        source_id
        for source_id in training_pattern_ids
        if training_labels[source_id] != "ineligible"
    ) + sorted(
        source_id
        for source_id in holdout_pattern_ids
        if holdout_labels[source_id] != "ineligible"
    )
    if remaining_eligible:
        raise ValueError(
            f"title-pattern matches remain eligible after correction: {remaining_eligible[:10]}"
        )

    correction_rows = list(training_corrections.values()) + list(holdout_corrections.values())
    ledger_by_source_id = {row["source_id"]: {
        **row,
        "new_label": "ineligible",
        "review_authority": REVIEW_AUTHORITY,
        "review_scope": "title",
        "reason": CORRECTION_REASON,
        "news_synthesis_engine_version": ENGINE_VERSION,
    } for row in correction_rows}
    for output, parent, corrections in (
        (args.training_output, PARENT_TRAINING, training_corrections),
        (args.holdout_output, PARENT_HOLDOUT, holdout_corrections),
    ):
        previous = {
            str(row["source_id"]): row
            for row in iter_jsonl(parent / "why_moving_correction_ledger.jsonl")
        }
        incremental = {
            source_id: ledger_by_source_id[source_id]
            for source_id in corrections
        }
        overlap = set(previous) & set(incremental)
        if overlap:
            raise ValueError(f"incremental correction already exists in parent: {sorted(overlap)[:10]}")
        cumulative = {**previous, **incremental}
        _write_jsonl(
            output / "incremental_why_moving_correction_ledger.jsonl",
            (incremental[source_id] for source_id in sorted(incremental)),
        )
        _write_jsonl(
            output / "why_moving_correction_ledger.jsonl",
            (cumulative[source_id] for source_id in sorted(cumulative)),
        )

    pattern_counts = dict(Counter(row["title_pattern"] for row in audit_rows))
    validation = {
        "status": "passed",
        "review_authority": REVIEW_AUTHORITY,
        "news_synthesis_engine_version": ENGINE_VERSION,
        "reviewed_rows": len(audit_rows),
        "reviewed_training_rows": len(training_review),
        "reviewed_holdout_rows": len(holdout_review),
        "incremental_training_corrections": len(training_corrections),
        "incremental_holdout_corrections": len(holdout_corrections),
        "incremental_total_label_corrections": len(correction_rows),
        "cumulative_training_corrections": sum(
            1 for _ in iter_jsonl(args.training_output / "why_moving_correction_ledger.jsonl")
        ),
        "cumulative_holdout_corrections": sum(
            1 for _ in iter_jsonl(args.holdout_output / "why_moving_correction_ledger.jsonl")
        ),
        "reviewed_title_pattern_counts": pattern_counts,
        "training_why_moving_rows": len(why_training),
        "training_title_pattern_matches": len(training_pattern_ids),
        "training_news_synthesis_purposes": dict(training_purposes),
        "holdout_articles": len(holdout_sources),
        "holdout_title_pattern_matches": len(holdout_pattern_ids),
        "holdout_news_synthesis_purposes": dict(holdout_purposes),
        "pattern_matches_still_eligible": 0,
        "checks": {
            "all_reviewed_titles_match_named_pattern": True,
            "all_reviewed_labels_remain_ineligible": True,
            "all_other_matching_training_rows_ineligible": True,
            "all_other_matching_holdout_rows_ineligible": True,
            "all_matching_news_synthesis_purposes_block_forecast_trigger": True,
            "parent_authorities_immutable": True,
        },
    }
    _write_json(args.training_output / "VALIDATION.json", validation)
    _write_json(args.holdout_output / "VALIDATION.json", validation)
    _write_json(args.training_output / "REPORT.json", {
        "status": "scoped_human_title_review_successor",
        "authority_version": args.training_output.name,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "parent_authority": str(PARENT_TRAINING),
        "label_counts": training_counts,
        "label_changes": len(training_corrections),
        "review_authority": REVIEW_AUTHORITY,
        "limitations": [
            "The correction is a human adjudication of titles, not a second full-text review.",
            "Issuer sentiment labels are inherited byte-for-byte and are not changed.",
        ],
    })
    _write_json(args.training_output / "LOAD_MANIFEST.json", {
        "status": "scoped_human_title_review_successor",
        "dataset_version": args.training_output.name,
        "parent_authority": str(PARENT_TRAINING),
        "primary_tables": {
            "article_forecast_eligibility": {
                "path": str(args.training_output / "article_forecast_eligibility_labels.jsonl"),
                "primary_key": ["source_id"],
                "rows": len(training_labels),
            },
            "gold_issuer_sentiment": {
                "path": str(args.training_output / "gold_issuer_sentiment_labels.jsonl"),
                "primary_key": ["unit_id"],
            },
        },
    })
    parent_holdout_report = json.loads(
        (PARENT_HOLDOUT / "FINAL_LABEL_REPORT_V2.json").read_text(encoding="utf-8")
    )
    corrected_holdout_report = {
        **parent_holdout_report,
        "audit_version": args.holdout_output.name,
        "final_labels_sha256": _sha256(args.holdout_output / "FINAL_LABELS_V2.jsonl"),
        "labels": holdout_counts,
        "post_review_corrections": len(holdout_corrections),
        "parent_authority": str(PARENT_HOLDOUT),
        "review_authority": REVIEW_AUTHORITY,
    }
    _write_json(args.holdout_output / "FINAL_LABEL_REPORT_V2.json", corrected_holdout_report)
    _write_json(args.holdout_output / "REPORT.json", {
        "status": "scoped_human_title_review_successor",
        "authority_version": args.holdout_output.name,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "parent_authority": str(PARENT_HOLDOUT),
        "label_counts": holdout_counts,
        "label_changes": len(holdout_corrections),
        "review_authority": REVIEW_AUTHORITY,
    })

    _manifest(args.training_output, args.training_output.name)
    _manifest(args.holdout_output, args.holdout_output.name)
    result = {
        "status": "passed",
        "training_output": str(args.training_output),
        "holdout_output": str(args.holdout_output),
        "validation": validation,
        "training_label_counts": training_counts,
        "holdout_label_counts": holdout_counts,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("PASSED  why-moving forecast-label correction")
        print(
            f"incremental_corrected={validation['incremental_total_label_corrections']:,} "
            f"direct={validation['reviewed_rows']:,} "
            f"cumulative_training={validation['cumulative_training_corrections']:,}"
        )
        print(
            f"validated training={validation['training_title_pattern_matches']:,} "
            f"holdout={validation['holdout_title_pattern_matches']:,} remaining_eligible=0"
        )
        print(f"training authority  {args.training_output}")
        print(f"holdout authority   {args.holdout_output}")


if __name__ == "__main__":
    main()
