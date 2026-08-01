from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.preprocessing import MultiLabelBinarizer

from .comparison import (
    CANONICAL_CONCEPT_FAMILIES,
    CollectionItem,
    _human_by_ticker,
    _prediction_by_ticker,
    canonical_concept_family,
)
from .storage import assert_runtime_root, read_json, write_json_atomic


V6_VERSION = "news_text_calibration_v6_candidate_1"


@dataclass(frozen=True, slots=True)
class Thresholds:
    extraction: float
    ticker_scope: float
    concept: float
    forecast: float
    reaction: float
    history: float


class TextFeatures:
    def __init__(self) -> None:
        self.word = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.995,
            max_features=40_000,
            sublinear_tf=True,
        )
        self.char = TfidfVectorizer(
            analyzer="char_wb",
            lowercase=True,
            ngram_range=(3, 5),
            min_df=2,
            max_features=30_000,
            sublinear_tf=True,
        )

    def fit_transform(self, values: list[str]):
        return hstack(
            (self.word.fit_transform(values), self.char.fit_transform(values)),
            format="csr",
        )

    def transform(self, values: list[str]):
        return hstack(
            (self.word.transform(values), self.char.transform(values)),
            format="csr",
        )


@dataclass(slots=True)
class V6Model:
    article_features: TextFeatures
    unit_features: TextFeatures
    extraction_model: Any
    role_model: LogisticRegression
    origin_model: LogisticRegression
    scope_model: Any
    direction_model: LogisticRegression
    concept_model: OneVsRestClassifier
    concept_binarizer: MultiLabelBinarizer
    forecast_model: Any
    reaction_model: Any
    history_model: Any
    thresholds: Thresholds


def fit_v6(
    items: tuple[CollectionItem, ...],
    *,
    v5_dir: Path,
    artifact_path: Path,
) -> V6Model:
    assert_runtime_root(artifact_path.parent)
    fit_items = tuple(item for item in items if item.split == "fit")
    calibration_items = tuple(item for item in items if item.split == "calibration")
    article_features = TextFeatures()
    article_texts = [_article_text(item) for item in fit_items]
    article_x = article_features.fit_transform(article_texts)
    extraction_y = np.asarray([
        item.truth["extraction_decision"] == "labeled" for item in fit_items
    ], dtype=np.int8)
    extraction_model = _fit_binary(article_x, extraction_y)
    role_model = _multiclass_model().fit(
        article_x,
        [str(item.truth["content_role"]) for item in fit_items],
    )
    origin_model = _multiclass_model().fit(
        article_x,
        [str(item.truth["source_origin"]) for item in fit_items],
    )

    fit_candidates = _candidate_rows(fit_items, v5_dir)
    unit_features = TextFeatures()
    unit_x = unit_features.fit_transform([row["text"] for row in fit_candidates])
    scope_y = np.asarray([row["truth"] is not None for row in fit_candidates], dtype=np.int8)
    scope_model = _fit_binary(unit_x, scope_y)
    truth_indexes = np.flatnonzero(scope_y)
    truth_x = unit_x[truth_indexes]
    truth_rows = [fit_candidates[index] for index in truth_indexes]
    direction_model = _multiclass_model().fit(
        truth_x,
        [row["truth"]["semantic_direction"] for row in truth_rows],
    )
    concept_binarizer = MultiLabelBinarizer(classes=CANONICAL_CONCEPT_FAMILIES)
    concept_y = concept_binarizer.fit_transform([
        sorted({
            projected
            for concept in row["truth"]["event_concepts"]
            if (projected := canonical_concept_family(concept))
        })
        for row in truth_rows
    ])
    concept_model = OneVsRestClassifier(
        LogisticRegression(
            C=1.0,
            max_iter=2_000,
            class_weight="balanced",
            random_state=20260731,
        ),
        n_jobs=1,
    ).fit(truth_x, concept_y)
    forecast_model = _fit_binary(
        truth_x,
        np.asarray([row["truth"]["forecast_trigger_eligible"] for row in truth_rows], dtype=np.int8),
    )
    reaction_model = _fit_binary(
        truth_x,
        np.asarray([row["truth"]["reaction_evaluation_eligible"] for row in truth_rows], dtype=np.int8),
    )
    history_model = _fit_binary(
        truth_x,
        np.asarray([row["truth"]["issuer_history_context_eligible"] for row in truth_rows], dtype=np.int8),
    )
    provisional = V6Model(
        article_features=article_features,
        unit_features=unit_features,
        extraction_model=extraction_model,
        role_model=role_model,
        origin_model=origin_model,
        scope_model=scope_model,
        direction_model=direction_model,
        concept_model=concept_model,
        concept_binarizer=concept_binarizer,
        forecast_model=forecast_model,
        reaction_model=reaction_model,
        history_model=history_model,
        thresholds=Thresholds(0.5, 0.5, 0.5, 0.5, 0.5, 0.5),
    )
    thresholds = _select_thresholds(provisional, calibration_items, v5_dir)
    provisional.thresholds = thresholds
    joblib.dump(provisional, artifact_path)
    return provisional


def generate_predictions(
    model: V6Model,
    items: Iterable[CollectionItem],
    *,
    v5_dir: Path,
    output_dir: Path,
) -> None:
    assert_runtime_root(output_dir)
    for index, item in enumerate(items, 1):
        target = output_dir / f"{item.sample_id}.json"
        prediction = predict_item(model, item, v5_dir=v5_dir)
        write_json_atomic(target, prediction)
        if index % 100 == 0:
            print(f"V6 {index:,}", flush=True)


def predict_item(model: V6Model, item: CollectionItem, *, v5_dir: Path) -> dict[str, Any]:
    article_x = model.article_features.transform([_article_text(item)])
    extraction_probability = _positive_probability(model.extraction_model, article_x)[0]
    role = str(model.role_model.predict(article_x)[0])
    origin = str(model.origin_model.predict(article_x)[0])
    labels: list[dict[str, Any]] = []
    if extraction_probability >= model.thresholds.extraction:
        candidates = _candidate_rows((item,), v5_dir)
        if not candidates:
            return {
                "version": V6_VERSION,
                "sample_id": item.sample_id,
                "split": item.split,
                "source_id": item.blinded["source_id"],
                "extraction_probability": float(extraction_probability),
                "labels": [],
            }
        unit_x = model.unit_features.transform([row["text"] for row in candidates])
        scope_probability = _positive_probability(model.scope_model, unit_x)
        selected = np.flatnonzero(scope_probability >= model.thresholds.ticker_scope)
        if len(selected):
            selected_x = unit_x[selected]
            directions = model.direction_model.predict(selected_x)
            direction_probabilities = model.direction_model.predict_proba(selected_x)
            concepts = model.concept_model.predict_proba(selected_x)
            forecast = _positive_probability(model.forecast_model, selected_x)
            reaction = _positive_probability(model.reaction_model, selected_x)
            history = _positive_probability(model.history_model, selected_x)
            for local_index, row_index in enumerate(selected):
                row = candidates[int(row_index)]
                active = [
                    str(name)
                    for name, probability in zip(model.concept_binarizer.classes_, concepts[local_index])
                    if probability >= model.thresholds.concept
                ]
                labels.append(
                    {
                        "ticker": row["ticker"],
                        "unit_role": "v6_calibrated_news_unit",
                        "classification": {
                            "content_role": role,
                            "source_origin": origin,
                            "event_concepts": active,
                            "semantic_direction": str(directions[local_index]),
                            "semantic_score": 0.0,
                            "direction_confidence": float(np.max(direction_probabilities[local_index])),
                        },
                        "forecast_trigger_eligible": bool(forecast[local_index] >= model.thresholds.forecast),
                        "reaction_evaluation_eligible": bool(reaction[local_index] >= model.thresholds.reaction),
                        "issuer_history_context_eligible": bool(history[local_index] >= model.thresholds.history),
                    }
                )
    return {
        "version": V6_VERSION,
        "sample_id": item.sample_id,
        "split": item.split,
        "source_id": item.blinded["source_id"],
        "extraction_probability": float(extraction_probability),
        "labels": labels,
    }


def _select_thresholds(model: V6Model, items: tuple[CollectionItem, ...], v5_dir: Path) -> Thresholds:
    article_x = model.article_features.transform([_article_text(item) for item in items])
    extraction = _best_threshold(
        np.asarray([item.truth["extraction_decision"] == "labeled" for item in items]),
        _positive_probability(model.extraction_model, article_x),
    )
    candidates = _candidate_rows(items, v5_dir)
    unit_x = model.unit_features.transform([row["text"] for row in candidates])
    scope_truth = np.asarray([row["truth"] is not None for row in candidates])
    scope = _best_threshold(
        scope_truth,
        _positive_probability(model.scope_model, unit_x),
    )
    truth_indexes = np.flatnonzero(scope_truth)
    truth_x = unit_x[truth_indexes]
    truth_rows = [candidates[index] for index in truth_indexes]
    concept_truth = model.concept_binarizer.transform([
        sorted({
            projected
            for concept in row["truth"]["event_concepts"]
            if (projected := canonical_concept_family(concept))
        })
        for row in truth_rows
    ])
    concept = _best_multilabel_threshold(concept_truth, model.concept_model.predict_proba(truth_x))
    return Thresholds(
        extraction=extraction,
        ticker_scope=scope,
        concept=concept,
        forecast=_best_threshold(
            np.asarray([row["truth"]["forecast_trigger_eligible"] for row in truth_rows]),
            _positive_probability(model.forecast_model, truth_x),
        ),
        reaction=_best_threshold(
            np.asarray([row["truth"]["reaction_evaluation_eligible"] for row in truth_rows]),
            _positive_probability(model.reaction_model, truth_x),
        ),
        history=_best_threshold(
            np.asarray([row["truth"]["issuer_history_context_eligible"] for row in truth_rows]),
            _positive_probability(model.history_model, truth_x),
        ),
    )


def _candidate_rows(items: Iterable[CollectionItem], v5_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        prediction = read_json(v5_dir / f"{item.sample_id}.json")
        predicted = _prediction_by_ticker(prediction)
        truth = _human_by_ticker(item.truth)
        label_text: dict[str, list[str]] = defaultdict(list)
        for label in prediction.get("labels") or ():
            ticker = str(label.get("ticker") or "").upper()
            if ticker:
                label_text[ticker].append(str(label.get("semantic_evidence_text") or ""))
        candidates = set(predicted) | set(truth)
        candidates.update(
            str(value.get("ticker") or "").upper()
            for value in item.blinded.get("point_in_time_issuer_candidates") or ()
            if value.get("ticker")
        )
        for ticker in sorted(candidates):
            rows.append(
                {
                    "sample_id": item.sample_id,
                    "ticker": ticker,
                    "truth": truth.get(ticker),
                    "text": _unit_text(item, ticker, prediction, label_text.get(ticker, [])),
                }
            )
    return rows


def _article_text(item: CollectionItem) -> str:
    publication = item.blinded["publication"]
    rendered = str(item.blinded["rendered_product"].get("text") or "")
    return "\n".join(
        (
            f"TITLE {publication.get('title') or ''}",
            f"TEASER {publication.get('teaser') or ''}",
            f"AUTHOR {publication.get('author') or ''}",
            "CHANNELS " + " ".join(publication.get("channels") or ()),
            "TAGS " + " ".join(publication.get("provider_tags") or ()),
            rendered[:12_000],
        )
    )


def _unit_text(
    item: CollectionItem,
    ticker: str,
    prediction: Mapping[str, Any],
    passages: list[str],
) -> str:
    publication = item.blinded["publication"]
    v5 = _prediction_by_ticker(prediction).get(ticker) or {}
    rendered = str(item.blinded["rendered_product"].get("text") or "")
    return "\n".join(
        (
            f"TICKER {ticker}",
            f"TITLE {publication.get('title') or ''}",
            f"AUTHOR {publication.get('author') or ''}",
            "TAGS " + " ".join(publication.get("provider_tags") or ()),
            "V5_CONCEPTS " + " ".join(v5.get("event_concepts") or ()),
            "V5_DIRECTION " + str(v5.get("semantic_direction") or ""),
            "\n".join(passages) if passages else rendered[:8_000],
        )
    )


def _fit_binary(x, values: np.ndarray):
    classes = np.unique(values)
    if len(classes) == 1:
        return DummyClassifier(strategy="constant", constant=int(classes[0])).fit(x, values)
    return LogisticRegression(
        C=1.0,
        max_iter=2_000,
        class_weight="balanced",
        random_state=20260731,
    ).fit(x, values)


def _multiclass_model() -> LogisticRegression:
    return LogisticRegression(
        C=1.0,
        max_iter=2_000,
        class_weight="balanced",
        random_state=20260731,
    )


def _positive_probability(model, x) -> np.ndarray:
    classes = list(model.classes_)
    if 1 not in classes:
        return np.zeros(x.shape[0], dtype=np.float64)
    if len(classes) == 1:
        return np.ones(x.shape[0], dtype=np.float64)
    return model.predict_proba(x)[:, classes.index(1)]


def _best_threshold(actual: np.ndarray, probabilities: np.ndarray) -> float:
    best = (0.0, 0.5)
    for threshold in np.arange(0.10, 0.91, 0.02):
        predicted = probabilities >= threshold
        tp = int(np.sum(actual & predicted))
        fp = int(np.sum(~actual & predicted))
        fn = int(np.sum(actual & ~predicted))
        score = _f1(tp, fp, fn)
        if score > best[0]:
            best = (score, float(threshold))
    return round(best[1], 2)


def _best_multilabel_threshold(actual: np.ndarray, probabilities: np.ndarray) -> float:
    best = (0.0, 0.5)
    truth = actual.astype(bool)
    for threshold in np.arange(0.10, 0.91, 0.02):
        predicted = probabilities >= threshold
        score = _f1(
            int(np.sum(truth & predicted)),
            int(np.sum(~truth & predicted)),
            int(np.sum(truth & ~predicted)),
        )
        if score > best[0]:
            best = (score, float(threshold))
    return round(best[1], 2)


def _f1(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
