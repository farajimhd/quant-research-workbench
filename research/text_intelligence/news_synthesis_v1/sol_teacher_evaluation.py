from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .contracts import CONTRACT_VERSION, canonical_json, sha256_json, validate_document
from .direct_trading_sentiment_audit import (
    article_source,
    build_benchmark_identity_snapshot,
)
from .engine import (
    ENGINE_VERSION,
    IssuerIdentity,
    IssuerIdentityIndex,
    NewsSynthesisEngine,
    _normalize_ticker_identifier,
)
from .facts import extract_typed_facts
from .registry import ConceptRegistry
from .synthesis import derive_issuer_views, derive_synthesis


CONVERSION_VERSION = "news_synthesis_sol_teacher_conversion_v2"
EVALUATION_VERSION = "news_synthesis_sol_teacher_direction_evaluation_v1"
CONCEPT_REGISTRY_VERSION = ConceptRegistry.load().version

SOL_CONCEPT_TO_V1 = {
    "accounting_audit": "unclassified.semantic_claim",
    "analyst_action": "analyst.rating_action",
    "capital_return": "capital.return",
    "capital_structure": "capital.structure",
    "clinical": "clinical.trial_result",
    "contract_order": "commercial.contract",
    "credit_solvency": "credit.solvency",
    "earnings": "earnings.performance",
    "financing": "capital.financing",
    "guidance": "guidance.issued",
    "legal": "legal.proceeding",
    "listing_market_structure": "listing.market_structure",
    "ma_transaction": "corporate_transaction.acquisition",
    "management_governance": "governance.management_change",
    "market_reaction": "market.price_move_observed",
    "operations": "operations.business_update",
    "ownership": "ownership.position_change",
    "product_commercial": "product.milestone",
    "regulatory": "regulatory.action",
    "strategy_valuation": "strategy.valuation_assessment",
}

CONTENT_PURPOSE = {
    "analyst_event": "analyze",
    "automated_summary": "recap",
    "editorial_analysis": "analyze",
    "market_roundup": "recap",
    "mover_recap": "recap",
    "preview": "preview",
    "primary_event": "report",
    "regulatory_event": "report",
    "why_moving_followup": "explain_move",
}

SOURCE_ORIGIN = {
    "analyst_research": "analyst",
    "automated_summary": "editorial",
    "editorial_aggregation": "editorial",
    "editorial_original": "editorial",
    "issuer_direct": "issuer",
    "regulatory_primary": "regulator",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def convert_sol_teacher_label(
    article: Mapping[str, Any],
    teacher_label: Mapping[str, Any],
) -> dict[str, Any]:
    """Convert one coarse Sol label to an explicitly unreviewed V1 document.

    Sol direction, issuer scope, concepts, and eligibility are preserved. The
    source-bound evidence is deterministic but coarse because the teacher did
    not return atomic evidence spans; migration provenance therefore remains
    ``review_required`` and must never be presented as human certification.
    """
    sample_id = str(article["sample_id"])
    if str(teacher_label.get("sample_id")) != sample_id:
        raise RuntimeError(f"Sol teacher identity mismatch for {sample_id}")
    if str(teacher_label.get("source_id")) != str(article.get("source_id")):
        raise RuntimeError(f"Sol teacher source mismatch for {sample_id}")
    registry = ConceptRegistry.load()
    units = list(teacher_label.get("labels", ()))
    if str(teacher_label.get("extraction_decision")) == "labeled" and not units:
        raise RuntimeError(f"Labeled Sol teacher article has no units: {sample_id}")
    if str(teacher_label.get("extraction_decision")) != "labeled" and units:
        raise RuntimeError(f"Rejected Sol teacher article has units: {sample_id}")

    candidates = {
        str(row.get("canonical_instrument_id") or "").upper(): row
        for row in article.get("point_in_time_issuer_candidates", ())
        if row.get("canonical_instrument_id")
    }
    entities: list[dict[str, Any]] = []
    statements: list[dict[str, Any]] = []
    participations: list[dict[str, Any]] = []
    eligibility: list[dict[str, Any]] = []
    seen_units: set[str] = set()
    statement_index = 0

    for unit in units:
        canonical_id = str(unit.get("canonical_instrument_id") or "").upper()
        if not canonical_id or canonical_id in seen_units:
            raise RuntimeError(
                f"Invalid or duplicate Sol instrument in {sample_id}: {canonical_id!r}"
            )
        seen_units.add(canonical_id)
        candidate = candidates.get(canonical_id)
        if candidate is None:
            raise RuntimeError(
                f"Sol instrument is absent from candidate authority in {sample_id}: {canonical_id}"
            )
        ticker = str(candidate.get("display_symbol") or canonical_id).upper()
        entity_id = f"sol-teacher-security:{canonical_id}"
        aliases = _candidate_aliases(candidate)
        entities.append(
            {
                "entity_id": entity_id,
                "entity_kind": "security",
                "display_name": max(aliases, key=len) if aliases else ticker,
                "ticker": ticker,
                "identity_status": "resolved",
                "identity_evidence": sorted(
                    {
                        f"canonical_instrument_id:{canonical_id}",
                        *(
                            str(value)
                            for value in candidate.get("identity_evidence", ())
                            if value
                        ),
                    }
                ),
            }
        )
        classification = unit.get("classification", {})
        direction = str(classification.get("semantic_direction") or "")
        if direction not in {"positive", "negative", "neutral", "mixed"}:
            raise RuntimeError(f"Invalid Sol direction in {sample_id}/{canonical_id}")
        sol_concepts = tuple(
            dict.fromkeys(str(value) for value in classification.get("event_concepts", ()))
        )
        if not sol_concepts:
            raise RuntimeError(f"Sol unit has no concepts in {sample_id}/{canonical_id}")
        evidence = _issuer_evidence(article, candidate)
        teacher_participation_directions = (
            ("positive", "negative") if direction == "mixed" else (direction,)
        )
        mapped_concepts: list[str] = []
        for sol_concept in sol_concepts:
            concept = SOL_CONCEPT_TO_V1.get(sol_concept)
            if concept is None or not registry.contains(concept):
                raise RuntimeError(
                    f"Unmapped Sol concept in {sample_id}/{canonical_id}: {sol_concept}"
                )
            mapped_concepts.append(concept)
            # V1 treats observed price movement as a neutral observation.  It
            # cannot inherit a directional forecast label from the coarse Sol
            # taxonomy or create forecast eligibility by itself.
            participation_directions = (
                ("neutral",)
                if concept == "market.price_move_observed"
                else teacher_participation_directions
            )
            for participation_direction in participation_directions:
                statement_index += 1
                statement_id = f"S{statement_index:05d}"
                statement = {
                    "statement_id": statement_id,
                    "statement_kind": _statement_kind(concept),
                    "concept_leaf": concept,
                    "epistemic_status": "expected" if concept == "guidance.issued" else "confirmed",
                    "time_relation": "forward" if concept == "guidance.issued" else "current",
                    "evidence_spans": [evidence],
                    "typed_facts": extract_typed_facts((evidence,)),
                }
                statements.append(statement)
                participations.append(
                    {
                        "statement_id": statement_id,
                        "entity_id": entity_id,
                        "semantic_role": "affected_subject",
                        "discourse_role": "none",
                        "semantic_sentiment": participation_direction,
                        "sentiment_strength": (
                            0 if participation_direction == "neutral" else 2
                        ),
                    }
                )
        analyst_eligible = (
            str(teacher_label.get("content_role")) == "analyst_event"
            or str(teacher_label.get("source_origin")) == "analyst_research"
            or "analyst_action" in sol_concepts
        )
        has_directional_concept = any(
            concept != "market.price_move_observed" for concept in mapped_concepts
        )
        flags = {
            "forecast_trigger": bool(unit.get("forecast_trigger_eligible"))
            and has_directional_concept,
            "reaction_study": bool(unit.get("reaction_evaluation_eligible")),
            "issuer_history": bool(unit.get("issuer_history_context_eligible")),
            "analyst_evaluation": analyst_eligible,
        }
        eligibility.extend(
            {
                "entity_id": entity_id,
                "product": product,
                "eligible": eligible,
                "policy_id": f"sol_teacher_conversion_policy_v2:{product}",
                "reasons": [
                    (
                        f"eligible_under:{product}"
                        if eligible
                        else f"ineligible_under:{product}"
                    )
                ],
                "blocking_flags": (
                    ["market_observation_only"]
                    if product == "forecast_trigger"
                    and bool(unit.get("forecast_trigger_eligible"))
                    and not has_directional_concept
                    else []
                ),
            }
            for product, eligible in flags.items()
        )

    issuer_views = derive_issuer_views(
        entities,
        participations,
        statements=statements,
    )
    expected_directions = {}
    for unit in units:
        concepts = {
            SOL_CONCEPT_TO_V1[str(value)]
            for value in unit["classification"]["event_concepts"]
        }
        expected_directions[
            f"sol-teacher-security:{str(unit['canonical_instrument_id']).upper()}"
        ] = (
            str(unit["classification"]["semantic_direction"])
            if any(concept != "market.price_move_observed" for concept in concepts)
            else "neutral"
        )
    for view in issuer_views:
        direction = expected_directions[str(view["entity_id"])]
        view["composite_sentiment"] = direction
        if direction == "positive":
            view["positive_strength"], view["negative_strength"] = 2, 0
        elif direction == "negative":
            view["positive_strength"], view["negative_strength"] = 0, 2
        elif direction == "mixed":
            view["positive_strength"], view["negative_strength"] = 2, 2
        else:
            view["positive_strength"], view["negative_strength"] = 0, 0
    actual_directions = {
        str(row["entity_id"]): str(row["composite_sentiment"])
        for row in issuer_views
    }
    if expected_directions != actual_directions:
        raise RuntimeError(
            f"Converted Sol direction changed in {sample_id}: "
            f"expected={expected_directions} actual={actual_directions}"
        )
    document = {
        "contract_version": CONTRACT_VERSION,
        "concept_registry_version": registry.version,
        "sample_id": sample_id,
        "source_id": str(article["source_id"]),
        "source_timestamp": str(article["source_timestamp"]),
        "source_text_sha256": str(article["source_text_sha256"]),
        "envelope": _teacher_envelope(article, teacher_label, len(units)),
        "entities": entities,
        "statements": statements,
        "participations": participations,
        "issuer_views": issuer_views,
        "synthesis": derive_synthesis(
            entities=entities,
            statements=statements,
            participations=participations,
            issuer_views=issuer_views,
        ),
        "eligibility": eligibility,
        "quality_flags": sorted(
            {
                "sol_teacher_derived_unreviewed",
                *(
                    str(value)
                    for value in article.get("rendered_product", {}).get(
                        "quality_flags", ()
                    )
                ),
            }
        ),
        "migration": {
            "source_contract": str(teacher_label.get("teacher_label_version") or ""),
            "status": "review_required",
            "issues": [
                "sol_teacher_has_no_atomic_evidence_spans",
                "sol_teacher_has_no_participation_roles_or_strengths",
                "analyst_eligibility_rule_mapped",
                "market_observations_normalized_to_v1_neutrality",
            ],
            "conversion_version": CONVERSION_VERSION,
            "teacher_corpus_version": str(teacher_label.get("teacher_corpus_version") or ""),
            "teacher_label_sha256": sha256_json(teacher_label),
        },
    }
    validation = validate_document(document)
    if not validation.valid:
        raise RuntimeError(
            f"Invalid converted Sol document for {sample_id}: {validation.issues}"
        )
    return document


def evaluate_sol_teacher_population(
    teacher_root: Path,
    output_root: Path,
    *,
    expected_items: int = 10_000,
    expected_labels: int = 9_997,
) -> dict[str, Any]:
    item_paths = sorted((teacher_root / "items").glob("*.json"))
    label_paths = sorted((teacher_root / "sol_batch" / "labels").glob("*.json"))
    if len(item_paths) != expected_items or len(label_paths) != expected_labels:
        raise RuntimeError(
            "Sol teacher population mismatch: "
            f"items={len(item_paths)} labels={len(label_paths)} "
            f"expected={expected_items}/{expected_labels}"
        )
    items = {path.stem: load_json(path) for path in item_paths}
    labels = {path.stem: load_json(path) for path in label_paths}
    missing_ids = sorted(set(items) - set(labels))
    if len(missing_ids) != expected_items - expected_labels:
        raise RuntimeError(f"Unexpected Sol missing-label identities: {missing_ids}")
    if set(labels) - set(items):
        raise RuntimeError("Sol labels exist without source articles")

    converted_root = output_root / "converted_labels"
    prediction_root = output_root / "predictions"
    converted_root.mkdir(parents=True, exist_ok=True)
    prediction_root.mkdir(parents=True, exist_ok=True)
    converted_documents: dict[str, dict[str, Any]] = {}
    reused_converted = 0
    for index, sample_id in enumerate(sorted(labels), start=1):
        target = converted_root / f"{sample_id}.json"
        document = _reusable_converted_document(
            target,
            items[sample_id],
            labels[sample_id],
        )
        if document is None:
            document = convert_sol_teacher_label(items[sample_id], labels[sample_id])
            write_json_atomic(target, document)
        else:
            reused_converted += 1
        converted_documents[sample_id] = document
        if index % 250 == 0 or index == len(labels):
            print(
                f"CONVERTED {index:,}/{len(labels):,} reused={reused_converted:,}",
                flush=True,
            )

    teacher_items_sha256 = _document_set_sha256(items)
    identity_path = output_root / "identity_snapshot.json"
    reusable_identity = _reusable_identity_snapshot(
        identity_path, len(items), teacher_items_sha256
    )
    identity_reused = reusable_identity is not None
    if reusable_identity is None:
        identity_index, identity_snapshot = build_benchmark_identity_snapshot(
            tuple(items[sample_id] for sample_id in sorted(items))
        )
        identity_snapshot["source_items_sha256"] = teacher_items_sha256
        write_json_atomic(identity_path, identity_snapshot)
    else:
        identity_index, identity_snapshot = reusable_identity
        print(
            f"IDENTITY reused={identity_snapshot['identity_count']:,} identities",
            flush=True,
        )
    engine = NewsSynthesisEngine(identity_index)
    predictions: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    reused_predictions = 0
    for index, sample_id in enumerate(sorted(items), start=1):
        target = prediction_root / f"{sample_id}.json"
        prediction = (
            _reusable_prediction_document(target, items[sample_id])
            if identity_reused
            else None
        )
        if prediction is not None:
            reused_predictions += 1
            predictions[sample_id] = prediction
        else:
            try:
                prediction = engine.synthesize(article_source(items[sample_id]))
            except Exception as exc:  # retain genuine engine failures in the ledger
                failures.append(
                    {"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                prediction = None
            if prediction is not None:
                # Persistence failures are operational failures, not engine
                # failures.  Let them stop the run instead of misclassifying
                # every affected article in the evaluation ledger.
                write_json_atomic(target, prediction)
                predictions[sample_id] = prediction
        if index % 250 == 0 or index == len(items):
            print(
                f"PREDICTED {index:,}/{len(items):,} reused={reused_predictions:,} "
                f"failures={len(failures):,}",
                flush=True,
            )

    comparison = compare_eligible_directions(
        converted_documents,
        predictions,
        missing_label_ids=missing_ids,
    )
    authority = {
        "conversion_version": CONVERSION_VERSION,
        "evaluation_version": EVALUATION_VERSION,
        "contract_version": CONTRACT_VERSION,
        "concept_registry_version": ConceptRegistry.load().version,
        "engine_version": ENGINE_VERSION,
        "teacher_items_sha256": teacher_items_sha256,
        "teacher_labels_sha256": _document_set_sha256(labels),
        "converted_labels_sha256": _document_set_sha256(converted_documents),
        "identity_snapshot_sha256": str(identity_snapshot["sha256"]),
        "prediction_documents_sha256": _document_set_sha256(predictions),
        "comparison_sha256": sha256_json(comparison),
    }
    manifest = {
        "version": EVALUATION_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "population": {
            "source_articles": len(items),
            "converted_labels": len(converted_documents),
            "missing_teacher_labels": len(missing_ids),
            "prediction_documents": len(predictions),
            "engine_failures": len(failures),
        },
        "authority": authority,
        "missing_teacher_labels": [
            {"sample_id": sample_id, "label_status": "missing"}
            for sample_id in missing_ids
        ],
        "engine_failures": failures,
        "comparison": comparison,
    }
    write_json_atomic(output_root / "comparison.json", comparison)
    write_json_atomic(output_root / "manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(
        render_summary(manifest), encoding="utf-8"
    )
    return manifest


def finalize_sol_teacher_evaluation(
    teacher_root: Path,
    output_root: Path,
    *,
    expected_items: int = 10_000,
    expected_labels: int = 9_997,
) -> dict[str, Any]:
    """Finalize an interrupted full run without loading both corpora at once."""
    item_paths = {path.stem: path for path in (teacher_root / "items").glob("*.json")}
    label_paths = {
        path.stem: path
        for path in (teacher_root / "sol_batch" / "labels").glob("*.json")
    }
    converted_paths = {
        path.stem: path for path in (output_root / "converted_labels").glob("*.json")
    }
    prediction_paths = {
        path.stem: path
        for path in (output_root / "predictions").glob("*.json")
        if path.stem in item_paths
    }
    if (
        len(item_paths) != expected_items
        or len(label_paths) != expected_labels
        or set(converted_paths) != set(label_paths)
    ):
        raise RuntimeError(
            "Existing Sol evaluation population mismatch: "
            f"items={len(item_paths)} labels={len(label_paths)} "
            f"converted={len(converted_paths)}"
        )
    missing_ids = sorted(set(item_paths) - set(label_paths))
    teacher_items_sha256 = _json_file_set_sha256(item_paths)
    identity_path = output_root / "identity_snapshot.json"
    prior_identity_sha256 = ""
    if identity_path.is_file():
        try:
            prior_identity_sha256 = str(load_json(identity_path).get("sha256") or "")
        except (OSError, ValueError):
            pass
    reusable_identity = _reusable_identity_snapshot(
        identity_path, len(item_paths), teacher_items_sha256
    )
    identity_reused = reusable_identity is not None
    if reusable_identity is None:
        articles = tuple(load_json(item_paths[sample_id]) for sample_id in sorted(item_paths))
        identity_index, identity_snapshot = build_benchmark_identity_snapshot(articles)
        identity_snapshot["source_items_sha256"] = teacher_items_sha256
        write_json_atomic(identity_path, identity_snapshot)
        # A legacy snapshot can be safely rebound to the corpus only after a
        # fresh rebuild proves that its resolved identity rows are identical.
        identity_reused = (
            bool(prior_identity_sha256)
            and prior_identity_sha256 == str(identity_snapshot["sha256"])
        )
    else:
        identity_index, identity_snapshot = reusable_identity
    engine = NewsSynthesisEngine(identity_index)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    valid_prediction_paths: dict[str, Path] = {}

    for index, sample_id in enumerate(sorted(label_paths), start=1):
        article = load_json(item_paths[sample_id])
        teacher_label = load_json(label_paths[sample_id])
        gold = _reusable_converted_document(
            converted_paths[sample_id], article, teacher_label
        )
        if gold is None:
            gold = convert_sol_teacher_label(article, teacher_label)
            write_json_atomic(converted_paths[sample_id], gold)
        prediction = None
        prediction_path = prediction_paths.get(sample_id)
        if identity_reused and prediction_path is not None:
            prediction = _reusable_prediction_document(prediction_path, article)
        if prediction is None:
            try:
                prediction = engine.synthesize(article_source(article))
            except Exception as exc:
                failures.append(
                    {"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                prediction = {}
            else:
                target = output_root / "predictions" / f"{sample_id}.json"
                write_json_atomic(target, prediction)
                prediction_paths[sample_id] = target
                valid_prediction_paths[sample_id] = target
        else:
            valid_prediction_paths[sample_id] = prediction_path
        records.extend(
            compare_eligible_directions(
                {sample_id: gold}, {sample_id: prediction}
            )["records"]
        )
        if index % 500 == 0 or index == len(label_paths):
            print(
                f"FINALIZED {index:,}/{len(label_paths):,} failures={len(failures):,}",
                flush=True,
            )

    # Run the engine for the three explicitly missing teacher labels as well;
    # they remain outside direction scoring because no gold unit exists.
    for sample_id in missing_ids:
        article = load_json(item_paths[sample_id])
        prediction_path = prediction_paths.get(sample_id)
        prediction = (
            _reusable_prediction_document(prediction_path, article)
            if identity_reused and prediction_path is not None
            else None
        )
        if prediction is None:
            try:
                prediction = engine.synthesize(article_source(article))
            except Exception as exc:
                failures.append(
                    {"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"}
                )
            else:
                target = output_root / "predictions" / f"{sample_id}.json"
                write_json_atomic(target, prediction)
                prediction_paths[sample_id] = target
                valid_prediction_paths[sample_id] = target
        else:
            valid_prediction_paths[sample_id] = prediction_path

    comparison = {
        "missing_teacher_label_articles": len(missing_ids),
        "union_eligible": _direction_metrics(
            records,
            predicted_eligibility_fields=(
                "predicted_forecast_eligible",
                "predicted_analyst_eligible",
            ),
        ),
        "forecast_eligible": _direction_metrics(
            (row for row in records if row["gold_forecast_eligible"]),
            predicted_eligibility_fields=("predicted_forecast_eligible",),
        ),
        "analyst_eligible": _direction_metrics(
            (row for row in records if row["gold_analyst_eligible"]),
            predicted_eligibility_fields=("predicted_analyst_eligible",),
        ),
        "records": records,
    }
    authority = {
        "conversion_version": CONVERSION_VERSION,
        "evaluation_version": EVALUATION_VERSION,
        "contract_version": CONTRACT_VERSION,
        "concept_registry_version": ConceptRegistry.load().version,
        "engine_version": ENGINE_VERSION,
        "teacher_items_sha256": teacher_items_sha256,
        "teacher_labels_sha256": _json_file_set_sha256(label_paths),
        "converted_labels_sha256": _json_file_set_sha256(converted_paths),
        "identity_snapshot_sha256": str(identity_snapshot["sha256"]),
        "prediction_documents_sha256": _json_file_set_sha256(
            valid_prediction_paths
        ),
        "comparison_sha256": sha256_json(comparison),
    }
    manifest = {
        "version": EVALUATION_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "population": {
            "source_articles": len(item_paths),
            "converted_labels": len(converted_paths),
            "missing_teacher_labels": len(missing_ids),
            "prediction_documents": len(valid_prediction_paths),
            "engine_failures": len(failures),
        },
        "authority": authority,
        "missing_teacher_labels": [
            {"sample_id": sample_id, "label_status": "missing"}
            for sample_id in missing_ids
        ],
        "engine_failures": failures,
        "comparison": comparison,
    }
    write_json_atomic(output_root / "comparison.json", comparison)
    write_json_atomic(output_root / "manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(
        render_summary(manifest), encoding="utf-8"
    )
    return manifest


def compare_eligible_directions(
    gold_documents: Mapping[str, Mapping[str, Any]],
    prediction_documents: Mapping[str, Mapping[str, Any]],
    *,
    missing_label_ids: Iterable[str] = (),
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for sample_id in sorted(gold_documents):
        gold = gold_documents[sample_id]
        prediction = prediction_documents.get(sample_id, {})
        predicted = _prediction_by_ticker(prediction)
        gold_entities = {
            str(row["entity_id"]): str(row.get("ticker") or "").upper()
            for row in gold.get("entities", ())
        }
        gold_eligibility = {
            (str(row["entity_id"]), str(row["product"])): bool(row["eligible"])
            for row in gold.get("eligibility", ())
        }
        for view in gold.get("issuer_views", ()):
            entity_id = str(view["entity_id"])
            ticker = gold_entities[entity_id]
            prediction = predicted.get(_normalize_ticker_identifier(ticker))
            forecast = gold_eligibility.get((entity_id, "forecast_trigger"), False)
            analyst = gold_eligibility.get((entity_id, "analyst_evaluation"), False)
            if not (forecast or analyst):
                continue
            predicted_sentiment = (
                str(prediction["sentiment"]) if prediction is not None else "missing"
            )
            predicted_eligibility = (
                prediction["eligibility"] if prediction is not None else {}
            )
            records.append(
                {
                    "sample_id": sample_id,
                    "ticker": ticker,
                    "gold_sentiment": str(view["composite_sentiment"]),
                    "predicted_sentiment": predicted_sentiment,
                    "exact_direction": predicted_sentiment
                    == str(view["composite_sentiment"]),
                    "gold_forecast_eligible": forecast,
                    "gold_analyst_eligible": analyst,
                    "predicted_forecast_eligible": bool(
                        predicted_eligibility.get("forecast_trigger", False)
                    ),
                    "predicted_analyst_eligible": bool(
                        predicted_eligibility.get("analyst_evaluation", False)
                    ),
                }
            )
    return {
        "missing_teacher_label_articles": len(tuple(missing_label_ids)),
        "union_eligible": _direction_metrics(
            records,
            predicted_eligibility_fields=(
                "predicted_forecast_eligible",
                "predicted_analyst_eligible",
            ),
        ),
        "forecast_eligible": _direction_metrics(
            (row for row in records if row["gold_forecast_eligible"]),
            predicted_eligibility_fields=("predicted_forecast_eligible",),
        ),
        "analyst_eligible": _direction_metrics(
            (row for row in records if row["gold_analyst_eligible"]),
            predicted_eligibility_fields=("predicted_analyst_eligible",),
        ),
        "records": records,
    }


def render_summary(manifest: Mapping[str, Any]) -> str:
    population = manifest["population"]
    comparison = manifest["comparison"]
    lines = [
        "# Sol teacher to News Synthesis direction evaluation",
        "",
        "The converted labels are Sol-derived, source-bound review artifacts. "
        "They are not human-certified gold.",
        "",
        f"- Source articles: {population['source_articles']:,}",
        f"- Converted Sol labels: {population['converted_labels']:,}",
        f"- Missing Sol labels: {population['missing_teacher_labels']:,}",
        f"- Prediction documents: {population['prediction_documents']:,}",
        f"- Engine failures: {population['engine_failures']:,}",
        "",
        "| Gold eligible scope | Units | Direction exact | Direction accuracy | "
        "Predicted eligible | Eligibility recall | End-to-end exact | "
        "End-to-end accuracy | Missing views |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in (
        ("forecast_eligible", "Forecast trigger"),
        ("analyst_eligible", "Analyst evaluation"),
        ("union_eligible", "Forecast or analyst union"),
    ):
        metrics = comparison[key]
        lines.append(
            f"| {label} | {metrics['units']:,} | {metrics['exact']:,} | "
            f"{metrics['accuracy']:.4f} | {metrics['predicted_eligible_units']:,} | "
            f"{metrics['eligibility_recall']:.4f} | {metrics['end_to_end_exact']:,} | "
            f"{metrics['end_to_end_accuracy']:.4f} | {metrics['missing_predictions']:,} |"
        )
    return "\n".join(lines) + "\n"


def _teacher_envelope(
    article: Mapping[str, Any],
    label: Mapping[str, Any],
    unit_count: int,
) -> dict[str, Any]:
    content_role = str(label.get("content_role") or "")
    source_origin = str(label.get("source_origin") or "")
    title = str(article.get("publication", {}).get("title") or "")
    evidence = [{"source_field": "title", "start": 0, "end": len(title), "quote": title}]
    if content_role == "market_roundup":
        structure = "market_overview"
    elif content_role in {"automated_summary", "mover_recap"} or unit_count > 1:
        structure = "multi_subject_digest"
    else:
        structure = "single_subject"
    if source_origin == "automated_summary":
        production = "automated"
    elif source_origin == "editorial_aggregation":
        production = "aggregated"
    else:
        production = "unknown"
    rendered = article.get("rendered_product", {})
    availability = "title_only" if int(rendered.get("source_count") or 0) == 0 else "rendered"
    values = {
        "document_structure": structure,
        "communication_purpose": CONTENT_PURPOSE[content_role],
        "information_origin": SOURCE_ORIGIN[source_origin],
        "production_method": production,
        "text_availability": availability,
    }
    return {
        field: {
            "value": value,
            "rule_id": "sol_teacher_conversion.v1",
            "evidence": evidence,
        }
        for field, value in values.items()
    }


def _candidate_aliases(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    aliases = []
    for value in candidate.get("identity_evidence", ()):
        value = str(value)
        if value.startswith("issuer_alias:"):
            aliases.append(value.split(":", 1)[1])
    return tuple(dict.fromkeys(value for value in aliases if value))


def _issuer_evidence(
    article: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    text = str(article.get("rendered_product", {}).get("text") or "")
    terms = [
        str(candidate.get("display_symbol") or ""),
        str(candidate.get("canonical_instrument_id") or ""),
        *_candidate_aliases(candidate),
    ]
    starts = [
        match.start()
        for term in terms
        if term
        for match in [re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I)]
        if match is not None
    ]
    anchor = min(starts, default=0)
    start = max(text.rfind("\n", 0, anchor) + 1, 0)
    end = text.find("\n", anchor)
    if end < 0:
        end = len(text)
    if end <= start:
        start, end = 0, min(len(text), 500)
    quote = text[start:end]
    if not quote:
        title = str(article.get("publication", {}).get("title") or "")
        quote = title
        start, end = 0, len(title)
        return {"source_field": "title", "start": start, "end": end, "quote": quote}
    return {"source_field": "rendered_text", "start": start, "end": end, "quote": quote}


def _statement_kind(concept: str) -> str:
    if concept.startswith("analyst.") or concept.startswith("strategy.valuation"):
        return "assessment"
    if concept == "guidance.issued":
        return "forecast"
    if concept.startswith("market."):
        return "market_observation"
    return "event"


def _reusable_converted_document(
    path: Path,
    article: Mapping[str, Any],
    teacher_label: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = load_json(path)
    except (OSError, ValueError):
        return None
    migration = document.get("migration", {})
    if (
        str(document.get("sample_id")) != str(article.get("sample_id"))
        or str(document.get("source_id")) != str(article.get("source_id"))
        or str(document.get("source_timestamp"))
        != str(article.get("source_timestamp"))
        or str(document.get("source_text_sha256"))
        != str(article.get("source_text_sha256"))
        or str(document.get("concept_registry_version")) != CONCEPT_REGISTRY_VERSION
        or str(migration.get("conversion_version")) != CONVERSION_VERSION
        or str(migration.get("teacher_label_sha256")) != sha256_json(teacher_label)
        or not validate_document(document).valid
    ):
        return None
    return document


def _reusable_prediction_document(
    path: Path,
    article: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = load_json(path)
    except (OSError, ValueError):
        return None
    production = document.get("production", {})
    if (
        str(document.get("sample_id")) != str(article.get("sample_id"))
        or str(document.get("source_id")) != str(article.get("source_id"))
        or str(document.get("source_timestamp"))
        != str(article.get("source_timestamp"))
        or str(document.get("source_text_sha256"))
        != str(article.get("source_text_sha256"))
        or str(document.get("concept_registry_version")) != CONCEPT_REGISTRY_VERSION
        or str(production.get("engine_version")) != ENGINE_VERSION
        or not validate_document(document).valid
    ):
        return None
    return document


def _reusable_identity_snapshot(
    path: Path,
    expected_articles: int,
    expected_source_items_sha256: str,
) -> tuple[IssuerIdentityIndex, dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        snapshot = load_json(path)
    except (OSError, ValueError):
        return None
    rows = snapshot.get("identities", ())
    if (
        int(snapshot.get("article_count") or 0) != expected_articles
        or int(snapshot.get("identity_count") or 0) != len(rows)
        or str(snapshot.get("source_items_sha256") or "")
        != expected_source_items_sha256
        or sha256_json(rows) != str(snapshot.get("sha256") or "")
    ):
        return None
    identities = tuple(
        IssuerIdentity(
            ticker=str(row["ticker"]),
            issuer_id=str(row["issuer_id"]),
            display_name=str(row["display_name"]),
            aliases=tuple(str(value) for value in row.get("aliases", ()))
            or (str(row["ticker"]),),
            security_id=str(row["security_id"]),
        )
        for row in rows
    )
    return IssuerIdentityIndex(identities), snapshot


def _document_set_sha256(documents: Mapping[str, Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for sample_id in sorted(documents):
        digest.update(str(sample_id).encode("utf-8"))
        digest.update(b"\0")
        digest.update(canonical_json(documents[sample_id]).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _json_file_set_sha256(paths: Mapping[str, Path]) -> str:
    digest = hashlib.sha256()
    for sample_id in sorted(paths):
        digest.update(str(sample_id).encode("utf-8"))
        digest.update(b"\0")
        digest.update(canonical_json(load_json(paths[sample_id])).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _prediction_by_ticker(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entities = {
        str(row["entity_id"]): str(row.get("ticker") or "").upper()
        for row in document.get("entities", ())
        if row.get("ticker")
    }
    eligibility: dict[str, dict[str, bool]] = {}
    for row in document.get("eligibility", ()):
        entity_id = str(row.get("entity_id") or "")
        eligibility.setdefault(entity_id, {})[str(row.get("product") or "")] = bool(
            row.get("eligible")
        )
    output: dict[str, dict[str, Any]] = {}
    for view in document.get("issuer_views", ()):
        entity_id = str(view.get("entity_id") or "")
        ticker = entities.get(entity_id)
        if not ticker:
            continue
        key = _normalize_ticker_identifier(ticker)
        if key in output:
            raise RuntimeError(f"Ambiguous predicted ticker key: {key}")
        output[key] = {
            "sentiment": str(view.get("composite_sentiment") or "missing"),
            "eligibility": eligibility.get(entity_id, {}),
        }
    return output


def _direction_metrics(
    rows: Iterable[Mapping[str, Any]],
    *,
    predicted_eligibility_fields: Sequence[str] = (),
) -> dict[str, Any]:
    materialized = list(rows)
    confusion = Counter(
        (str(row["gold_sentiment"]), str(row["predicted_sentiment"]))
        for row in materialized
    )
    exact = sum(bool(row["exact_direction"]) for row in materialized)
    predicted_eligible = [
        any(bool(row.get(field)) for field in predicted_eligibility_fields)
        for row in materialized
    ]
    end_to_end_exact = sum(
        bool(row["exact_direction"]) and eligible
        for row, eligible in zip(materialized, predicted_eligible, strict=True)
    )
    return {
        "units": len(materialized),
        "exact": exact,
        "accuracy": exact / len(materialized) if materialized else 0.0,
        "predicted_eligible_units": sum(predicted_eligible),
        "eligibility_recall": (
            sum(predicted_eligible) / len(materialized) if materialized else 0.0
        ),
        "end_to_end_exact": end_to_end_exact,
        "end_to_end_accuracy": (
            end_to_end_exact / len(materialized) if materialized else 0.0
        ),
        "missing_predictions": sum(
            str(row["predicted_sentiment"]) == "missing" for row in materialized
        ),
        "gold_distribution": dict(Counter(str(row["gold_sentiment"]) for row in materialized)),
        "predicted_distribution": dict(
            Counter(str(row["predicted_sentiment"]) for row in materialized)
        ),
        "confusion": [
            {"gold": gold, "predicted": predicted, "units": count}
            for (gold, predicted), count in sorted(confusion.items())
        ],
    }
