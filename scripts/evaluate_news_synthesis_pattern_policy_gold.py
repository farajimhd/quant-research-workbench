from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.text_intelligence.news_synthesis_v1.engine import (  # noqa: E402
    ENGINE_VERSION,
    IssuerIdentity,
    IssuerIdentityIndex,
    NewsSynthesisEngine,
)
from research.text_intelligence.news_synthesis_v1.provider_filter_analysis import (  # noqa: E402
    canonical_json,
    iter_jsonl,
)


RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence")
NEWS_ROOT = RUNTIME_ROOT / "news_synthesis_v1"
TRAINING_GOLD = (
    RUNTIME_ROOT
    / "llm_issuer_labeling_v4"
    / "forecast_eligibility_sentiment_authority_v59_calibrated_reaudit_v1"
)
HOLDOUT_GOLD = (
    NEWS_ROOT
    / "forecast_eligibility_august_2026_temporal_holdout_pattern_policy_final_v2"
)
ASSIGNMENTS = (
    NEWS_ROOT
    / "news_v59_training_mismatch_calibrated_file_reaudit_v3"
    / "reconciliation"
    / "final"
    / "ARTICLE_POLICY_ASSIGNMENTS_REAUDITED.csv"
)
DEFAULT_OUTPUT = NEWS_ROOT / "news_synthesis_v61_reaudited_gold_evaluation_v1"
EVALUATION_VERSION = "news_synthesis_pattern_policy_gold_evaluation_v3"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _authority_manifest(root: Path) -> Path:
    for name in ("MANIFEST.json", "HASH_MANIFEST.json"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"authority manifest is missing: {root}")


def _result_block(
    *,
    population_articles: int,
    gold_counts: Counter[str],
    confusion: Counter[tuple[str, str]],
    path_mismatches: Counter[str],
    title_policy_predictions: Counter[tuple[str, str, str]],
) -> dict[str, Any]:
    tp = confusion[("eligible", "eligible")]
    fn = confusion[("eligible", "ineligible")]
    fp = confusion[("ineligible", "eligible")]
    tn = confusion[("ineligible", "ineligible")]
    binary = tp + fn + fp + tn
    eligible_recall = tp / (tp + fn) if tp + fn else None
    ineligible_recall = tn / (tn + fp) if tn + fp else None
    return {
        "population_articles": population_articles,
        "binary_gold_articles": binary,
        "nonbinary_gold_articles": population_articles - binary,
        "gold_label_counts": dict(gold_counts),
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "metrics": {
            "accuracy": (tp + tn) / binary if binary else None,
            "eligible_precision": tp / (tp + fp) if tp + fp else None,
            "eligible_recall": eligible_recall,
            "ineligible_recall": ineligible_recall,
            "balanced_accuracy": (
                (eligible_recall + ineligible_recall) / 2
                if eligible_recall is not None and ineligible_recall is not None
                else None
            ),
        },
        "mismatches": fp + fn,
        "mismatch_paths": dict(path_mismatches.most_common()),
        "reviewed_title_policy_confusion": {
            f"{family}|gold_{actual}|predicted_{predicted}": count
            for (family, actual, predicted), count in sorted(title_policy_predictions.items())
        },
    }


def _month_bounds(month: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month}-01")
    end = date(start.year + int(start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, end


def _load_scope() -> tuple[dict[str, dict[str, str]], list[str]]:
    rows: dict[str, dict[str, str]] = {}
    with ASSIGNMENTS.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = str(row["source_id"])
            if source_id in rows:
                raise ValueError(f"duplicate assignment: {source_id}")
            rows[source_id] = row
    if len(rows) != 352_559:
        raise ValueError(f"assignment population changed: {len(rows)}")
    months = sorted({str(row["published_at_utc"])[:7] for row in rows.values()})
    return rows, months


def _load_gold(scope: dict[str, dict[str, str]]) -> dict[str, str]:
    gold: dict[str, str] = {}
    training_ids = {
        source_id for source_id, row in scope.items()
        if row["population_split"] == "training_development"
    }
    for row in iter_jsonl(TRAINING_GOLD / "article_forecast_eligibility_labels.jsonl"):
        source_id = str(row["source_id"])
        if source_id in training_ids:
            gold[source_id] = str(row["forecast_eligibility_label"])
    for row in iter_jsonl(HOLDOUT_GOLD / "FINAL_LABELS_V2.jsonl"):
        source_id = str(row["source_id"])
        if source_id not in scope or scope[source_id]["population_split"] != "holdout_august_2026":
            raise ValueError(f"holdout label outside evaluation scope: {source_id}")
        if source_id in gold:
            raise ValueError(f"training/holdout identity overlap: {source_id}")
        gold[source_id] = str(row["final_label"])
    if set(gold) != set(scope):
        raise ValueError(
            f"gold/scope mismatch: missing={len(set(scope) - set(gold))}, extra={len(set(gold) - set(scope))}"
        )
    return gold


def _holdout_sources() -> dict[str, dict[str, Any]]:
    result = {}
    for row in iter_jsonl(HOLDOUT_GOLD / "SOURCE_ROWS.jsonl"):
        source_id = str(row["source_id"])
        result[source_id] = {
            "source_id": source_id,
            "source_timestamp": row["published_at_utc"],
            "provider": row.get("provider"),
            "title": row.get("title"),
            "author": row.get("author"),
            "url_domain": row.get("url_domain"),
            "text": row.get("rendered_text") or row.get("title") or "",
            "tickers": row.get("tickers") or [],
            "channels": row.get("channels") or [],
            "provider_tags": row.get("provider_tags") or [],
            "content_quality_flags": row.get("content_quality_flags") or [],
            "quality_flags": row.get("renderer_quality_flags") or [],
            "source_revision_key": row.get("source_revision_key") or "",
            "render_status": "title_only" if row.get("title_only") else "rendered",
            "rendered_text_hash": row.get("rendered_text_hash") or "",
        }
    return result


def _optional_date(value: Any) -> date | None:
    clean = str(value or "")[:10]
    if not clean or clean == "0000-00-00":
        return None
    return date.fromisoformat(clean)


def _load_scoped_identity_index(
    client: ClickHouseHttpClient,
    *,
    database: str,
    tickers: set[str],
) -> tuple[IssuerIdentityIndex, tuple[IssuerIdentity, ...]]:
    if not tickers:
        raise ValueError("evaluation scope contains no tickers")
    ticker_sql = ",".join(sql_string(ticker) for ticker in sorted(tickers))
    rows = list(client.iter_json_each_row(f"""
SELECT upperUTF8(sym.ticker_normalized) AS ticker,
       sec.issuer_id AS issuer_id,sec.security_id AS security_id,
       coalesce(nullIf(issuer.branding_name,''),nullIf(issuer.issuer_name,''),
                nullIf(issuer.legal_name,''),sym.display_name) display_name,
       arrayFilter(value -> notEmpty(value),[
         ifNull(issuer.issuer_name,''),ifNull(issuer.legal_name,''),
         ifNull(issuer.branding_name,''),ifNull(sec.security_name,''),ifNull(sym.display_name,'')
       ]) aliases,
       listing.exchange_code AS exchange_code,toString(listing.list_date) AS list_date,
       toString(listing.delisted_date) AS delisted_date
FROM `{database}`.`id_symbol_v1` sym FINAL
INNER JOIN `{database}`.`id_listing_v1` listing FINAL ON listing.listing_id=sym.listing_id
INNER JOIN `{database}`.`id_security_v1` sec FINAL ON sec.security_id=listing.security_id
INNER JOIN `{database}`.`id_issuer_v1` issuer FINAL ON issuer.issuer_id=sec.issuer_id
WHERE sym.ticker_normalized!='' AND sec.issuer_id!='' AND listing.currency_code='USD'
  AND upperUTF8(sym.ticker_normalized) IN ({ticker_sql})
FORMAT JSONEachRow
"""))
    identities = [
        IssuerIdentity(
            ticker=str(row["ticker"]),
            issuer_id=str(row["issuer_id"]),
            display_name=str(row["display_name"]),
            aliases=tuple(str(value).strip() for value in row.get("aliases") or () if str(value).strip()),
            security_id=str(row.get("security_id") or ""),
            exchange_code=str(row.get("exchange_code") or ""),
            list_date=_optional_date(row.get("list_date")),
            delisted_date=_optional_date(row.get("delisted_date")),
        )
        for row in rows
        if row.get("aliases")
    ]
    if not identities:
        raise RuntimeError("scoped identity preflight returned no canonical identities")
    return IssuerIdentityIndex(identities), tuple(identities)


def _query_training_month(
    client: ClickHouseHttpClient,
    *,
    database: str,
    month: str,
) -> Iterable[dict[str, Any]]:
    start, end = _month_bounds(month)
    query = f"""
SELECT e.canonical_news_id source_id,toString(e.published_at_utc) source_timestamp,
       e.provider,e.title,e.author,e.article_url,e.url_domain,
       if(empty(r.rendered_text),e.title,r.rendered_text) text,
       e.tickers,e.channels,e.provider_tags,e.content_quality_flags,r.quality_flags,
       e.source_revision_key,
       multiIf(empty(r.canonical_news_id),'unrendered',r.source_count=0,'title_only','rendered') render_status,
       if(empty(r.rendered_text_hash),hex(SHA256(e.title)),r.rendered_text_hash) rendered_text_hash
FROM `{database}`.`benzinga_news_event_v2` e FINAL
LEFT JOIN `{database}`.`benzinga_news_rendered_v2` r FINAL
  ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
PREWHERE e.published_date >= toDate({sql_string(start.isoformat())})
    AND e.published_date < toDate({sql_string(end.isoformat())})
FORMAT JSONEachRow
"""
    return client.iter_json_each_row(query)


def _prediction(engine: NewsSynthesisEngine, source: dict[str, Any]) -> dict[str, Any]:
    try:
        document = engine.synthesize(source)
        forecast_rows = [
            row for row in document["eligibility"]
            if row["product"] == "forecast_trigger"
        ]
        eligible = any(
            bool(row["eligible"]) for row in forecast_rows
        )
        envelope = document["envelope"]
        return {
            "prediction": "eligible" if eligible else "ineligible",
            "purpose": envelope["communication_purpose"]["value"],
            "origin": envelope["information_origin"]["value"],
            "structure": envelope["document_structure"]["value"],
            "quality_flags": document["quality_flags"],
            "forecast_policy_ids": sorted({str(row["policy_id"]) for row in forecast_rows}),
            "forecast_reasons": sorted({
                str(reason) for row in forecast_rows for reason in row.get("reasons") or ()
            }),
            "error": None,
        }
    except Exception as exc:
        return {
            "prediction": "error",
            "purpose": "error",
            "origin": "error",
            "structure": "error",
            "quality_flags": [],
            "forecast_policy_ids": [],
            "forecast_reasons": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


_PROCESS_ENGINE: NewsSynthesisEngine | None = None


def _initialize_prediction_worker(identities: tuple[IssuerIdentity, ...]) -> None:
    global _PROCESS_ENGINE
    _PROCESS_ENGINE = NewsSynthesisEngine(IssuerIdentityIndex(identities))


def _worker_prediction(source: dict[str, Any]) -> dict[str, Any]:
    if _PROCESS_ENGINE is None:
        raise RuntimeError("prediction worker was not initialized")
    return _prediction(_PROCESS_ENGINE, source)


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    parser = argparse.ArgumentParser(description="Evaluate News Synthesis against reviewed 2025-2026 gold.")
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 32:
        raise ValueError("workers must be in [1, 32]")
    if args.output.exists() or args.output.with_name(args.output.name + ".building").exists():
        raise FileExistsError(f"evaluation output already exists: {args.output}")

    started = time.monotonic()
    scope, months = _load_scope()
    gold = _load_gold(scope)
    holdout = _holdout_sources()
    building = args.output.with_name(args.output.name + ".building")
    building.mkdir(parents=True)
    mismatch_path = building / "MISMATCHES.jsonl"
    failure_path = building / "FAILURES.jsonl"
    confusion: Counter[tuple[str, str]] = Counter()
    gold_counts: Counter[str] = Counter(gold.values())
    path_mismatches: Counter[str] = Counter()
    title_policy_predictions: Counter[tuple[str, str, str]] = Counter()
    splits = tuple(sorted({str(row["population_split"]) for row in scope.values()}))
    split_confusion = {split: Counter() for split in splits}
    split_gold_counts = {
        split: Counter(
            gold[source_id]
            for source_id, row in scope.items()
            if str(row["population_split"]) == split
        )
        for split in splits
    }
    split_path_mismatches = {split: Counter() for split in splits}
    split_title_policy_predictions = {split: Counter() for split in splits}
    source_seen: set[str] = set()
    failure_count = 0

    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=300
    )
    try:
        scoped_tickers = {
            ticker.strip().upper()
            for row in scope.values()
            for ticker in str(row["tickers"] or "").split("|")
            if ticker.strip()
        }
        identity_months: dict[str, dict[str, int]] = {}
        with mismatch_path.open("x", encoding="utf-8", newline="\n") as mismatch_handle, failure_path.open(
            "x", encoding="utf-8", newline="\n"
        ) as failure_handle:
            for month in months:
                month_ids = {
                    source_id for source_id, row in scope.items()
                    if str(row["published_at_utc"]).startswith(month)
                }
                training_ids = {
                    source_id for source_id in month_ids
                    if scope[source_id]["population_split"] == "training_development"
                }
                month_tickers = {
                    ticker.strip().upper()
                    for source_id in month_ids
                    for ticker in str(scope[source_id]["tickers"] or "").split("|")
                    if ticker.strip()
                }
                identity_index, identities = _load_scoped_identity_index(
                    client, database=args.database, tickers=month_tickers
                )
                identity_rows = len(identities)
                identity_months[month] = {
                    "scoped_tickers": len(month_tickers),
                    "identity_rows": identity_rows,
                }
                engine = NewsSynthesisEngine(identity_index)
                sources = {
                    str(row["source_id"]): row
                    for row in _query_training_month(client, database=args.database, month=month)
                    if str(row["source_id"]) in training_ids
                }
                for source_id in month_ids - training_ids:
                    if source_id not in holdout:
                        raise ValueError(f"missing sealed holdout source: {source_id}")
                    sources[source_id] = holdout[source_id]
                if set(sources) != month_ids:
                    raise ValueError(
                        f"source coverage mismatch for {month}: missing={len(month_ids - set(sources))}, extra={len(set(sources) - month_ids)}"
                    )
                ordered_ids = sorted(month_ids)
                if args.workers == 1:
                    predictions = [
                        _prediction(engine, sources[source_id]) for source_id in ordered_ids
                    ]
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
                    source_seen.add(source_id)
                    actual = gold[source_id]
                    predicted = str(prediction["prediction"])
                    population_split = str(scope[source_id]["population_split"])
                    if predicted == "error":
                        failure_count += 1
                        failure_handle.write(canonical_json({
                            "source_id": source_id,
                            "title": scope[source_id]["title"],
                            "error": prediction["error"],
                        }) + "\n")
                        continue
                    if actual in {"eligible", "ineligible"}:
                        confusion[(actual, predicted)] += 1
                        split_confusion[population_split][(actual, predicted)] += 1
                        if actual != predicted:
                            path = " > ".join((prediction["structure"], prediction["purpose"], prediction["origin"]))
                            path_mismatches[path] += 1
                            split_path_mismatches[population_split][path] += 1
                            mismatch_handle.write(canonical_json({
                                "source_id": source_id,
                                "published_at_utc": scope[source_id]["published_at_utc"],
                                "population_split": population_split,
                                "gold_label": actual,
                                "synthesis_label": predicted,
                                "confusion_cell": "fp" if predicted == "eligible" else "fn",
                                "title": scope[source_id]["title"],
                                "tickers": scope[source_id]["tickers"].split("|") if scope[source_id]["tickers"] else [],
                                "synthesis_path": path,
                                "quality_flags": prediction["quality_flags"],
                                "forecast_policy_ids": prediction["forecast_policy_ids"],
                                "forecast_reasons": prediction["forecast_reasons"],
                            }) + "\n")
                    for flag in prediction["quality_flags"]:
                        if str(flag).startswith("reviewed_title_policy:"):
                            title_key = (str(flag).split(":", 1)[1], actual, predicted)
                            title_policy_predictions[title_key] += 1
                            split_title_policy_predictions[population_split][title_key] += 1
                print(
                    f"[{month}] status=completed tickers={len(month_tickers):,} "
                    f"identities={identity_rows:,} completed={len(source_seen):,}/{len(scope):,} "
                    f"failed={failure_count:,} queued={len(scope) - len(source_seen):,}",
                    flush=True,
                )
    finally:
        client.close()

    if source_seen != set(scope) or failure_count:
        raise RuntimeError(
            f"evaluation incomplete: seen={len(source_seen)}/{len(scope)}, failures={failure_count}"
        )
    overall = _result_block(
        population_articles=len(scope),
        gold_counts=gold_counts,
        confusion=confusion,
        path_mismatches=path_mismatches,
        title_policy_predictions=title_policy_predictions,
    )
    split_results = {
        split: _result_block(
            population_articles=sum(
                str(row["population_split"]) == split for row in scope.values()
            ),
            gold_counts=split_gold_counts[split],
            confusion=split_confusion[split],
            path_mismatches=split_path_mismatches[split],
            title_policy_predictions=split_title_policy_predictions[split],
        )
        for split in splits
    }
    training_manifest = _authority_manifest(TRAINING_GOLD)
    holdout_manifest = _authority_manifest(HOLDOUT_GOLD)
    report = {
        "status": "passed",
        "evaluation_version": EVALUATION_VERSION,
        "engine_version": ENGINE_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        **overall,
        "splits": split_results,
        "authority": {
            "assignments": str(ASSIGNMENTS),
            "assignments_sha256": _sha256(ASSIGNMENTS),
            "training_gold": str(TRAINING_GOLD),
            "training_gold_manifest": str(training_manifest),
            "training_gold_manifest_sha256": _sha256(training_manifest),
            "holdout_gold": str(HOLDOUT_GOLD),
            "holdout_gold_manifest": str(holdout_manifest),
            "holdout_gold_manifest_sha256": _sha256(holdout_manifest),
            "scoped_identity_tickers": len(scoped_tickers),
            "monthly_identity_authority": identity_months,
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
