from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.preprocessing import Normalizer
from sklearn.decomposition import TruncatedSVD

from .candidate_contract import enrich_candidate_rows
from .comparison import CANONICAL_CONCEPT_FAMILIES, CollectionItem, canonical_concept_family
from .storage import assert_runtime_root, read_json, write_json_atomic
from .teacher_paths import DEFAULT_TEACHER_ROOT


V10_VERSION = "news_tfidf_bagged_random_forest_v10_candidate_1"
DEFAULT_HUMAN_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\news_1000"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1"
    r"\news_v10_tfidf_random_forest"
)
RANDOM_SEED = 20260801


@dataclass(frozen=True, slots=True)
class ForestConfig:
    article_components: int = 384
    unit_components: int = 384
    word_features: int = 40_000
    char_features: int = 24_000
    article_trees: int = 192
    unit_trees: int = 192
    concept_trees: int = 96
    max_leaf_nodes: int = 1_024
    min_samples_leaf: int = 2
    max_samples: float = 0.80
    scope_threshold: float = 0.50
    concept_threshold: float = 0.35
    eligibility_threshold: float = 0.50
    workers: int = -1


class TfidfSvdFeatures:
    def __init__(self, *, components: int, word_features: int, char_features: int) -> None:
        self.word = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.997,
            max_features=word_features,
            sublinear_tf=True,
            strip_accents="unicode",
            dtype=np.float32,
        )
        self.char = TfidfVectorizer(
            analyzer="char_wb",
            lowercase=True,
            ngram_range=(3, 5),
            min_df=3,
            max_features=char_features,
            sublinear_tf=True,
            dtype=np.float32,
        )
        self.svd = TruncatedSVD(n_components=components, n_iter=7, random_state=RANDOM_SEED)
        self.normalizer = Normalizer(copy=False)

    def fit_transform(self, values: Sequence[str]) -> np.ndarray:
        sparse = hstack(
            (self.word.fit_transform(values), self.char.fit_transform(values)),
            format="csr",
            dtype=np.float32,
        )
        dense = self.svd.fit_transform(sparse).astype(np.float32, copy=False)
        return self.normalizer.fit_transform(dense).astype(np.float32, copy=False)

    def transform(self, values: Sequence[str]) -> np.ndarray:
        sparse = hstack(
            (self.word.transform(values), self.char.transform(values)),
            format="csr",
            dtype=np.float32,
        )
        dense = self.svd.transform(sparse).astype(np.float32, copy=False)
        return self.normalizer.transform(dense).astype(np.float32, copy=False)


@dataclass(slots=True)
class NewsV10Model:
    version: str
    config: ForestConfig
    article_features: TfidfSvdFeatures
    unit_features: TfidfSvdFeatures
    extraction_model: RandomForestClassifier
    role_model: RandomForestClassifier
    origin_model: RandomForestClassifier
    scope_model: RandomForestClassifier
    direction_model: RandomForestClassifier
    concept_model: OneVsRestClassifier
    eligibility_model: OneVsRestClassifier
    concept_classes: tuple[str, ...]
    eligibility_classes: tuple[str, ...]
    training_articles: int
    training_candidates: int
    training_issuer_units: int


@dataclass(frozen=True, slots=True)
class TeacherArticle:
    sample_id: str
    source: dict[str, Any]
    label: dict[str, Any]


def load_teacher_articles(teacher_root: Path = DEFAULT_TEACHER_ROOT) -> tuple[TeacherArticle, ...]:
    manifest = read_json(teacher_root / "sample_manifest.json")
    rows: list[TeacherArticle] = []
    failures: list[str] = []
    for raw in manifest.get("items") or ():
        sample_id = str(raw["sample_id"])
        item_path = teacher_root / "items" / f"{sample_id}.json"
        label_path = teacher_root / "sol_batch" / "labels" / f"{sample_id}.json"
        if not label_path.exists():
            failures.append(sample_id)
            continue
        rows.append(
            TeacherArticle(
                sample_id=sample_id,
                source=read_json(item_path),
                label=read_json(label_path),
            )
        )
    if len(rows) != 9_997 or len(failures) != 3:
        raise RuntimeError(
            f"Unexpected Sol teacher authority: valid={len(rows):,} failures={len(failures):,}"
        )
    return tuple(rows)


def fit_v10(
    articles: Sequence[TeacherArticle],
    *,
    config: ForestConfig,
    artifact_path: Path,
) -> NewsV10Model:
    assert_runtime_root(artifact_path.parent)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    article_texts = [article_text(row.source) for row in articles]
    article_features = TfidfSvdFeatures(
        components=config.article_components,
        word_features=config.word_features,
        char_features=config.char_features,
    )
    print(f"V10 article TF-IDF/SVD rows={len(article_texts):,}", flush=True)
    article_x = article_features.fit_transform(article_texts)
    extraction_y = np.asarray([str(row.label["extraction_decision"]) for row in articles])
    role_y = np.asarray([str(row.label["content_role"]) for row in articles])
    origin_y = np.asarray([str(row.label["source_origin"]) for row in articles])
    extraction_model = _forest(config.article_trees, config, balanced=True).fit(article_x, extraction_y)
    role_model = _forest(config.article_trees, config, balanced=True).fit(article_x, role_y)
    origin_model = _forest(config.article_trees, config, balanced=True).fit(article_x, origin_y)

    candidates = teacher_candidate_rows(articles)
    print(
        f"V10 issuer TF-IDF/SVD candidates={len(candidates):,} "
        f"positive={sum(row['truth'] is not None for row in candidates):,}",
        flush=True,
    )
    unit_features = TfidfSvdFeatures(
        components=config.unit_components,
        word_features=config.word_features,
        char_features=config.char_features,
    )
    unit_x = unit_features.fit_transform([str(row["text"]) for row in candidates])
    scope_y = np.asarray([row["truth"] is not None for row in candidates], dtype=np.int8)
    scope_model = _forest(config.unit_trees, config, balanced=True).fit(unit_x, scope_y)
    positive_indexes = np.flatnonzero(scope_y)
    positive_x = unit_x[positive_indexes]
    positive_rows = [candidates[int(index)] for index in positive_indexes]
    direction_y = np.asarray([
        str(row["truth"]["classification"]["semantic_direction"])
        for row in positive_rows
    ])
    direction_model = _forest(config.unit_trees, config, balanced=True).fit(
        positive_x, direction_y
    )
    concept_classes = tuple(CANONICAL_CONCEPT_FAMILIES)
    concept_y = np.asarray([
        [
            int(name in {
                projected
                for concept in row["truth"]["classification"].get("event_concepts") or ()
                if (projected := canonical_concept_family(str(concept)))
            })
            for name in concept_classes
        ]
        for row in positive_rows
    ], dtype=np.int8)
    concept_model = OneVsRestClassifier(
        _forest(config.concept_trees, config, balanced=True, workers=1),
        n_jobs=config.workers,
    ).fit(positive_x, concept_y)
    eligibility_classes = (
        "forecast_trigger_eligible",
        "reaction_evaluation_eligible",
        "issuer_history_context_eligible",
    )
    eligibility_y = np.asarray([
        [int(bool(row["truth"].get(name))) for name in eligibility_classes]
        for row in positive_rows
    ], dtype=np.int8)
    eligibility_model = OneVsRestClassifier(
        _forest(config.concept_trees, config, balanced=True, workers=1),
        n_jobs=config.workers,
    ).fit(positive_x, eligibility_y)
    model = NewsV10Model(
        version=V10_VERSION,
        config=config,
        article_features=article_features,
        unit_features=unit_features,
        extraction_model=extraction_model,
        role_model=role_model,
        origin_model=origin_model,
        scope_model=scope_model,
        direction_model=direction_model,
        concept_model=concept_model,
        eligibility_model=eligibility_model,
        concept_classes=concept_classes,
        eligibility_classes=eligibility_classes,
        training_articles=len(articles),
        training_candidates=len(candidates),
        training_issuer_units=len(positive_rows),
    )
    joblib.dump(model, artifact_path, compress=3)
    return model


def predict_human_item(model: NewsV10Model, item: CollectionItem) -> dict[str, Any]:
    return predict_source(model, item.blinded, sample_id=item.sample_id, split=item.split)


def predict_source(
    model: NewsV10Model,
    source: Mapping[str, Any],
    *,
    sample_id: str,
    split: str = "",
) -> dict[str, Any]:
    article_x = model.article_features.transform([article_text(source)])
    extraction_decision = str(model.extraction_model.predict(article_x)[0])
    role = str(model.role_model.predict(article_x)[0])
    origin = str(model.origin_model.predict(article_x)[0])
    labels: list[dict[str, Any]] = []
    if extraction_decision == "labeled":
        candidates = source_candidates(source)
        if candidates:
            unit_x = model.unit_features.transform([
                unit_text(source, candidate) for candidate in candidates
            ])
            scope_probability = _positive_probability(model.scope_model, unit_x)
            selected = np.flatnonzero(scope_probability >= model.config.scope_threshold)
            if len(selected):
                selected_x = unit_x[selected]
                directions = model.direction_model.predict(selected_x)
                direction_probabilities = model.direction_model.predict_proba(selected_x)
                concept_probabilities = _ovr_probabilities(model.concept_model, selected_x)
                eligibility_probabilities = _ovr_probabilities(model.eligibility_model, selected_x)
                for local_index, row_index in enumerate(selected):
                    candidate = candidates[int(row_index)]
                    direction = str(directions[local_index])
                    class_probabilities = {
                        str(name): float(value)
                        for name, value in zip(
                            model.direction_model.classes_, direction_probabilities[local_index]
                        )
                    }
                    score = class_probabilities.get("positive", 0.0) - class_probabilities.get("negative", 0.0)
                    concepts = [
                        name
                        for name, probability in zip(
                            model.concept_classes, concept_probabilities[local_index]
                        )
                        if probability >= model.config.concept_threshold
                    ]
                    eligibility = {
                        name: bool(probability >= model.config.eligibility_threshold)
                        for name, probability in zip(
                            model.eligibility_classes, eligibility_probabilities[local_index]
                        )
                    }
                    labels.append({
                        "ticker": str(candidate["canonical_instrument_id"]),
                        "canonical_instrument_id": str(candidate["canonical_instrument_id"]),
                        "unit_role": "v10_candidate_issuer_unit",
                        "classification": {
                            "content_role": role,
                            "source_origin": origin,
                            "event_concepts": concepts,
                            "semantic_direction": direction,
                            "semantic_score": round(score, 6),
                            "direction_confidence": round(max(class_probabilities.values()), 6),
                        },
                        **eligibility,
                    })
    return {
        "version": model.version,
        "sample_id": sample_id,
        "split": split,
        "source_id": str(source.get("source_id") or ""),
        "extraction_decision": extraction_decision,
        "content_role": role,
        "source_origin": origin,
        "labels": labels,
    }


def teacher_candidate_rows(articles: Sequence[TeacherArticle]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for article in articles:
        truth = {
            str(unit.get("canonical_instrument_id") or unit.get("ticker") or "").upper(): unit
            for unit in article.label.get("labels") or ()
            if unit.get("canonical_instrument_id") or unit.get("ticker")
        }
        candidates = source_candidates(article.source)
        candidate_ids = {str(row["canonical_instrument_id"]).upper() for row in candidates}
        missing = set(truth) - candidate_ids
        if missing:
            raise RuntimeError(f"Teacher candidates miss {article.sample_id}: {sorted(missing)}")
        for candidate in candidates:
            identifier = str(candidate["canonical_instrument_id"]).upper()
            rows.append({
                "sample_id": article.sample_id,
                "ticker": identifier,
                "truth": truth.get(identifier),
                "text": unit_text(article.source, candidate),
            })
    return rows


def source_candidates(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    publication = source.get("publication") or {}
    rendered = source.get("rendered_product") or {}
    return enrich_candidate_rows(
        source.get("point_in_time_issuer_candidates") or (),
        title=str(publication.get("title") or ""),
        teaser=str(publication.get("teaser") or ""),
        rendered_text=str(rendered.get("text") or ""),
        authoritative_identifiers=publication.get("provider_tickers") or (),
    )


def article_text(source: Mapping[str, Any]) -> str:
    publication = source.get("publication") or {}
    rendered = source.get("rendered_product") or {}
    return "\n".join((
        f"TITLE {publication.get('title') or ''}",
        f"TEASER {publication.get('teaser') or ''}",
        f"AUTHOR {publication.get('author') or ''}",
        f"PROVIDER {publication.get('provider') or ''}",
        "CHANNELS " + " ".join(map(str, publication.get("channels") or ())),
        "TAGS " + " ".join(map(str, publication.get("provider_tags") or ())),
        f"BODY {str(rendered.get('text') or '')[:16_000]}",
    ))


def unit_text(source: Mapping[str, Any], candidate: Mapping[str, Any]) -> str:
    publication = source.get("publication") or {}
    rendered = source.get("rendered_product") or {}
    full_text = article_text(source)
    terms = [
        str(candidate.get("canonical_instrument_id") or ""),
        str(candidate.get("display_symbol") or ""),
    ]
    terms.extend(
        str(value).split(":", 1)[1]
        for value in candidate.get("identity_evidence") or ()
        if str(value).startswith("issuer_alias:")
    )
    terms = sorted({value.strip() for value in terms if value.strip()}, key=len, reverse=True)
    context_lines = _matching_context_lines(str(rendered.get("text") or ""), terms)
    masked_full = _mask_terms(full_text, terms)
    masked_context = _mask_terms("\n".join(context_lines), terms)
    evidence_families = sorted({
        str(value).split(":", 1)[0]
        for value in candidate.get("identity_evidence") or ()
        if value
    })
    return "\n".join((
        f"TARGET_TYPE {candidate.get('instrument_type') or 'unknown'}",
        "TARGET_EVIDENCE " + " ".join(evidence_families),
        f"TARGET_CONTEXT {masked_context}",
        f"ARTICLE {masked_full}",
        "CHANNELS " + " ".join(map(str, publication.get("channels") or ())),
        "TAGS " + " ".join(map(str, publication.get("provider_tags") or ())),
    ))


def _matching_context_lines(text: str, terms: Sequence[str]) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    matched: set[int] = set()
    lowered = [line.casefold() for line in lines]
    needles = [term.casefold() for term in terms if len(term) >= 2]
    for index, line in enumerate(lowered):
        if any(_term_present(line, needle) for needle in needles):
            matched.update(range(max(0, index - 1), min(len(lines), index + 2)))
    return [lines[index] for index in sorted(matched)]


def _term_present(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text, re.I))


def _mask_terms(text: str, terms: Sequence[str]) -> str:
    value = text
    for term in terms:
        value = re.sub(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
            " TARGET_ENTITY ",
            value,
            flags=re.I,
        )
    return value


def _forest(
    trees: int,
    config: ForestConfig,
    *,
    balanced: bool,
    workers: int | None = None,
) -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=trees,
        criterion="entropy",
        max_features="sqrt",
        max_leaf_nodes=config.max_leaf_nodes,
        min_samples_leaf=config.min_samples_leaf,
        bootstrap=True,
        max_samples=config.max_samples,
        class_weight="balanced_subsample" if balanced else None,
        n_jobs=config.workers if workers is None else workers,
        random_state=RANDOM_SEED,
        verbose=0,
    )


def _positive_probability(model: RandomForestClassifier, x: np.ndarray) -> np.ndarray:
    classes = list(model.classes_)
    if 1 not in classes:
        return np.zeros(x.shape[0], dtype=np.float64)
    return model.predict_proba(x)[:, classes.index(1)]


def _ovr_probabilities(model: OneVsRestClassifier, x: np.ndarray) -> np.ndarray:
    probabilities = np.asarray(model.predict_proba(x), dtype=np.float64)
    return probabilities.reshape(x.shape[0], -1)


def generate_human_predictions(
    model: NewsV10Model,
    items: Iterable[CollectionItem],
    *,
    output_dir: Path,
) -> None:
    assert_runtime_root(output_dir)
    for index, item in enumerate(items, 1):
        target = output_dir / f"{item.sample_id}.json"
        if target.exists() and _prediction_matches_item(read_json(target), item):
            continue
        write_json_atomic(target, predict_human_item(model, item))
        if index % 100 == 0:
            print(f"V10 HUMAN {index:,}", flush=True)


def _prediction_matches_item(
    prediction: Mapping[str, Any], item: CollectionItem
) -> bool:
    return (
        str(prediction.get("version") or "") == V10_VERSION
        and str(prediction.get("sample_id") or "") == item.sample_id
        and str(prediction.get("split") or "") == item.split
        and str(prediction.get("source_id") or "")
        == str(item.blinded.get("source_id") or "")
    )


def human_prediction_cache_complete(
    items: Iterable[CollectionItem], *, output_dir: Path
) -> bool:
    for item in items:
        target = output_dir / f"{item.sample_id}.json"
        if not target.exists() or not _prediction_matches_item(read_json(target), item):
            return False
    return True


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def config_dict(config: ForestConfig) -> dict[str, Any]:
    return asdict(config)
