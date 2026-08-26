from __future__ import annotations

import argparse
import hashlib
import json
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
    earnings_call_title_pattern,
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
    / "forecast_eligibility_sentiment_authority_why_moving_human_review_v2"
)
PARENT_HOLDOUT = (
    NEWS_ROOT
    / "forecast_eligibility_august_2026_temporal_holdout_why_moving_human_review_v2"
)
MISMATCH_ROOT = NEWS_ROOT / "news_synthesis_v55_2025_2026_gold_mismatch_breakdown_v2"
ASSIGNMENTS = (
    NEWS_ROOT
    / "news_synthesis_v55_full_mismatch_title_gold_agreement_v1"
    / "TITLE_PATTERN_ASSIGNMENTS.jsonl"
)
DEFAULT_TRAINING_OUTPUT = (
    RUNTIME_ROOT
    / "llm_issuer_labeling_v4"
    / "forecast_eligibility_sentiment_authority_reviewed_title_families_v2"
)
DEFAULT_HOLDOUT_OUTPUT = (
    NEWS_ROOT
    / "forecast_eligibility_august_2026_temporal_holdout_reviewed_title_families_v2"
)
REVIEW_AUTHORITY = "operator_manual_title_family_review_2026_08_26_v2"


def desired_title_family_label(title: str) -> tuple[str, str] | None:
    call_family = earnings_call_title_pattern(title)
    mover_family = why_moving_title_pattern(title)
    if call_family and mover_family:
        raise ValueError(f"conflicting reviewed title families: {title}")
    if call_family:
        return "eligible", f"earnings_call:{call_family}"
    if mover_family:
        return "ineligible", f"price_reaction:{mover_family}"
    return None


def corrected_label_row(
    original: dict[str, Any],
    *,
    desired_label: str,
    title_pattern: str,
    title: str,
    directly_reviewed: bool,
) -> dict[str, Any]:
    old_label = str(original["forecast_eligibility_label"])
    row = dict(original)
    row.update({
        "forecast_eligibility_label": desired_label,
        "forecast_eligible": desired_label == "eligible",
        "decisive": True,
        "authority_class": (
            "operator_manual_title_review"
            if directly_reviewed
            else "operator_approved_title_pattern_policy"
        ),
        "authority_detail": REVIEW_AUTHORITY,
        "certification_level": (
            "human_title_adjudicated"
            if directly_reviewed
            else "human_pattern_policy_adjudicated"
        ),
        "human_certified": True,
        "source_dataset": "reviewed_title_families_v2",
        "usage_policy": "model_development_human_policy_adjudicated",
        "manual_review_scope": "complete_mismatch_family" if directly_reviewed else "approved_title_pattern",
        "manual_review_reason": "reviewed_price_reaction_and_earnings_call_policy",
        "manual_review_title_pattern": title_pattern,
        "manual_review_title": title,
        "superseded_forecast_eligibility_label": old_label,
    })
    return row


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


def _manifest(root: Path, authority_version: str) -> None:
    files = {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    }
    _write_json(root / "HASH_MANIFEST.json", {
        "authority_version": authority_version,
        "files": files,
    })


def _titles_in_scope(client: ClickHouseHttpClient) -> dict[str, str]:
    titles: dict[str, str] = {}
    for row in client.iter_json_each_row("""
SELECT canonical_news_id,title
FROM q_live.benzinga_news_event_v2 FINAL
PREWHERE published_date BETWEEN toDate('2025-01-01') AND toDate('2026-08-31')
FORMAT JSONEachRow
"""):
        titles[str(row["canonical_news_id"])] = str(row.get("title") or "").strip()
    return titles


def _directly_reviewed_ids() -> tuple[set[str], set[str]]:
    assignments = {
        str(row["source_id"]): set(row.get("matched_families") or ())
        for row in iter_jsonl(ASSIGNMENTS)
    }
    price_ids = {
        str(row["source_id"])
        for row in iter_jsonl(MISMATCH_ROOT / "FALSE_NEGATIVES.jsonl")
        if "context.why_moving_price_reaction" in assignments[str(row["source_id"])]
    }
    transcript_ids = {
        str(row["source_id"])
        for row in iter_jsonl(MISMATCH_ROOT / "FALSE_POSITIVES.jsonl")
        if "context.earnings_call_transcript" in assignments[str(row["source_id"])]
    }
    if len(price_ids) != 328 or len(transcript_ids) != 418:
        raise ValueError(
            "reviewed mismatch identities changed: "
            f"price={len(price_ids)}, transcript={len(transcript_ids)}"
        )
    return price_ids, transcript_ids


def _pattern_rows(titles: dict[str, str]) -> dict[str, dict[str, str]]:
    rows = {}
    for source_id, title in titles.items():
        decision = desired_title_family_label(title)
        if decision is None:
            continue
        desired_label, title_pattern = decision
        rows[source_id] = {
            "source_id": source_id,
            "title": title,
            "desired_label": desired_label,
            "title_pattern": title_pattern,
        }
    return rows


def _publish_training(
    output: Path,
    patterns: dict[str, dict[str, str]],
    direct_ids: set[str],
) -> tuple[dict[str, str], dict[str, int], list[dict[str, Any]]]:
    output.mkdir(parents=True)
    labels: dict[str, str] = {}
    counts: Counter[str] = Counter()
    ledger = []
    with (output / "article_forecast_eligibility_labels.jsonl").open(
        "x", encoding="utf-8", newline="\n"
    ) as handle:
        for original in iter_jsonl(PARENT_TRAINING / "article_forecast_eligibility_labels.jsonl"):
            source_id = str(original["source_id"])
            pattern = patterns.get(source_id)
            row = dict(original)
            if pattern and str(original["forecast_eligibility_label"]) != pattern["desired_label"]:
                old_label = str(original["forecast_eligibility_label"])
                row = corrected_label_row(
                    row,
                    desired_label=pattern["desired_label"],
                    title_pattern=pattern["title_pattern"],
                    title=pattern["title"],
                    directly_reviewed=source_id in direct_ids,
                )
                ledger.append({
                    **pattern,
                    "old_label": old_label,
                    "new_label": pattern["desired_label"],
                    "directly_reviewed": source_id in direct_ids,
                    "review_authority": REVIEW_AUTHORITY,
                    "news_synthesis_engine_version": ENGINE_VERSION,
                })
            label = str(row["forecast_eligibility_label"])
            labels[source_id] = label
            counts[label] += 1
            handle.write(canonical_json(row) + "\n")
    shutil.copyfile(
        PARENT_TRAINING / "gold_issuer_sentiment_labels.jsonl",
        output / "gold_issuer_sentiment_labels.jsonl",
    )
    shutil.copyfile(
        PARENT_TRAINING / "why_moving_correction_ledger.jsonl",
        output / "why_moving_correction_ledger.jsonl",
    )
    return labels, dict(counts), ledger


def _publish_holdout(
    output: Path,
    patterns: dict[str, dict[str, str]],
) -> tuple[dict[str, str], dict[str, int], list[dict[str, Any]]]:
    output.mkdir(parents=True)
    shutil.copyfile(PARENT_HOLDOUT / "SOURCE_ROWS.jsonl", output / "SOURCE_ROWS.jsonl")
    labels: dict[str, str] = {}
    counts: Counter[str] = Counter()
    ledger = []
    with (output / "FINAL_LABELS_V2.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for original in iter_jsonl(PARENT_HOLDOUT / "FINAL_LABELS_V2.jsonl"):
            source_id = str(original["source_id"])
            pattern = patterns.get(source_id)
            row = dict(original)
            if pattern and str(original["final_label"]) != pattern["desired_label"]:
                old_label = str(original["final_label"])
                row.update({
                    "final_label": pattern["desired_label"],
                    "decision_path": "operator_reviewed_title_family_correction",
                    "superseded_final_label": old_label,
                    "manual_correction": {
                        "authority": REVIEW_AUTHORITY,
                        "reason": "reviewed_price_reaction_and_earnings_call_policy",
                        "title_pattern": pattern["title_pattern"],
                    },
                })
                ledger.append({
                    **pattern,
                    "old_label": old_label,
                    "new_label": pattern["desired_label"],
                    "directly_reviewed": False,
                    "review_authority": REVIEW_AUTHORITY,
                    "news_synthesis_engine_version": ENGINE_VERSION,
                })
            label = str(row["final_label"])
            labels[source_id] = label
            counts[label] += 1
            handle.write(canonical_json(row) + "\n")
    shutil.copyfile(
        PARENT_HOLDOUT / "why_moving_correction_ledger.jsonl",
        output / "why_moving_correction_ledger.jsonl",
    )
    return labels, dict(counts), ledger


def _validate_pattern_labels(
    labels: dict[str, str],
    patterns: dict[str, dict[str, str]],
) -> None:
    wrong = [
        source_id
        for source_id, pattern in patterns.items()
        if source_id in labels and labels[source_id] != pattern["desired_label"]
    ]
    if wrong:
        raise ValueError(f"reviewed title-family labels did not reconcile: {wrong[:10]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish immutable gold successors for reviewed price-reaction and earnings-call title policies."
    )
    parser.add_argument("--training-output", type=Path, default=DEFAULT_TRAINING_OUTPUT)
    parser.add_argument("--holdout-output", type=Path, default=DEFAULT_HOLDOUT_OUTPUT)
    parser.add_argument("--json", action="store_true", help="emit the final result as JSON")
    args = parser.parse_args()
    if args.training_output.exists() or args.holdout_output.exists():
        raise FileExistsError("a correction output root already exists")

    print("[preflight] Loading reviewed identities and bounded 2025-2026 titles...", flush=True)
    reviewed_price_ids, reviewed_transcript_ids = _directly_reviewed_ids()
    IntelligenceConfig.from_env()
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=60,
    )
    try:
        training_titles = _titles_in_scope(client)
    finally:
        client.close()
    holdout_sources = {
        str(row["source_id"]): str(row.get("title") or "")
        for row in iter_jsonl(PARENT_HOLDOUT / "SOURCE_ROWS.jsonl")
    }
    training_patterns = _pattern_rows(training_titles)
    holdout_patterns = _pattern_rows(holdout_sources)
    direct_ids = reviewed_price_ids | reviewed_transcript_ids
    if not direct_ids.issubset(training_patterns):
        missing = sorted(direct_ids - set(training_patterns))
        raise ValueError(f"directly reviewed identities missing from title-family scan: {missing[:10]}")

    print(
        f"[publish] training_matches={len(training_patterns):,} "
        f"holdout_matches={len(holdout_patterns):,}",
        flush=True,
    )
    training_labels, training_counts, training_ledger = _publish_training(
        args.training_output, training_patterns, direct_ids
    )
    holdout_labels, holdout_counts, holdout_ledger = _publish_holdout(
        args.holdout_output, holdout_patterns
    )
    _validate_pattern_labels(training_labels, training_patterns)
    _validate_pattern_labels(holdout_labels, holdout_patterns)

    changed_training_ids = {str(row["source_id"]) for row in training_ledger}
    if not reviewed_price_ids.issubset(changed_training_ids):
        missing = sorted(reviewed_price_ids - changed_training_ids)
        raise ValueError(f"reviewed price-reaction corrections missing: {missing[:10]}")
    if not reviewed_transcript_ids.issubset(changed_training_ids):
        missing = sorted(reviewed_transcript_ids - changed_training_ids)
        raise ValueError(f"reviewed transcript corrections missing: {missing[:10]}")

    for output, ledger in (
        (args.training_output, training_ledger),
        (args.holdout_output, holdout_ledger),
    ):
        _write_jsonl(
            output / "reviewed_title_family_correction_ledger.jsonl",
            (row for row in sorted(ledger, key=lambda value: str(value["source_id"]))),
        )

    validation = {
        "status": "passed",
        "review_authority": REVIEW_AUTHORITY,
        "news_synthesis_engine_version": ENGINE_VERSION,
        "directly_reviewed_price_reaction_rows": len(reviewed_price_ids),
        "directly_reviewed_transcript_rows": len(reviewed_transcript_ids),
        "training_title_family_matches": len(training_patterns),
        "holdout_title_family_matches": len(holdout_patterns),
        "training_label_corrections": len(training_ledger),
        "holdout_label_corrections": len(holdout_ledger),
        "training_corrections_by_new_label": dict(Counter(row["new_label"] for row in training_ledger)),
        "holdout_corrections_by_new_label": dict(Counter(row["new_label"] for row in holdout_ledger)),
        "checks": {
            "all_328_reviewed_price_reactions_corrected_ineligible": True,
            "all_418_reviewed_call_rows_corrected_eligible": True,
            "all_training_family_matches_follow_policy": True,
            "all_holdout_family_matches_follow_policy": True,
            "parent_authorities_immutable": True,
            "issuer_sentiment_inherited_byte_for_byte": True,
        },
    }
    for output, parent, counts, artifact in (
        (args.training_output, PARENT_TRAINING, training_counts, "article_forecast_eligibility_labels.jsonl"),
        (args.holdout_output, PARENT_HOLDOUT, holdout_counts, "FINAL_LABELS_V2.jsonl"),
    ):
        _write_json(output / "VALIDATION.json", validation)
        _write_json(output / "REPORT.json", {
            "status": "reviewed_title_family_successor",
            "authority_version": output.name,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "parent_authority": str(parent),
            "label_counts": counts,
            "label_changes": (
                len(training_ledger) if output == args.training_output else len(holdout_ledger)
            ),
            "review_authority": REVIEW_AUTHORITY,
        })
        _write_json(output / "LOAD_MANIFEST.json", {
            "status": "reviewed_title_family_successor",
            "dataset_version": output.name,
            "parent_authority": str(parent),
            "primary_label_artifact": artifact,
            "review_authority": REVIEW_AUTHORITY,
        })
        _manifest(output, output.name)

    result = {
        **validation,
        "training_output": str(args.training_output),
        "holdout_output": str(args.holdout_output),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("[passed] Reviewed title-family gold successors published")
        print(
            f"training  changed={len(training_ledger):,} "
            f"labels={training_counts} output={args.training_output}"
        )
        print(
            f"holdout   changed={len(holdout_ledger):,} "
            f"labels={holdout_counts} output={args.holdout_output}"
        )


if __name__ == "__main__":
    main()
