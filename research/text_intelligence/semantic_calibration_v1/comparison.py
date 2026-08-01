from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.text_intelligence.scoped_labeling_v1.news_identity import (
    IssuerIdentity,
    NewsIssuerResolver,
)
from research.text_intelligence.scoped_labeling_v1.pipeline import (
    classify_news_document,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .schema import stable_json_hash
from .storage import assert_runtime_root, read_json, write_json_atomic


@dataclass(frozen=True, slots=True)
class CollectionItem:
    sample_id: str
    split: str
    blinded: dict[str, Any]
    truth: dict[str, Any]


def load_collection(root: Path) -> tuple[CollectionItem, ...]:
    manifest = read_json(root / "sample_manifest.json")
    sealed = read_json(root / "sealed" / "v5_comparison_and_splits.json")
    split_by_id = {
        str(item["sample_id"]): str(item["locked_split"])
        for item in sealed["items"]
    }
    output: list[CollectionItem] = []
    for row in manifest["items"]:
        sample_id = str(row["sample_id"])
        output.append(
            CollectionItem(
                sample_id=sample_id,
                split=split_by_id[sample_id],
                blinded=read_json(root / "blinded_articles" / f"{sample_id}.json"),
                truth=read_json(root / "annotations_v2" / f"{sample_id}.json"),
            )
        )
    return tuple(output)


def current_v5_prediction(item: CollectionItem) -> dict[str, Any]:
    source = item.blinded
    publication = source["publication"]
    rendered = source["rendered_product"]
    identities: list[IssuerIdentity] = []
    for candidate in source.get("point_in_time_issuer_candidates") or ():
        ticker = str(candidate.get("ticker") or "").upper()
        aliases = tuple(
            evidence.split(":", 1)[1]
            for evidence in candidate.get("identity_evidence") or ()
            if str(evidence).startswith("issuer_alias:")
        )
        identities.append(
            IssuerIdentity(
                ticker=ticker,
                issuer_id=f"calibration:{ticker}",
                aliases=aliases,
            )
        )
    provider_tickers = tuple(
        str(value).upper()
        for value in publication.get("provider_tickers") or ()
    )
    metadata = {
        "author": publication.get("author") or "",
        "provider": publication.get("provider") or "",
        "provider_tags": publication.get("provider_tags") or (),
        "channels": publication.get("channels") or (),
        "issuer_identities": tuple(
            {
                "ticker": value.ticker,
                "issuer_id": value.issuer_id,
                "aliases": value.aliases,
            }
            for value in identities
        ),
    }
    document = SemanticDocument(
        corpus="news",
        source_id=str(source["source_id"]),
        timestamp=str(source["source_timestamp"]),
        title=str(publication.get("title") or ""),
        text=str(rendered.get("text") or ""),
        tickers=provider_tickers,
        metadata=metadata,
    )
    labels = classify_news_document(
        document,
        issuer_resolver=NewsIssuerResolver(
            identities,
            article_tickers=provider_tickers,
        ),
    )
    return {
        "sample_id": item.sample_id,
        "split": item.split,
        "source_id": source["source_id"],
        "labels": [label.as_dict() for label in labels],
    }


def run_v5_predictions(
    root: Path,
    *,
    output_dir: Path,
    report_every: int = 100,
) -> tuple[CollectionItem, ...]:
    assert_runtime_root(output_dir)
    items = load_collection(root)
    prediction_dir = output_dir / "v5_predictions"
    for index, item in enumerate(items, 1):
        target = prediction_dir / f"{item.sample_id}.json"
        if not target.exists():
            write_json_atomic(target, current_v5_prediction(item))
        if index % report_every == 0 or index == len(items):
            print(f"V5 {index:,}/{len(items):,}", flush=True)
    return items


def evaluate_predictions(
    items: Iterable[CollectionItem],
    *,
    prediction_dir: Path,
    splits: set[str] | None = None,
    canonical_concepts: bool = False,
) -> dict[str, Any]:
    rows = [item for item in items if splits is None or item.split in splits]
    article_role = Counter()
    article_origin = Counter()
    extraction = Counter()
    truth_tickers: set[tuple[str, str]] = set()
    predicted_tickers: set[tuple[str, str]] = set()
    truth_concepts: set[tuple[str, str, str]] = set()
    predicted_concepts: set[tuple[str, str, str]] = set()
    directions = Counter()
    eligibility = {
        name: Counter()
        for name in (
            "forecast_trigger_eligible",
            "reaction_evaluation_eligible",
            "issuer_history_context_eligible",
        )
    }
    errors: list[dict[str, Any]] = []
    for item in rows:
        prediction = read_json(prediction_dir / f"{item.sample_id}.json")
        truth = item.truth
        human_units = _human_by_ticker(truth)
        predicted_units = _prediction_by_ticker(prediction)
        truth_labeled = truth["extraction_decision"] == "labeled"
        prediction_labeled = bool(predicted_units)
        extraction[(truth_labeled, prediction_labeled)] += 1
        predicted_role = _majority(
            str(label["classification"].get("content_role") or "")
            for label in prediction["labels"]
        )
        predicted_origin = _majority(
            str(label["classification"].get("source_origin") or "")
            for label in prediction["labels"]
        )
        _confusion_add(article_role, str(truth["content_role"]), predicted_role)
        _confusion_add(article_origin, str(truth["source_origin"]), predicted_origin)
        sample_errors: list[str] = []
        if truth_labeled != prediction_labeled:
            sample_errors.append("extraction")
        if predicted_role != truth["content_role"]:
            sample_errors.append("content_role")
        if predicted_origin != truth["source_origin"]:
            sample_errors.append("source_origin")
        for ticker, unit in human_units.items():
            key = (item.sample_id, ticker)
            truth_tickers.add(key)
            for concept in unit["event_concepts"]:
                projected = canonical_concept_family(concept) if canonical_concepts else concept
                if projected:
                    truth_concepts.add((*key, projected))
            predicted = predicted_units.get(ticker)
            predicted_direction = (
                str(predicted["semantic_direction"])
                if predicted is not None
                else "__missing__"
            )
            directions[(str(unit["semantic_direction"]), predicted_direction)] += 1
            if predicted_direction != unit["semantic_direction"]:
                sample_errors.append(f"direction:{ticker}")
            for field, counter in eligibility.items():
                actual = bool(unit[field])
                observed = bool(predicted[field]) if predicted is not None else False
                counter[(actual, observed)] += 1
            if predicted is not None:
                for concept in predicted["event_concepts"]:
                    projected = canonical_concept_family(concept) if canonical_concepts else concept
                    if projected:
                        predicted_concepts.add((*key, projected))
        for ticker, unit in predicted_units.items():
            key = (item.sample_id, ticker)
            predicted_tickers.add(key)
            if ticker not in human_units:
                sample_errors.append(f"extra_ticker:{ticker}")
            for concept in unit["event_concepts"]:
                projected = canonical_concept_family(concept) if canonical_concepts else concept
                if projected:
                    predicted_concepts.add((*key, projected))
        if sample_errors:
            errors.append(
                {
                    "sample_id": item.sample_id,
                    "split": item.split,
                    "errors": sorted(set(sample_errors)),
                    "truth_role": truth["content_role"],
                    "predicted_role": predicted_role,
                    "truth_tickers": sorted(human_units),
                    "predicted_tickers": sorted(predicted_units),
                }
            )
    result = {
        "sample_count": len(rows),
        "splits": sorted(splits) if splits else ["all"],
        "concept_contract": "canonical_family_v1" if canonical_concepts else "exact_raw_label",
        "extraction": _binary_metrics(extraction),
        "ticker_scope": _set_metrics(truth_tickers, predicted_tickers),
        "event_concepts": _set_metrics(truth_concepts, predicted_concepts),
        "content_role": _multiclass_metrics(article_role),
        "source_origin": _multiclass_metrics(article_origin),
        "semantic_direction": _multiclass_metrics(directions),
        "eligibility": {
            name: _binary_metrics(counter)
            for name, counter in eligibility.items()
        },
        "error_articles": len(errors),
        "errors": errors,
    }
    result["report_sha256"] = stable_json_hash(result)
    return result


def _human_by_ticker(annotation: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for unit in annotation.get("issuer_units") or ():
        grouped[str(unit["ticker"]).upper()].append(unit)
    return {
        ticker: {
            "event_concepts": sorted({
                str(concept)
                for unit in units
                for concept in unit.get("event_concepts") or ()
            }),
            "semantic_direction": _aggregate_direction(
                str(unit["semantic_direction"]) for unit in units
            ),
            "forecast_trigger_eligible": any(
                bool(unit["forecast_trigger_eligible"]) for unit in units
            ),
            "reaction_evaluation_eligible": any(
                bool(unit["reaction_evaluation_eligible"]) for unit in units
            ),
            "issuer_history_context_eligible": any(
                bool(unit["issuer_history_context_eligible"]) for unit in units
            ),
        }
        for ticker, units in grouped.items()
    }


def _prediction_by_ticker(prediction: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for label in prediction.get("labels") or ():
        ticker = str(label.get("ticker") or "").upper()
        if ticker:
            grouped[ticker].append(label)
    output: dict[str, dict[str, Any]] = {}
    for ticker, labels in grouped.items():
        output[ticker] = {
            "event_concepts": sorted({
                str(concept)
                for label in labels
                for concept in label["classification"].get("event_concepts") or ()
            }),
            "semantic_direction": _aggregate_direction(
                str(label["classification"].get("semantic_direction") or "neutral")
                for label in labels
            ),
            "forecast_trigger_eligible": any(
                bool(label.get("forecast_trigger_eligible")) for label in labels
            ),
            "reaction_evaluation_eligible": any(
                bool(label.get("reaction_evaluation_eligible")) for label in labels
            ),
            "issuer_history_context_eligible": any(
                bool(label.get("issuer_history_context_eligible")) for label in labels
            ),
        }
    return output


def _aggregate_direction(values: Iterable[str]) -> str:
    selected = {value for value in values if value}
    if not selected:
        return "neutral"
    if len(selected) == 1:
        return next(iter(selected))
    if "mixed" in selected or {"positive", "negative"} <= selected:
        return "mixed"
    selected.discard("neutral")
    return next(iter(selected)) if len(selected) == 1 else "mixed"


def _majority(values: Iterable[str]) -> str:
    counts = Counter(value for value in values if value)
    if not counts:
        return "__missing__"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _confusion_add(counter: Counter, actual: str, predicted: str) -> None:
    counter[(actual, predicted)] += 1


def _set_metrics(actual: set, predicted: set) -> dict[str, Any]:
    true_positive = len(actual & predicted)
    false_positive = len(predicted - actual)
    false_negative = len(actual - predicted)
    return _precision_recall(true_positive, false_positive, false_negative)


def _binary_metrics(counter: Counter) -> dict[str, Any]:
    true_positive = counter[(True, True)]
    false_positive = counter[(False, True)]
    false_negative = counter[(True, False)]
    true_negative = counter[(False, False)]
    result = _precision_recall(true_positive, false_positive, false_negative)
    total = true_positive + false_positive + false_negative + true_negative
    result.update(
        {
            "true_negative": true_negative,
            "accuracy": _ratio(true_positive + true_negative, total),
        }
    )
    return result


def _precision_recall(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": _ratio(2 * precision * recall, precision + recall),
    }


def _multiclass_metrics(counter: Counter) -> dict[str, Any]:
    labels = sorted({value for pair in counter for value in pair})
    per_class: dict[str, Any] = {}
    correct = 0
    total = sum(counter.values())
    for label in labels:
        tp = counter[(label, label)]
        fp = sum(count for (actual, predicted), count in counter.items() if predicted == label and actual != label)
        fn = sum(count for (actual, predicted), count in counter.items() if actual == label and predicted != label)
        per_class[label] = _precision_recall(tp, fp, fn)
        correct += tp
    usable = [value for key, value in per_class.items() if key != "__missing__"]
    return {
        "accuracy": _ratio(correct, total),
        "macro_f1": round(sum(value["f1"] for value in usable) / len(usable), 6) if usable else 0.0,
        "per_class": per_class,
        "confusion": {
            f"{actual}->{predicted}": count
            for (actual, predicted), count in sorted(counter.items())
        },
    }


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


CANONICAL_CONCEPT_FAMILIES = (
    "accounting_audit",
    "analyst_action",
    "capital_return",
    "capital_structure",
    "clinical",
    "contract_order",
    "credit_solvency",
    "earnings",
    "financing",
    "guidance",
    "legal",
    "listing_market_structure",
    "ma_transaction",
    "management_governance",
    "market_reaction",
    "operations",
    "ownership",
    "product_commercial",
    "regulatory",
    "strategy_valuation",
)


def canonical_concept_family(value: str) -> str:
    """Project reviewer-specific concepts into a stable broad News ontology.

    The human reviews intentionally retain precise event language. This
    projection is a separate auditable comparison layer; it never rewrites the
    immutable annotation.
    """
    text = str(value or "").casefold().replace("-", "_")
    if not text:
        return ""
    if any(token in text for token in ("analyst", "rating", "price_target", "price target")):
        return "analyst_action"
    if any(token in text for token in ("earnings", "eps", "revenue", "sales", "profit", "margin", "ebitda", "cash_flow", "cash flow", "subscriber")):
        return "earnings"
    if any(token in text for token in ("guidance", "outlook", "forecast", "estimate")):
        return "guidance"
    if any(token in text for token in ("fda", "regulatory", "approval", "complete_response", "complete response", "pdufa", "clinical_hold")):
        return "regulatory"
    if any(token in text for token in ("clinical", "trial", "endpoint", "patient", "topline", "study", "safety", "efficacy")):
        return "clinical"
    if any(token in text for token in ("acquisition", "acquire", "merger", "takeover", "business_combination", "business combination")):
        return "ma_transaction"
    if any(token in text for token in ("offering", "financing", "private_placement", "private placement", "at_the_market", "convertible", "warrant", "dilution", "share_issu", "capital_raise", "ipo", "pipe")):
        return "financing"
    if any(token in text for token in ("reverse_split", "reverse_stock", "stock_split", "share_split", "authorized_shares")):
        return "capital_structure"
    if any(token in text for token in ("buyback", "repurchase", "dividend", "capital_return")):
        return "capital_return"
    if any(token in text for token in ("bankrupt", "chapter11", "chapter_11", "chapter7", "chapter_7", "going_concern", "solvency", "liquidity")):
        return "credit_solvency"
    if any(token in text for token in ("contract", "order", "award", "partnership", "collaboration", "license", "licensing")):
        return "contract_order"
    if any(token in text for token in ("lawsuit", "litigation", "settlement", "investigation", "fraud", "legal", "patent")):
        return "legal"
    if any(token in text for token in ("restructur", "workforce", "layoff", "closure", "demand", "manufactur", "operations", "production", "capacity", "supply_chain")):
        return "operations"
    if any(token in text for token in ("listing", "delist", "noncompliance", "trading_halt", "trading halt", "trading_resume")):
        return "listing_market_structure"
    if any(token in text for token in ("accounting", "audit", "restatement", "material_weakness", "internal_control")):
        return "accounting_audit"
    if any(token in text for token in ("insider", "ownership", "stake", "beneficial_owner")):
        return "ownership"
    if any(token in text for token in ("management", "board", "ceo", "cfo", "governance", "director", "executive")):
        return "management_governance"
    if any(token in text for token in ("market_reaction", "market_move", "mover", "observed_gain", "observed_decline", "price_action", "52_week", "volume")):
        return "market_reaction"
    if any(token in text for token in ("product", "commercial", "launch", "recall", "customer", "distribution", "prescription")):
        return "product_commercial"
    if any(token in text for token in ("strategy", "valuation", "strategic", "competition", "market_share", "industry", "macro")):
        return "strategy_valuation"
    prefix = text.split(".", 1)[0]
    return prefix if prefix in CANONICAL_CONCEPT_FAMILIES else ""
