from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.text_intelligence.news_synthesis_v1.engine import ENGINE_VERSION, NewsSynthesisEngine  # noqa: E402
from research.text_intelligence.news_synthesis_v1.provider_filter_analysis import (  # noqa: E402
    canonical_json,
    iter_jsonl,
)
from scripts.evaluate_news_synthesis_pattern_policy_gold import (  # noqa: E402
    ASSIGNMENTS,
    HOLDOUT_GOLD,
    NEWS_ROOT,
    TRAINING_GOLD,
    _holdout_sources,
    _initialize_prediction_worker,
    _load_scope,
    _load_scoped_identity_index,
    _prediction,
    _query_training_month,
    _sha256,
    _worker_prediction,
    _write_json,
)


TRAINING_GOLD_V1 = TRAINING_GOLD.with_name(
    "forecast_eligibility_sentiment_authority_pattern_policy_final_v1"
)
HOLDOUT_GOLD_V1 = HOLDOUT_GOLD.with_name(
    "forecast_eligibility_august_2026_temporal_holdout_pattern_policy_final_v1"
)
PARENT_EVALUATION = NEWS_ROOT / "news_synthesis_v57_pattern_policy_gold_evaluation_v1"
DEFAULT_OUTPUT = NEWS_ROOT / "news_synthesis_v57_pattern_policy_gold_evaluation_v2"
EVALUATION_VERSION = "news_synthesis_pattern_policy_gold_delta_evaluation_v2"


def _load_gold(
    scope: dict[str, dict[str, str]],
    *,
    training_root: Path,
    holdout_root: Path,
) -> dict[str, str]:
    training_ids = {
        source_id for source_id, row in scope.items()
        if row["population_split"] == "training_development"
    }
    gold = {
        str(row["source_id"]): str(row["forecast_eligibility_label"])
        for row in iter_jsonl(training_root / "article_forecast_eligibility_labels.jsonl")
        if str(row["source_id"]) in training_ids
    }
    for row in iter_jsonl(holdout_root / "FINAL_LABELS_V2.jsonl"):
        source_id = str(row["source_id"])
        if source_id in gold:
            raise ValueError(f"training/holdout overlap: {source_id}")
        gold[source_id] = str(row["final_label"])
    if set(gold) != set(scope):
        raise ValueError("gold identity set does not match frozen scope")
    return gold


def _cell(actual: str, predicted: str) -> str:
    if actual == "eligible":
        return "tp" if predicted == "eligible" else "fn"
    return "fp" if predicted == "eligible" else "tn"


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    parser = argparse.ArgumentParser(description="Re-evaluate only identities changed by the v2 gold successor.")
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists() or args.output.with_name(args.output.name + ".building").exists():
        raise FileExistsError(f"evaluation output exists: {args.output}")
    if not 1 <= args.workers <= 32:
        raise ValueError("workers must be in [1, 32]")

    started = time.monotonic()
    scope, months = _load_scope()
    old_gold = _load_gold(scope, training_root=TRAINING_GOLD_V1, holdout_root=HOLDOUT_GOLD_V1)
    new_gold = _load_gold(scope, training_root=TRAINING_GOLD, holdout_root=HOLDOUT_GOLD)
    changed_ids = {source_id for source_id in scope if old_gold[source_id] != new_gold[source_id]}
    if not changed_ids:
        raise ValueError("v2 gold has no changed identities")

    parent_report = json.loads((PARENT_EVALUATION / "REPORT.json").read_text(encoding="utf-8"))
    if parent_report["engine_version"] != ENGINE_VERSION or parent_report["population_articles"] != len(scope):
        raise ValueError("parent evaluation is not reusable for this engine/population")
    mismatch_by_id = {
        str(row["source_id"]): row
        for row in iter_jsonl(PARENT_EVALUATION / "MISMATCHES.jsonl")
        if str(row["source_id"]) not in changed_ids
    }
    confusion = Counter({key: int(value) for key, value in parent_report["confusion"].items()})
    holdout_sources = _holdout_sources()
    rescored = 0
    failures: list[dict[str, str]] = []

    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=300
    )
    try:
        for month in months:
            month_ids = {
                source_id for source_id in changed_ids
                if str(scope[source_id]["published_at_utc"]).startswith(month)
            }
            if not month_ids:
                continue
            full_month_ids = {
                source_id for source_id, row in scope.items()
                if str(row["published_at_utc"]).startswith(month)
            }
            month_tickers = {
                ticker.strip().upper()
                for source_id in full_month_ids
                for ticker in str(scope[source_id]["tickers"] or "").split("|")
                if ticker.strip()
            }
            identity_index, identities = _load_scoped_identity_index(
                client, database=args.database, tickers=month_tickers
            )
            training_ids = {
                source_id for source_id in month_ids
                if scope[source_id]["population_split"] == "training_development"
            }
            sources = {
                str(row["source_id"]): row
                for row in _query_training_month(client, database=args.database, month=month)
                if str(row["source_id"]) in training_ids
            }
            for source_id in month_ids - training_ids:
                sources[source_id] = holdout_sources[source_id]
            if set(sources) != month_ids:
                raise ValueError(f"changed-source coverage mismatch for {month}")
            ordered_ids = sorted(month_ids)
            engine = NewsSynthesisEngine(identity_index)
            if args.workers == 1:
                predictions = [_prediction(engine, sources[source_id]) for source_id in ordered_ids]
            else:
                with ProcessPoolExecutor(
                    max_workers=args.workers,
                    initializer=_initialize_prediction_worker,
                    initargs=(identities,),
                ) as executor:
                    predictions = list(executor.map(
                        _worker_prediction,
                        (sources[source_id] for source_id in ordered_ids),
                        chunksize=32,
                    ))
            for source_id, prediction in zip(ordered_ids, predictions):
                if prediction["error"]:
                    failures.append({"source_id": source_id, "error": str(prediction["error"])})
                    continue
                predicted = str(prediction["prediction"])
                old_actual = old_gold[source_id]
                new_actual = new_gold[source_id]
                if old_actual in {"eligible", "ineligible"}:
                    confusion[_cell(old_actual, predicted)] -= 1
                if new_actual in {"eligible", "ineligible"}:
                    confusion[_cell(new_actual, predicted)] += 1
                    if new_actual != predicted:
                        path = " > ".join((prediction["structure"], prediction["purpose"], prediction["origin"]))
                        mismatch_by_id[source_id] = {
                            "source_id": source_id,
                            "published_at_utc": scope[source_id]["published_at_utc"],
                            "gold_label": new_actual,
                            "v57_label": predicted,
                            "confusion_cell": "fp" if predicted == "eligible" else "fn",
                            "title": scope[source_id]["title"],
                            "tickers": scope[source_id]["tickers"].split("|") if scope[source_id]["tickers"] else [],
                            "synthesis_path": path,
                            "quality_flags": prediction["quality_flags"],
                        }
                rescored += 1
            print(f"[{month}] rescored={rescored:,}/{len(changed_ids):,}", flush=True)
    finally:
        client.close()
    if failures or rescored != len(changed_ids):
        raise RuntimeError(f"delta evaluation incomplete: rescored={rescored}, failures={len(failures)}")

    tp, fn, fp, tn = (confusion[name] for name in ("tp", "fn", "fp", "tn"))
    binary = tp + fn + fp + tn
    path_counts = Counter(str(row["synthesis_path"]) for row in mismatch_by_id.values())
    building = args.output.with_name(args.output.name + ".building")
    building.mkdir(parents=True)
    with (building / "MISMATCHES.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for source_id in sorted(mismatch_by_id):
            handle.write(canonical_json(mismatch_by_id[source_id]) + "\n")
    (building / "FAILURES.jsonl").write_text("", encoding="utf-8")
    report: dict[str, Any] = {
        "status": "passed",
        "evaluation_version": EVALUATION_VERSION,
        "engine_version": ENGINE_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "population_articles": len(scope),
        "binary_gold_articles": binary,
        "nonbinary_gold_articles": len(scope) - binary,
        "gold_label_counts": dict(Counter(new_gold.values())),
        "changed_gold_articles_rescored": len(changed_ids),
        "unchanged_predictions_reused": len(scope) - len(changed_ids),
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "metrics": {
            "accuracy": (tp + tn) / binary,
            "eligible_precision": tp / (tp + fp) if tp + fp else None,
            "eligible_recall": tp / (tp + fn) if tp + fn else None,
            "ineligible_recall": tn / (tn + fp) if tn + fp else None,
            "balanced_accuracy": ((tp / (tp + fn)) + (tn / (tn + fp))) / 2,
        },
        "mismatches": fp + fn,
        "mismatch_paths": dict(path_counts.most_common()),
        "authority": {
            "assignments": str(ASSIGNMENTS),
            "assignments_sha256": _sha256(ASSIGNMENTS),
            "training_gold": str(TRAINING_GOLD),
            "training_gold_manifest_sha256": _sha256(TRAINING_GOLD / "HASH_MANIFEST.json"),
            "holdout_gold": str(HOLDOUT_GOLD),
            "holdout_gold_manifest_sha256": _sha256(HOLDOUT_GOLD / "HASH_MANIFEST.json"),
            "parent_evaluation": str(PARENT_EVALUATION),
            "parent_report_sha256": _sha256(PARENT_EVALUATION / "REPORT.json"),
            "parent_mismatches_sha256": _sha256(PARENT_EVALUATION / "MISMATCHES.jsonl"),
        },
    }
    _write_json(building / "REPORT.json", report)
    _write_json(building / "HASH_MANIFEST.json", {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(building.iterdir()) if path.is_file() and path.name != "HASH_MANIFEST.json"
    })
    building.replace(args.output)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
