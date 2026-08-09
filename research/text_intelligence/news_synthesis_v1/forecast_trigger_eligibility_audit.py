from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import CONTRACT_VERSION, sha256_json
from .direct_trading_sentiment_audit import (
    article_source,
    build_benchmark_identity_snapshot,
    load_population,
)
from .engine import (
    ENGINE_VERSION,
    NewsSynthesisEngine,
    _normalize_ticker_identifier,
)
from .registry import ConceptRegistry
from .source_authority import sha256_file


AUDIT_VERSION = "news_synthesis_forecast_trigger_eligibility_audit_v1"
SPLIT_VERSION = "news_synthesis_forecast_trigger_split_v1"
CODE_AUTHORITY_FILES = (
    "engine.py",
    "synthesis.py",
    "facts.py",
    "concept_registry.json",
    "certification.py",
    "direct_trading_sentiment_audit.py",
    "forecast_trigger_eligibility_audit.py",
)


def generate_eligibility_audit(
    output_root: Path,
    *,
    population_ids: Iterable[str] | None = None,
    persist_prediction_documents: bool = True,
) -> dict[str, Any]:
    """Evaluate forecast-trigger eligibility on every certified issuer unit.

    The comparable population is defined only by the manually certified
    authority: every resolved issuer/security entity with a ticker. Prediction
    failures and missing prediction identities remain in the confusion matrix
    as a negative prediction and are also reported as coverage diagnostics.
    """
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite versioned audit: {output_root}")
    # Capture source authority before inference so a long-running audit cannot
    # accidentally hash files changed after this process loaded its code.
    code_authority = _code_authority()
    population = load_population(population_ids)
    reviewed_candidate_tickers = {
        str(value)
        for annotation in population.annotations.values()
        for value in annotation.get("candidate_tickers", ())
        if value
    }
    reviewed_entities = tuple(
        entity
        for document in population.certified_documents.values()
        for entity in document.get("entities", ())
        if entity.get("identity_status") == "resolved" and entity.get("ticker")
    )
    identity_index, identity_snapshot = build_benchmark_identity_snapshot(
        population.identity_articles,
        supplemental_tickers=reviewed_candidate_tickers,
        reviewed_entities=reviewed_entities,
    )
    engine = NewsSynthesisEngine(identity_index)
    output_root.mkdir(parents=True)
    prediction_root = output_root / "prediction_documents"
    if persist_prediction_documents:
        prediction_root.mkdir()

    unit_records: list[dict[str, Any]] = []
    article_records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    extra_prediction_units: list[dict[str, Any]] = []
    prediction_hashes: list[dict[str, str]] = []
    for sample_id in sorted(population.certified_ids):
        gold_document = population.certified_documents[sample_id]
        article = population.articles[sample_id]
        gold_units = certified_forecast_units(gold_document)
        gold_tickers = [str(row["ticker"]) for row in gold_units]
        try:
            prediction = engine.synthesize(
                article_source(article, additional_tickers=gold_tickers)
            )
        except Exception as exc:  # preserved as a scored negative plus failure
            prediction = {}
            failures.append(
                {"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"}
            )
        prediction_hash = sha256_json(prediction) if prediction else ""
        prediction_hashes.append(
            {"sample_id": sample_id, "sha256": prediction_hash}
        )
        if persist_prediction_documents:
            _write_json(prediction_root / f"{sample_id}.json", prediction)

        prediction_units = predicted_forecast_units(prediction)
        gold_ticker_set = {str(row["normalized_ticker"]) for row in gold_units}
        for ticker, rows in sorted(prediction_units.items()):
            if ticker in gold_ticker_set:
                continue
            for row in rows:
                extra_prediction_units.append(
                    {
                        "sample_id": sample_id,
                        "ticker": str(row.get("ticker") or ticker),
                        "normalized_ticker": ticker,
                        "predicted_forecast_eligible": bool(row["eligible"]),
                        "prediction_entity_id": str(row.get("entity_id") or ""),
                        "prediction_document_sha256": prediction_hash,
                    }
                )

        article_gold = any(bool(row["gold_forecast_eligible"]) for row in gold_units)
        article_predicted = any(
            bool(row["eligible"])
            for rows in prediction_units.values()
            for row in rows
        )
        article_status = _confusion_label(article_gold, article_predicted)
        article_records.append(
            {
                "sample_id": sample_id,
                "gold_forecast_eligible": article_gold,
                "predicted_forecast_eligible": article_predicted,
                "confusion": article_status,
                "comparable_issuer_units": len(gold_units),
                "prediction_document_sha256": prediction_hash,
                "engine_failure": not bool(prediction),
            }
        )
        for gold_unit in gold_units:
            ticker = str(gold_unit["normalized_ticker"])
            candidates = prediction_units.get(ticker, [])
            if len(candidates) == 1:
                candidate = candidates[0]
                predicted = bool(candidate["eligible"])
                scoring_status = "scored"
                prediction_entity_id = str(candidate.get("entity_id") or "")
                reasons = list(candidate.get("reasons") or ())
                blocking_flags = list(candidate.get("blocking_flags") or ())
            elif not candidates:
                predicted = False
                scoring_status = (
                    "engine_failure" if not prediction else "prediction_identity_unresolved"
                )
                prediction_entity_id = ""
                reasons = [scoring_status]
                blocking_flags = []
            else:
                predicted = False
                scoring_status = "prediction_identity_ambiguous"
                prediction_entity_id = ""
                reasons = [scoring_status]
                blocking_flags = []
            gold = bool(gold_unit["gold_forecast_eligible"])
            unit_records.append(
                {
                    **gold_unit,
                    "predicted_forecast_eligible": predicted,
                    "confusion": _confusion_label(gold, predicted),
                    "scoring_status": scoring_status,
                    "prediction_entity_id": prediction_entity_id,
                    "prediction_reasons": reasons,
                    "prediction_blocking_flags": blocking_flags,
                    "prediction_document_sha256": prediction_hash,
                }
            )

    unit_records.sort(key=lambda row: str(row["unit_id"]))
    article_records.sort(key=lambda row: str(row["sample_id"]))
    extra_prediction_units.sort(
        key=lambda row: (str(row["sample_id"]), str(row["normalized_ticker"]))
    )
    gold_document_hashes = [
        {
            "sample_id": sample_id,
            "sha256": sha256_json(population.certified_documents[sample_id]),
        }
        for sample_id in sorted(population.certified_ids)
    ]
    source_article_hashes = [
        {
            "sample_id": sample_id,
            "source_id": str(population.articles[sample_id].get("source_id") or ""),
            "source_text_sha256": str(
                population.articles[sample_id].get("source_text_sha256") or ""
            ),
        }
        for sample_id in sorted(population.certified_ids)
    ]
    certification_root = next(iter(population.certified_documents), None)
    del certification_root  # population presence is already validated fail-closed
    manifest = {
        "version": AUDIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "run_name": output_root.name,
        "selection": (
            "Every manually certified resolved issuer/security entity with a "
            "non-empty ticker; forecast_trigger eligibility is scored for the "
            "same normalized ticker in the current engine output."
        ),
        "prediction_failure_policy": (
            "Missing, ambiguous, or failed prediction identities score as "
            "forecast_trigger=false and are separately counted under coverage."
        ),
        "authority": {
            "contract_version": CONTRACT_VERSION,
            "engine_version": ENGINE_VERSION,
            "concept_registry_version": ConceptRegistry.load().version,
            "code_files": code_authority,
            "code_files_sha256": sha256_json(code_authority),
            "certified_ids_sha256": sha256_json(sorted(population.certified_ids)),
            "certified_documents_sha256": sha256_json(gold_document_hashes),
            "source_articles_sha256": sha256_json(source_article_hashes),
            "prediction_documents_sha256": sha256_json(prediction_hashes),
            "article_population_sha256": sha256_json(
                [row["sample_id"] for row in article_records]
            ),
            "issuer_unit_population_sha256": sha256_json(
                [
                    {
                        "unit_id": row["unit_id"],
                        "gold_forecast_eligible": row["gold_forecast_eligible"],
                    }
                    for row in unit_records
                ]
            ),
            "unit_records_sha256": sha256_json(unit_records),
            "article_records_sha256": sha256_json(article_records),
        },
        "population": {
            "certified_articles": len(population.certified_ids),
            "comparable_issuer_units": len(unit_records),
            "extra_prediction_units": len(extra_prediction_units),
        },
        "issuer_unit_metrics": binary_metrics(unit_records),
        "article_metrics": binary_metrics(article_records),
        "coverage": {
            "unit_scoring_status": dict(
                sorted(Counter(str(row["scoring_status"]) for row in unit_records).items())
            ),
            "unscorable_or_identity_units": sum(
                row["scoring_status"] != "scored" for row in unit_records
            ),
            "extra_prediction_units": len(extra_prediction_units),
            "extra_prediction_eligible_units": sum(
                bool(row["predicted_forecast_eligible"])
                for row in extra_prediction_units
            ),
        },
        "engine_failures": failures,
    }
    manifest["authority"]["manifest_inputs_sha256"] = sha256_json(
        {
            "authority": manifest["authority"],
            "population": manifest["population"],
        }
    )
    _write_json(output_root / "identity_snapshot.json", identity_snapshot)
    _write_json(output_root / "issuer_units.json", unit_records)
    _write_json(output_root / "articles.json", article_records)
    _write_json(output_root / "extra_prediction_units.json", extra_prediction_units)
    _write_json(output_root / "manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(
        render_summary(manifest), encoding="utf-8", newline="\n"
    )
    return manifest


def rescore_cached_predictions(
    source_audit_root: Path,
    output_root: Path,
    *,
    population_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Rescore immutable cached predictions against the current certified gold."""
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite versioned rescore: {output_root}")
    source_manifest = _load_json(source_audit_root / "manifest.json")
    population = load_population(population_ids)
    selected_ids = set(population.certified_ids)
    source_units = [
        row for row in _load_json(source_audit_root / "issuer_units.json")
        if str(row["sample_id"]) in selected_ids
    ]
    source_articles = {
        str(row["sample_id"]): row
        for row in _load_json(source_audit_root / "articles.json")
        if str(row["sample_id"]) in selected_ids
    }
    source_extra_units = [
        row for row in _load_json(source_audit_root / "extra_prediction_units.json")
        if str(row["sample_id"]) in selected_ids
    ]
    current_gold = {
        str(row["unit_id"]): row
        for sample_id in sorted(selected_ids)
        for row in certified_forecast_units(population.certified_documents[sample_id])
    }
    if {str(row["unit_id"]) for row in source_units} != set(current_gold):
        raise RuntimeError("Cached prediction population differs from current certified units")
    units = []
    by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prior in source_units:
        unit_id = str(prior["unit_id"])
        gold = current_gold[unit_id]
        row = {
            **prior,
            "gold_entity_id": gold["gold_entity_id"],
            "ticker": gold["ticker"],
            "normalized_ticker": gold["normalized_ticker"],
            "gold_forecast_eligible": gold["gold_forecast_eligible"],
        }
        row["confusion"] = _confusion_label(
            bool(row["gold_forecast_eligible"]),
            bool(row["predicted_forecast_eligible"]),
        )
        units.append(row)
        by_article[str(row["sample_id"])].append(row)
    units.sort(key=lambda row: str(row["unit_id"]))
    articles = []
    for sample_id in sorted(selected_ids):
        prior = source_articles[sample_id]
        gold = any(bool(row["gold_forecast_eligible"]) for row in by_article[sample_id])
        predicted = bool(prior["predicted_forecast_eligible"])
        articles.append({
            **prior,
            "gold_forecast_eligible": gold,
            "confusion": _confusion_label(gold, predicted),
        })
    gold_hashes = [
        {"sample_id": sample_id, "sha256": sha256_json(population.certified_documents[sample_id])}
        for sample_id in sorted(selected_ids)
    ]
    failures = [
        row for row in source_manifest.get("engine_failures", ())
        if str(row.get("sample_id")) in selected_ids
    ]
    manifest = {
        "version": AUDIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "run_name": output_root.name,
        "evaluation_mode": "cached_prediction_rescore",
        "authority": {
            "engine_version": source_manifest.get("authority", {}).get("engine_version"),
            "inference_code_files": source_manifest.get("authority", {}).get("code_files", []),
            "inference_code_files_sha256": source_manifest.get("authority", {}).get("code_files_sha256"),
            "source_prediction_documents_sha256": source_manifest.get("authority", {}).get("prediction_documents_sha256"),
            "selected_prediction_documents_sha256": sha256_json(sorted(
                [
                    {"sample_id": sample_id, "sha256": source_articles[sample_id].get("prediction_document_sha256", "")}
                    for sample_id in selected_ids
                ],
                key=lambda row: row["sample_id"],
            )),
            "source_audit_manifest_sha256": sha256_file(source_audit_root / "manifest.json"),
            "rescore_code_files": _code_authority(),
            "certified_documents_sha256": sha256_json(gold_hashes),
            "article_population_sha256": sha256_json(sorted(selected_ids)),
            "issuer_unit_population_sha256": sha256_json([
                {"unit_id": row["unit_id"], "gold_forecast_eligible": row["gold_forecast_eligible"]}
                for row in units
            ]),
            "unit_records_sha256": sha256_json(units),
            "article_records_sha256": sha256_json(articles),
        },
        "population": {
            "certified_articles": len(selected_ids),
            "comparable_issuer_units": len(units),
            "extra_prediction_units": len(source_extra_units),
        },
        "issuer_unit_metrics": binary_metrics(units),
        "article_metrics": binary_metrics(articles),
        "coverage": {
            "unit_scoring_status": dict(sorted(Counter(str(row["scoring_status"]) for row in units).items())),
            "unscorable_or_identity_units": sum(row["scoring_status"] != "scored" for row in units),
            "extra_prediction_units": len(source_extra_units),
            "extra_prediction_eligible_units": sum(bool(row["predicted_forecast_eligible"]) for row in source_extra_units),
        },
        "engine_failures": failures,
    }
    output_root.mkdir(parents=True)
    _write_json(output_root / "issuer_units.json", units)
    _write_json(output_root / "articles.json", articles)
    _write_json(output_root / "extra_prediction_units.json", source_extra_units)
    _write_json(output_root / "manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(render_summary(manifest), encoding="utf-8", newline="\n")
    return manifest


def certified_forecast_units(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    eligibility = {
        str(row.get("entity_id") or ""): bool(row.get("eligible"))
        for row in document.get("eligibility", ())
        if row.get("product") == "forecast_trigger"
    }
    units = []
    seen: set[str] = set()
    for entity in document.get("entities", ()):
        if entity.get("entity_kind") not in {"issuer", "security"}:
            continue
        if entity.get("identity_status") != "resolved" or not entity.get("ticker"):
            continue
        entity_id = str(entity.get("entity_id") or "")
        ticker = str(entity.get("ticker") or "")
        normalized = _normalize_ticker_identifier(ticker)
        if not normalized:
            raise RuntimeError(
                f"Resolved certified entity has invalid ticker: {document.get('sample_id')}/{entity_id}"
            )
        unit_id = f"{document['sample_id']}::{normalized}"
        if unit_id in seen:
            raise RuntimeError(f"Duplicate certified issuer unit: {unit_id}")
        seen.add(unit_id)
        units.append(
            {
                "unit_id": unit_id,
                "sample_id": str(document["sample_id"]),
                "gold_entity_id": entity_id,
                "ticker": ticker,
                "normalized_ticker": normalized,
                "gold_forecast_eligible": bool(eligibility.get(entity_id, False)),
            }
        )
    return sorted(units, key=lambda row: str(row["unit_id"]))


def predicted_forecast_units(
    document: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    entity_by_id = {
        str(row.get("entity_id") or ""): row
        for row in document.get("entities", ())
        if row.get("entity_id")
    }
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in document.get("eligibility", ()):
        if row.get("product") != "forecast_trigger":
            continue
        entity_id = str(row.get("entity_id") or "")
        entity = entity_by_id.get(entity_id, {})
        ticker = str(entity.get("ticker") or "")
        normalized = _normalize_ticker_identifier(ticker)
        if not normalized:
            continue
        output[normalized].append(
            {
                "entity_id": entity_id,
                "ticker": ticker,
                "eligible": bool(row.get("eligible")),
                "reasons": list(row.get("reasons") or ()),
                "blocking_flags": list(row.get("blocking_flags") or ()),
            }
        )
    return dict(output)


def binary_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["confusion"]) for row in records)
    tp, fn = counts["TP"], counts["FN"]
    fp, tn = counts["FP"], counts["TN"]
    total = tp + fn + fp + tn
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    return {
        "total": total,
        "gold_positive": tp + fn,
        "gold_negative": fp + tn,
        "predicted_positive": tp + fp,
        "predicted_negative": fn + tn,
        "confusion": {"TP": tp, "FN": fn, "FP": fp, "TN": tn},
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": _ratio(2 * precision * recall, precision + recall),
        "balanced_accuracy": (recall + specificity) / 2,
        "raw_accuracy": _ratio(tp + tn, total),
    }


def create_frozen_split(
    audit_root: Path,
    output_root: Path,
    *,
    sealed_fraction: float = 0.30,
) -> dict[str, Any]:
    """Freeze a deterministic article-grouped split stratified by error class."""
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite frozen split: {output_root}")
    manifest = _load_json(audit_root / "manifest.json")
    articles = _load_json(audit_root / "articles.json")
    units = _load_json(audit_root / "issuer_units.json")
    if not 0.0 < sealed_fraction < 1.0:
        raise ValueError("sealed_fraction must be between zero and one")
    by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in units:
        by_article[str(row["sample_id"])].append(row)
    strata: dict[str, list[str]] = defaultdict(list)
    for article in articles:
        sample_id = str(article["sample_id"])
        labels = {str(row["confusion"]) for row in by_article[sample_id]}
        if "FN" in labels and "FP" in labels:
            stratum = "FN_FP"
        elif "FN" in labels:
            stratum = "FN"
        elif "FP" in labels:
            stratum = "FP"
        else:
            stratum = "NO_MISMATCH"
        strata[stratum].append(sample_id)
    sealed_ids: set[str] = set()
    stratum_summary: dict[str, dict[str, int]] = {}
    for stratum, sample_ids in sorted(strata.items()):
        ordered = sorted(
            sample_ids,
            key=lambda value: hashlib.sha256(
                f"{SPLIT_VERSION}:{stratum}:{value}".encode("utf-8")
            ).hexdigest(),
        )
        sealed_count = round(len(ordered) * sealed_fraction)
        if stratum in {"FN", "FP", "FN_FP"} and ordered:
            sealed_count = min(len(ordered), max(1, sealed_count))
        selected = ordered[:sealed_count]
        sealed_ids.update(selected)
        stratum_summary[stratum] = {
            "articles": len(ordered),
            "audit_articles": len(ordered) - len(selected),
            "sealed_articles": len(selected),
        }
    article_ids = {str(row["sample_id"]) for row in articles}
    audit_ids = article_ids - sealed_ids
    if audit_ids & sealed_ids or audit_ids | sealed_ids != article_ids:
        raise RuntimeError("Frozen article partitions are not disjoint and complete")
    audit_units = [row for row in units if str(row["sample_id"]) in audit_ids]
    sealed_units = [row for row in units if str(row["sample_id"]) in sealed_ids]
    output_root.mkdir(parents=True)
    audit_doc = _partition_document("audit", audit_ids, audit_units)
    sealed_doc = _partition_document("sealed_test", sealed_ids, sealed_units)
    _write_json(output_root / "audit_partition.json", audit_doc)
    _write_json(output_root / "sealed_test_partition.json", sealed_doc)
    split_manifest = {
        "version": SPLIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_audit_root": str(audit_root.resolve()),
        "source_audit_version": str(manifest.get("version") or ""),
        "source_engine_version": str(
            manifest.get("authority", {}).get("engine_version") or ""
        ),
        "prediction_blind": False,
        "development_case_content_read": False,
        "sealed_case_content_read": False,
        "grouping_key": "sample_id",
        "method": (
            "Deterministic SHA-256 ordering within article-level FN, FP, "
            "FN+FP, and no-mismatch strata; 30 percent sealed per stratum."
        ),
        "strata": stratum_summary,
        "audit": _partition_summary(audit_doc),
        "sealed_test": _partition_summary(sealed_doc),
        "authority": {
            "source_manifest_sha256": sha256_file(audit_root / "manifest.json"),
            "source_article_records_sha256": sha256_json(articles),
            "source_unit_records_sha256": sha256_json(units),
            "article_partition_sha256": sha256_json(
                {"audit": sorted(audit_ids), "sealed_test": sorted(sealed_ids)}
            ),
            "audit_partition_sha256": sha256_json(audit_doc),
            "sealed_test_partition_sha256": sha256_json(sealed_doc),
        },
    }
    _write_json(output_root / "split_manifest.json", split_manifest)
    return split_manifest


def rebind_frozen_split(
    source_split_root: Path,
    audit_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Bind an existing frozen article partition to an identical new population."""
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite rebound split: {output_root}")
    prior_manifest = _load_json(source_split_root / "split_manifest.json")
    audit_manifest = _load_json(audit_root / "manifest.json")
    articles = _load_json(audit_root / "articles.json")
    units = _load_json(audit_root / "issuer_units.json")
    prior_audit = _load_json(source_split_root / "audit_partition.json")
    prior_sealed = _load_json(source_split_root / "sealed_test_partition.json")
    article_ids = {str(row["sample_id"]) for row in articles}
    unit_ids = {str(row["unit_id"]) for row in units}
    prior_audit_articles = set(prior_audit["article_ids"])
    prior_audit_units = set(prior_audit["unit_ids"])
    partition_articles = prior_audit_articles | set(prior_sealed["article_ids"])
    partition_units = prior_audit_units | set(prior_sealed["unit_ids"])
    if set(prior_audit["article_ids"]) & set(prior_sealed["article_ids"]):
        raise RuntimeError("Source frozen split has overlapping articles")
    full_population = partition_articles == article_ids and partition_units == unit_ids
    audit_population = prior_audit_articles == article_ids and prior_audit_units == unit_ids
    if not full_population and not audit_population:
        raise RuntimeError("New audit population is not identical to the frozen split")
    output_root.mkdir(parents=True)
    _write_json(output_root / "audit_partition.json", prior_audit)
    _write_json(output_root / "sealed_test_partition.json", prior_sealed)
    rebound = {
        **prior_manifest,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_audit_root": str(audit_root.resolve()),
        "source_audit_version": str(audit_manifest.get("version") or ""),
        "source_engine_version": str(audit_manifest.get("authority", {}).get("engine_version") or ""),
        "method": "Exact reuse of the previously frozen article and issuer-unit IDs; no repartitioning.",
        "inherited_from_split_root": str(source_split_root.resolve()),
        "population_scope": "full" if full_population else "audit_development_only",
        "authority": {
            "source_manifest_sha256": sha256_file(audit_root / "manifest.json"),
            "source_article_records_sha256": sha256_json(articles),
            "source_unit_records_sha256": sha256_json(units),
            "article_partition_sha256": sha256_json({
                "audit": sorted(prior_audit["article_ids"]),
                "sealed_test": sorted(prior_sealed["article_ids"]),
            }),
            "audit_partition_sha256": sha256_json(prior_audit),
            "sealed_test_partition_sha256": sha256_json(prior_sealed),
            "inherited_split_manifest_sha256": sha256_file(source_split_root / "split_manifest.json"),
        },
    }
    _write_json(output_root / "split_manifest.json", rebound)
    return rebound


def evaluate_audit_partition(
    audit_root: Path,
    partition_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Persist metrics for one already-frozen partition without rerunning inference."""
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite partition evaluation: {output_root}")
    audit_manifest = _load_json(audit_root / "manifest.json")
    partition = _load_json(partition_path)
    article_ids = {str(value) for value in partition["article_ids"]}
    unit_ids = {str(value) for value in partition["unit_ids"]}
    articles = [
        row for row in _load_json(audit_root / "articles.json")
        if str(row["sample_id"]) in article_ids
    ]
    units = [
        row for row in _load_json(audit_root / "issuer_units.json")
        if str(row["unit_id"]) in unit_ids
    ]
    if {str(row["sample_id"]) for row in articles} != article_ids:
        raise RuntimeError("Partition article IDs are not present in the audit")
    if {str(row["unit_id"]) for row in units} != unit_ids:
        raise RuntimeError("Partition unit IDs are not present in the audit")
    result = {
        "version": "news_synthesis_forecast_trigger_partition_evaluation_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "partition": str(partition.get("partition") or partition_path.stem),
        "engine_version": audit_manifest.get("authority", {}).get("engine_version"),
        "issuer_unit_metrics": binary_metrics(units),
        "article_metrics": binary_metrics(articles),
        "coverage": {
            "unit_scoring_status": dict(sorted(Counter(str(row["scoring_status"]) for row in units).items())),
            "unscorable_or_identity_units": sum(row["scoring_status"] != "scored" for row in units),
        },
        "engine_failures": [
            row for row in audit_manifest.get("engine_failures", ())
            if str(row.get("sample_id")) in article_ids
        ],
        "authority": {
            "audit_manifest_sha256": sha256_file(audit_root / "manifest.json"),
            "partition_sha256": sha256_file(partition_path),
            "article_records_sha256": sha256_json(articles),
            "unit_records_sha256": sha256_json(units),
        },
    }
    output_root.mkdir(parents=True)
    _write_json(output_root / "articles.json", articles)
    _write_json(output_root / "issuer_units.json", units)
    _write_json(output_root / "manifest.json", result)
    return result


def compare_eligibility_audits(
    previous_root: Path,
    current_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Compare predictions on an identical certified population and persist every change."""
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite audit comparison: {output_root}")
    previous_manifest = _load_json(previous_root / "manifest.json")
    current_manifest = _load_json(current_root / "manifest.json")
    previous_units = {str(row["unit_id"]): row for row in _load_json(previous_root / "issuer_units.json")}
    current_units = {str(row["unit_id"]): row for row in _load_json(current_root / "issuer_units.json")}
    previous_articles = {str(row["sample_id"]): row for row in _load_json(previous_root / "articles.json")}
    current_articles = {str(row["sample_id"]): row for row in _load_json(current_root / "articles.json")}
    if previous_units.keys() != current_units.keys() or previous_articles.keys() != current_articles.keys():
        raise RuntimeError("Audit comparison populations differ")
    gold_changes = [
        unit_id for unit_id in previous_units
        if bool(previous_units[unit_id]["gold_forecast_eligible"])
        != bool(current_units[unit_id]["gold_forecast_eligible"])
    ]
    if gold_changes:
        raise RuntimeError(f"Gold changed inside engine comparison: {gold_changes[:10]}")
    changed_units = []
    for unit_id in sorted(previous_units):
        before, after = previous_units[unit_id], current_units[unit_id]
        if (
            bool(before["predicted_forecast_eligible"]) == bool(after["predicted_forecast_eligible"])
            and str(before["scoring_status"]) == str(after["scoring_status"])
        ):
            continue
        changed_units.append({
            "unit_id": unit_id,
            "sample_id": after["sample_id"],
            "ticker": after["ticker"],
            "gold_forecast_eligible": after["gold_forecast_eligible"],
            "previous_predicted_forecast_eligible": before["predicted_forecast_eligible"],
            "current_predicted_forecast_eligible": after["predicted_forecast_eligible"],
            "previous_confusion": before["confusion"],
            "current_confusion": after["confusion"],
            "previous_scoring_status": before["scoring_status"],
            "current_scoring_status": after["scoring_status"],
            "previous_reasons": before.get("prediction_reasons", []),
            "current_reasons": after.get("prediction_reasons", []),
        })
    previous_unit_metrics = binary_metrics(list(previous_units.values()))
    current_unit_metrics = binary_metrics(list(current_units.values()))
    previous_article_metrics = binary_metrics(list(previous_articles.values()))
    current_article_metrics = binary_metrics(list(current_articles.values()))
    result = {
        "version": "news_synthesis_forecast_trigger_audit_comparison_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "previous_engine_version": _manifest_engine_version(previous_manifest),
        "current_engine_version": _manifest_engine_version(current_manifest),
        "population": {"articles": len(current_articles), "issuer_units": len(current_units)},
        "issuer_unit_metrics": _metric_comparison(previous_unit_metrics, current_unit_metrics),
        "article_metrics": _metric_comparison(previous_article_metrics, current_article_metrics),
        "prediction_changes": {
            "total": len(changed_units),
            "errors_fixed": sum(row["previous_confusion"] in {"FN", "FP"} and row["current_confusion"] in {"TP", "TN"} for row in changed_units),
            "errors_introduced": sum(row["previous_confusion"] in {"TP", "TN"} and row["current_confusion"] in {"FN", "FP"} for row in changed_units),
            "identity_or_coverage_changes": sum(row["previous_scoring_status"] != row["current_scoring_status"] for row in changed_units),
            "gold_changes": 0,
        },
        "authority": {
            "previous_manifest_sha256": sha256_file(previous_root / "manifest.json"),
            "current_manifest_sha256": sha256_file(current_root / "manifest.json"),
            "changed_units_sha256": sha256_json(changed_units),
        },
    }
    output_root.mkdir(parents=True)
    _write_json(output_root / "changed_units.json", changed_units)
    _write_json(output_root / "manifest.json", result)
    return result


def _manifest_engine_version(manifest: Mapping[str, Any]) -> str:
    return str(manifest.get("engine_version") or manifest.get("authority", {}).get("engine_version") or "")


def _metric_comparison(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    score_names = ("precision", "recall", "specificity", "f1", "balanced_accuracy", "raw_accuracy")
    return {
        "previous": previous,
        "current": current,
        "confusion_delta": {
            key: int(current["confusion"][key]) - int(previous["confusion"][key])
            for key in ("TP", "FN", "FP", "TN")
        },
        "score_delta": {key: float(current[key]) - float(previous[key]) for key in score_names},
    }


def generate_audit_packets(
    audit_root: Path,
    split_root: Path,
    output_root: Path,
    *,
    batch_size: int = 20,
) -> dict[str, Any]:
    """Materialize complete mismatch packets for the development partition only."""
    if output_root.exists():
        raise RuntimeError(f"Refusing to overwrite audit packets: {output_root}")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    audit_manifest = _load_json(audit_root / "manifest.json")
    split_manifest = _load_json(split_root / "split_manifest.json")
    if (
        split_manifest.get("authority", {}).get("source_manifest_sha256")
        != sha256_file(audit_root / "manifest.json")
    ):
        raise RuntimeError("Frozen split does not match the supplied audit manifest")
    partition = _load_json(split_root / "audit_partition.json")
    audit_unit_ids = {str(value) for value in partition["unit_ids"]}
    records = _load_json(audit_root / "issuer_units.json")
    selected = [
        row
        for row in records
        if str(row["unit_id"]) in audit_unit_ids
        and str(row["confusion"]) in {"FN", "FP"}
    ]
    selected.sort(key=lambda row: str(row["unit_id"]))
    selected_ids = {str(row["sample_id"]) for row in selected}
    population = load_population(selected_ids)
    output_root.mkdir(parents=True)
    index: list[dict[str, Any]] = []
    for row in selected:
        sample_id = str(row["sample_id"])
        prediction = _load_json(
            audit_root / "prediction_documents" / f"{sample_id}.json"
        )
        gold_document = population.certified_documents[sample_id]
        article = population.articles[sample_id]
        packet = render_eligibility_packet(
            row,
            article,
            gold_document,
            prediction,
            engine_version=_manifest_engine_version(audit_manifest),
        )
        folder = (
            "identity_or_coverage_issue"
            if row.get("scoring_status") != "scored"
            else "false_negative"
            if row["confusion"] == "FN"
            else "false_positive"
        )
        relative = Path(folder) / f"{sample_id}__{_safe_path(row['ticker'])}.md"
        target = output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(packet, encoding="utf-8", newline="\n")
        index.append(
            {
                "unit_id": str(row["unit_id"]),
                "sample_id": sample_id,
                "ticker": str(row["ticker"]),
                "confusion": str(row["confusion"]),
                "scoring_status": str(row["scoring_status"]),
                "relative_path": relative.as_posix(),
                "packet_sha256": hashlib.sha256(packet.encode("utf-8")).hexdigest(),
            }
        )
    batches = [
        {
            "batch_id": f"B{batch_index + 1:03d}",
            "units": batch,
        }
        for batch_index, batch in enumerate(
            [index[offset : offset + batch_size] for offset in range(0, len(index), batch_size)]
        )
    ]
    packet_manifest = {
        "version": "news_synthesis_forecast_trigger_packet_set_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "partition": "audit",
        "sealed_test_read": False,
        "engine_version": audit_manifest.get("authority", {}).get("engine_version"),
        "population": {
            "packets": len(index),
            "false_negative": sum(row["confusion"] == "FN" for row in index),
            "false_positive": sum(row["confusion"] == "FP" for row in index),
            "identity_or_coverage_issue": sum(
                row["scoring_status"] != "scored" for row in index
            ),
            "batches": len(batches),
        },
        "authority": {
            "audit_manifest_sha256": sha256_file(audit_root / "manifest.json"),
            "split_manifest_sha256": sha256_file(split_root / "split_manifest.json"),
            "audit_partition_sha256": sha256_file(
                split_root / "audit_partition.json"
            ),
            "packet_index_sha256": sha256_json(index),
            "packet_set_sha256": sha256_json(
                [
                    {"relative_path": row["relative_path"], "sha256": row["packet_sha256"]}
                    for row in index
                ]
            ),
        },
    }
    _write_json(output_root / "packet_index.json", index)
    _write_json(output_root / "review_batches.json", batches)
    _write_json(output_root / "manifest.json", packet_manifest)
    return packet_manifest


def render_eligibility_packet(
    record: Mapping[str, Any],
    article: Mapping[str, Any],
    gold_document: Mapping[str, Any],
    prediction: Mapping[str, Any],
    *,
    engine_version: str,
) -> str:
    gold_entity_id = str(record["gold_entity_id"])
    prediction_entity_id = str(record.get("prediction_entity_id") or "")
    gold_context = _entity_context(gold_document, gold_entity_id)
    prediction_context = _entity_context(prediction, prediction_entity_id)
    publication = article.get("publication", {})
    rendered = article.get("rendered_product", {})
    source_metadata = {
        "title": publication.get("title"),
        "timestamp": article.get("source_timestamp"),
        "author": publication.get("author"),
        "provider": publication.get("provider"),
        "article_url": publication.get("article_url"),
        "url_domain": publication.get("url_domain"),
        "provider_tickers": publication.get("provider_tickers", []),
        "channels": publication.get("channels", []),
        "provider_tags": publication.get("provider_tags", []),
        "content_quality_flags": publication.get("content_quality_flags", []),
        "source_text_sha256": article.get("source_text_sha256"),
        "render_quality_flags": rendered.get("quality_flags", []),
    }
    comparison = {
        key: record.get(key)
        for key in (
            "unit_id",
            "sample_id",
            "ticker",
            "gold_forecast_eligible",
            "predicted_forecast_eligible",
            "confusion",
            "scoring_status",
        )
    }
    decision_trace = eligibility_gate_trace(prediction, prediction_entity_id)
    return (
        f"# Forecast-trigger {record['confusion']}: {record['sample_id']} / {record['ticker']}\n\n"
        "## Unit and decision\n\n```json\n"
        + json.dumps(comparison, indent=2, ensure_ascii=False)
        + "\n```\n\n## Source metadata\n\n```json\n"
        + json.dumps(source_metadata, indent=2, ensure_ascii=False)
        + "\n```\n\n## Rendered source\n\n"
        + str(rendered.get("text") or "")
        + "\n\n## Certified gold entity and eligibility\n\n```json\n"
        + json.dumps(gold_context, indent=2, ensure_ascii=False)
        + f"\n```\n\n## {engine_version} predicted entity, statements, and participations\n\n```json\n"
        + json.dumps(prediction_context, indent=2, ensure_ascii=False)
        + f"\n```\n\n## {engine_version} forecast-trigger gate trace\n\n```json\n"
        + json.dumps(decision_trace, indent=2, ensure_ascii=False)
        + "\n```\n"
    )


def eligibility_gate_trace(
    document: Mapping[str, Any], entity_id: str
) -> dict[str, Any]:
    entities = {
        str(row.get("entity_id") or ""): row
        for row in document.get("entities", ())
    }
    entity = entities.get(entity_id, {})
    statements = {
        str(row.get("statement_id") or ""): row
        for row in document.get("statements", ())
    }
    participations = [
        row
        for row in document.get("participations", ())
        if str(row.get("entity_id") or "") == entity_id
    ]
    substantive = [
        statements[str(row.get("statement_id") or "")]
        for row in participations
        if str(row.get("statement_id") or "") in statements
        and statements[str(row.get("statement_id") or "")].get("statement_kind")
        in {"event", "assessment", "forecast", "background"}
        and row.get("semantic_role") != "none"
    ]
    current_event = any(
        (row.get("statement_kind") == "event" and row.get("time_relation") == "current")
        or (
            row.get("statement_kind") == "forecast"
            and row.get("concept_leaf") == "guidance.issued"
            and row.get("time_relation") == "forward"
        )
        for row in substantive
    )
    implication = any(
        row.get("semantic_sentiment") in {"positive", "negative"}
        for row in participations
    )
    flags = set(document.get("quality_flags", ()))
    blocking = {
        "invalid_text",
        "unrendered_text",
        "ambiguous_identity",
        "unresolved_identity",
    }
    envelope = document.get("envelope", {})
    purpose = envelope.get("communication_purpose", {}).get("value")
    origin = envelope.get("information_origin", {}).get("value")
    eligibility = next(
        (
            row
            for row in document.get("eligibility", ())
            if str(row.get("entity_id") or "") == entity_id
            and row.get("product") == "forecast_trigger"
        ),
        {},
    )
    return {
        "identity_ok": entity.get("identity_status") == "resolved",
        "tradable_security": entity.get("entity_kind") == "security"
        and bool(str(entity.get("ticker") or "").strip()),
        "evidence_ok": bool(substantive) and not bool(flags & blocking),
        "current_event_or_forward_guidance": current_event,
        "positive_or_negative_implication": implication,
        "communication_purpose": purpose,
        "purpose_is_report": purpose == "report",
        "information_origin": origin,
        "origin_is_non_analyst": origin != "analyst",
        "eligible": bool(eligibility.get("eligible", False)),
        "exact_engine_reasons": list(eligibility.get("reasons") or ()),
        "blocking_flags": list(eligibility.get("blocking_flags") or ()),
    }


def _entity_context(document: Mapping[str, Any], entity_id: str) -> dict[str, Any]:
    entity = next(
        (
            row
            for row in document.get("entities", ())
            if str(row.get("entity_id") or "") == entity_id
        ),
        None,
    )
    participations = [
        row
        for row in document.get("participations", ())
        if str(row.get("entity_id") or "") == entity_id
    ]
    statement_ids = {str(row.get("statement_id") or "") for row in participations}
    statements = [
        row
        for row in document.get("statements", ())
        if str(row.get("statement_id") or "") in statement_ids
    ]
    return {
        "entity": entity,
        "issuer_view": next(
            (
                row
                for row in document.get("issuer_views", ())
                if str(row.get("entity_id") or "") == entity_id
            ),
            None,
        ),
        "forecast_trigger_eligibility": next(
            (
                row
                for row in document.get("eligibility", ())
                if str(row.get("entity_id") or "") == entity_id
                and row.get("product") == "forecast_trigger"
            ),
            None,
        ),
        "statements": statements,
        "participations": participations,
        "envelope": document.get("envelope", {}),
        "quality_flags": document.get("quality_flags", []),
    }


def _partition_document(
    name: str, article_ids: set[str], units: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "version": SPLIT_VERSION,
        "partition": name,
        "article_ids": sorted(article_ids),
        "unit_ids": sorted(str(row["unit_id"]) for row in units),
    }


def _partition_summary(document: Mapping[str, Any]) -> dict[str, int]:
    return {
        "articles": len(document["article_ids"]),
        "issuer_units": len(document["unit_ids"]),
    }


def render_summary(manifest: Mapping[str, Any]) -> str:
    unit = manifest["issuer_unit_metrics"]
    article = manifest["article_metrics"]
    return (
        "# Forecast-trigger eligibility audit\n\n"
        f"- Engine: `{manifest['authority']['engine_version']}`\n"
        f"- Certified articles: {manifest['population']['certified_articles']:,}\n"
        f"- Comparable issuer units: {unit['total']:,}\n"
        f"- Issuer-unit TP/FN/FP/TN: {unit['confusion']['TP']:,} / "
        f"{unit['confusion']['FN']:,} / {unit['confusion']['FP']:,} / "
        f"{unit['confusion']['TN']:,}\n"
        f"- Issuer-unit precision/recall/F1: {unit['precision']:.4%} / "
        f"{unit['recall']:.4%} / {unit['f1']:.4%}\n"
        f"- Issuer-unit specificity/balanced accuracy/raw accuracy: "
        f"{unit['specificity']:.4%} / {unit['balanced_accuracy']:.4%} / "
        f"{unit['raw_accuracy']:.4%}\n"
        f"- Article TP/FN/FP/TN: {article['confusion']['TP']:,} / "
        f"{article['confusion']['FN']:,} / {article['confusion']['FP']:,} / "
        f"{article['confusion']['TN']:,}\n"
        f"- Unscorable or identity-coverage units: "
        f"{manifest['coverage']['unscorable_or_identity_units']:,}\n"
        f"- Engine failures: {len(manifest['engine_failures']):,}\n"
    )


def _code_authority() -> list[dict[str, str]]:
    root = Path(__file__).parent
    return [
        {"path": name, "sha256": sha256_file(root / name)}
        for name in CODE_AUTHORITY_FILES
    ]


def _confusion_label(gold: bool, predicted: bool) -> str:
    if gold:
        return "TP" if predicted else "FN"
    return "FP" if predicted else "TN"


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_path(value: Any) -> str:
    safe = "".join(
        character if character.isalnum() or character in {".", "-", "_"} else "_"
        for character in str(value)
    ).strip("_")
    return safe or "unknown"
