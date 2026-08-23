from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
from scipy import sparse
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

from research.mlops.clickhouse import ClickHouseHttpClient

from .provider_filter_analysis import canonical_json, iter_jsonl, parse_utc, sha256_path


CONTRACT_VERSION = "news_structured_feature_contract_v1"
MODEL_VERSION = "news_structured_metadata_rf_v1"
SEED = 20260822
DEFAULT_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v6_provider_path_exceptions_final\ARTICLE_FEATURES.jsonl"
)
DEFAULT_MARKET_CAP = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_market_cap_context_analysis_v3\ARTICLE_MARKET_CAP_FEATURES.jsonl"
)
DEFAULT_OUTPUT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\structured_metadata_rf_v1"
)
RF_PARAMETERS = {
    "n_estimators": 400,
    "max_depth": 30,
    "min_samples_leaf": 2,
    "max_features": "sqrt",
    "bootstrap": True,
    "max_samples": 0.70,
    "class_weight": "balanced_subsample",
    "n_jobs": 12,
    "random_state": SEED,
}
DEVELOPMENT_CANDIDATES = (
    {"max_depth": 24, "min_samples_leaf": 4, "max_features": "sqrt", "max_samples": 0.70},
    {"max_depth": 30, "min_samples_leaf": 2, "max_features": "sqrt", "max_samples": 0.70},
    {"max_depth": None, "min_samples_leaf": 2, "max_features": "sqrt", "max_samples": 0.80},
    {"max_depth": None, "min_samples_leaf": 1, "max_features": "log2", "max_samples": 0.80},
)
BOOLEAN_FIELDS = (
    "analyst_rating", "earnings_preview", "halt", "index_or_listing",
    "list_or_screener", "macro", "market_recap", "material_event",
    "price_target", "question_title", "short_interest", "technical_or_valuation",
    "title_only", "why_moving", "any_ticker_first_session",
    "all_tickers_first_session", "any_ticker_news_within_5m",
    "any_ticker_news_within_30m",
)
NUMERIC_FIELDS = (
    "ticker_count", "rendered_chars", "min_ticker_session_ordinal",
    "max_ticker_session_ordinal", "min_seconds_since_previous_ticker_news",
    "max_seconds_since_previous_ticker_news",
)
CAP_BUCKETS = (
    "nano_lt_50m", "micro_50m_300m", "small_300m_2b",
    "mid_2b_10b", "large_10b_200b", "mega_gte_200b",
)
RAW_CATALOG_FAMILIES = ("provider", "tag", "channel", "quality")
FAMILY_GROUP = {
    "provider": "provider_metadata", "tag": "provider_tags",
    "channel": "provider_channels", "quality": "source_quality",
    "session_segment": "publication_time", "hour_et": "publication_time",
    "weekday_et": "publication_time", "month": "publication_time",
    "ticker_count_bin": "ticker_structure", "rendered_chars_bin": "article_shape",
    "min_recency_bin": "ticker_history", "min_ordinal_bin": "ticker_history",
    "market_cap_coverage": "market_cap", "market_cap_min_bucket": "market_cap",
    "market_cap_max_bucket": "market_cap", "market_cap_bucket_set": "market_cap",
    "market_cap_source_set": "market_cap", "market_cap_age_bucket": "market_cap",
}


def _write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_json_idempotent(path: Path, value: Mapping[str, Any]) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise FileExistsError(f"existing artifact differs: {path}")
        return
    path.write_text(rendered, encoding="utf-8", newline="\n")


def _write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
    return count


def _write_csv_new(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for name in row:
            if name not in fields:
                fields.append(name)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def normalize_category(value: Any) -> str:
    normalized = " ".join(str(value or "").strip().casefold().split())
    if len(normalized) <= 256:
        return normalized
    return normalized[:192] + "#sha256=" + hashlib.sha256(normalized.encode()).hexdigest()


def _bucket(value: Any, boundaries: Sequence[tuple[float, str]], missing: str = "missing") -> str:
    if value is None or value == "":
        return missing
    parsed = max(0.0, float(value))
    for maximum, name in boundaries:
        if parsed <= maximum:
            return name
    return boundaries[-1][1]


def ticker_count_bin(value: Any) -> str:
    count = int(value or 0)
    if count <= 0: return "0"
    if count == 1: return "1"
    if count == 2: return "2"
    if count <= 5: return "3_5"
    if count <= 10: return "6_10"
    return "gt_10"


def rendered_chars_bin(value: Any) -> str:
    return _bucket(value, ((200, "lte_200"), (500, "201_500"), (1000, "501_1000"), (2500, "1001_2500"), (5000, "2501_5000"), (math.inf, "gt_5000")))


def recency_bin(value: Any) -> str:
    return _bucket(value, ((300, "lt_5m"), (1800, "5_30m"), (3600, "30_60m"), (14400, "1_4h"), (86400, "4_24h"), (math.inf, "gt_24h")))


def ordinal_bin(value: Any) -> str:
    return _bucket(value, ((1, "1"), (2, "2"), (5, "3_5"), (10, "6_10"), (math.inf, "gt_10")))


def load_historical_catalog(client: ClickHouseHttpClient, database: str = "q_live") -> list[dict[str, Any]]:
    sql = f"""
SELECT tupleElement(item, 1) AS family,
  lowerUTF8(trimBoth(tupleElement(item, 2))) AS category,
  count() AS support, min(published_date) AS first_date, max(published_date) AS last_date
FROM `{database}`.`benzinga_news_event_v2` FINAL
ARRAY JOIN arrayConcat(
  [tuple('provider', provider)],
  arrayMap(value -> tuple('tag', value), provider_tags),
  arrayMap(value -> tuple('channel', value), channels),
  arrayMap(value -> tuple('quality', value), content_quality_flags)
) AS item
WHERE published_date >= makeDate(2010, 1, 1)
  AND published_date < makeDate(2026, 1, 1)
  AND notEmpty(trimBoth(tupleElement(item, 2)))
GROUP BY family, category
ORDER BY family, category
FORMAT JSONEachRow
"""
    collapsed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in client.iter_json_each_row(sql):
        family = str(row["family"])
        category = normalize_category(row["category"])
        key = (family, category)
        target = collapsed.setdefault(key, {
            "family": family, "category": category, "historical_support": 0,
            "first_date": str(row["first_date"]), "last_date": str(row["last_date"]),
        })
        target["historical_support"] += int(row["support"])
        target["first_date"] = min(str(target["first_date"]), str(row["first_date"]))
        target["last_date"] = max(str(target["last_date"]), str(row["last_date"]))
    return [collapsed[key] for key in sorted(collapsed)]


def _raw_categories(row: Mapping[str, Any], cap: Mapping[str, Any]) -> dict[str, set[str]]:
    published = parse_utc(row["published_at_text"])
    categories: dict[str, set[str]] = defaultdict(set)
    categories["provider"].add(normalize_category(row.get("provider")))
    categories["tag"].update(normalize_category(value) for value in row.get("provider_tags") or () if normalize_category(value))
    categories["channel"].update(normalize_category(value) for value in row.get("channels") or () if normalize_category(value))
    categories["quality"].update(normalize_category(value) for value in row.get("content_quality_flags") or () if normalize_category(value))
    categories["session_segment"].add(normalize_category(row.get("session_segment")))
    categories["hour_et"].add(str(row.get("hour_et") if row.get("hour_et") is not None else published.hour))
    categories["weekday_et"].add(normalize_category(row.get("weekday_et") or published.strftime("%a")))
    categories["month"].add(str(published.month))
    categories["ticker_count_bin"].add(ticker_count_bin(row.get("ticker_count")))
    categories["rendered_chars_bin"].add(rendered_chars_bin(row.get("rendered_chars")))
    categories["min_recency_bin"].add(recency_bin(row.get("min_seconds_since_previous_ticker_news")))
    categories["min_ordinal_bin"].add(ordinal_bin(row.get("min_ticker_session_ordinal")))
    for family, field in (
        ("market_cap_coverage", "market_cap_coverage"),
        ("market_cap_min_bucket", "market_cap_min_bucket"),
        ("market_cap_max_bucket", "market_cap_max_bucket"),
        ("market_cap_bucket_set", "market_cap_bucket_set"),
        ("market_cap_source_set", "market_cap_source_set"),
        ("market_cap_age_bucket", "market_cap_max_age_bucket"),
    ):
        categories[family].add(normalize_category(cap.get(field) or "missing"))
    return categories


def _fixed_features() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in BOOLEAN_FIELDS:
        result[f"bool:{name}"] = "lexical_flags" if name not in {
            "title_only", "question_title", "any_ticker_first_session", "all_tickers_first_session",
            "any_ticker_news_within_5m", "any_ticker_news_within_30m",
        } else "source_quality" if name in {"title_only", "question_title"} else "ticker_history"
    for name in NUMERIC_FIELDS:
        group = "ticker_structure" if name == "ticker_count" else "article_shape" if name == "rendered_chars" else "ticker_history"
        result[f"numeric:{name}"] = group
        result[f"missing:{name}"] = group
    for name in ("hour_sin", "hour_cos", "dow_sin", "dow_cos", "year_sin", "year_cos"):
        result[f"numeric:{name}"] = "publication_time"
    for name in (
        "market_cap_known_ticker_count", "market_cap_missing_fraction", "market_cap_min_log",
        "market_cap_median_log", "market_cap_max_log", "market_cap_max_age_days_log",
    ):
        result[f"numeric:{name}"] = "market_cap"
        result[f"missing:{name}"] = "market_cap"
    for bucket in CAP_BUCKETS:
        result[f"numeric:market_cap_count:{bucket}"] = "market_cap"
        result[f"numeric:market_cap_fraction:{bucket}"] = "market_cap"
    return result


def _numeric_values(row: Mapping[str, Any], cap: Mapping[str, Any]) -> dict[str, float]:
    published = parse_utc(row["published_at_text"])
    # Benzinga's market-session fields are Eastern-time authorities.  Retain
    # minute/second precision from UTC because the offset changes hours, not
    # sub-hour position, while taking hour and weekday from the ET fields.
    hour = float(row.get("hour_et", published.hour)) + published.minute / 60 + published.second / 3600
    weekday_name = str(row.get("weekday_et") or "").casefold()[:3]
    dow = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}.get(
        weekday_name, published.weekday()
    )
    day = published.timetuple().tm_yday
    values = {
        "numeric:hour_sin": math.sin(2 * math.pi * hour / 24),
        "numeric:hour_cos": math.cos(2 * math.pi * hour / 24),
        "numeric:dow_sin": math.sin(2 * math.pi * dow / 7),
        "numeric:dow_cos": math.cos(2 * math.pi * dow / 7),
        "numeric:year_sin": math.sin(2 * math.pi * day / 365.2425),
        "numeric:year_cos": math.cos(2 * math.pi * day / 365.2425),
    }
    for name in NUMERIC_FIELDS:
        raw = row.get(name)
        values[f"missing:{name}"] = float(raw is None or raw == "")
        if raw is not None and raw != "":
            parsed = max(0.0, float(raw))
            values[f"numeric:{name}"] = parsed if name in {"ticker_count", "min_ticker_session_ordinal", "max_ticker_session_ordinal"} else math.log1p(parsed)
    cap_fields = {
        "market_cap_known_ticker_count": cap.get("market_cap_known_ticker_count"),
        "market_cap_missing_fraction": cap.get("market_cap_missing_fraction"),
        "market_cap_min_log": cap.get("market_cap_min"),
        "market_cap_median_log": cap.get("market_cap_median"),
        "market_cap_max_log": cap.get("market_cap_max"),
        "market_cap_max_age_days_log": cap.get("market_cap_max_age_days"),
    }
    for name, raw in cap_fields.items():
        values[f"missing:{name}"] = float(raw is None or raw == "")
        if raw is not None and raw != "":
            parsed = max(0.0, float(raw))
            values[f"numeric:{name}"] = parsed if name in {"market_cap_known_ticker_count", "market_cap_missing_fraction"} else math.log1p(parsed)
    ticker_rows = list(cap.get("market_cap_tickers") or ())
    counts = Counter(str(item.get("market_cap_bucket") or "missing") for item in ticker_rows)
    denominator = max(1, int(row.get("ticker_count") or 0))
    for bucket in CAP_BUCKETS:
        values[f"numeric:market_cap_count:{bucket}"] = float(counts[bucket])
        values[f"numeric:market_cap_fraction:{bucket}"] = counts[bucket] / denominator
    return values


def _feature_row(
    row: Mapping[str, Any],
    cap: Mapping[str, Any],
    *,
    active: Mapping[str, set[str]],
    historical: Mapping[str, set[str]],
) -> dict[str, float]:
    values = _numeric_values(row, cap)
    for name in BOOLEAN_FIELDS:
        values[f"bool:{name}"] = float(bool(row.get(name)))
    for family, categories in _raw_categories(row, cap).items():
        for category in categories:
            if category in active.get(family, set()):
                encoded = category
            elif category in historical.get(family, set()):
                encoded = "__known_historical_untrained__"
            else:
                encoded = "__unseen_post_training__"
            values[f"cat:{family}={encoded}"] = 1.0
    return values


def _row_index(row: Mapping[str, Any], cap: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(row["source_id"]), "published_at_utc": str(row["published_at_text"]),
        "split": str(row["split"]), "label": str(row["label"]),
        "session_segment": str(row.get("session_segment") or ""),
        "market_cap_max_bucket": str(cap.get("market_cap_max_bucket") or "missing"),
    }


def _build_matrix(
    rows: Sequence[Mapping[str, Any]],
    cap_by_id: Mapping[str, Mapping[str, Any]],
    feature_index: Mapping[str, int],
    active: Mapping[str, set[str]],
    historical: Mapping[str, set[str]],
) -> sparse.csr_matrix:
    data: list[float] = []
    indices: list[int] = []
    indptr = [0]
    for row in rows:
        features = _feature_row(row, cap_by_id[str(row["source_id"])], active=active, historical=historical)
        pairs = sorted((feature_index[name], value) for name, value in features.items() if name in feature_index and value != 0)
        indices.extend(index for index, _value in pairs)
        data.extend(float(value) for _index, value in pairs)
        indptr.append(len(data))
    return sparse.csr_matrix(
        (np.asarray(data, dtype=np.float32), np.asarray(indices, dtype=np.int32), np.asarray(indptr, dtype=np.int64)),
        shape=(len(rows), len(feature_index)), dtype=np.float32,
    )


def build_contract_and_matrices(
    *,
    client: ClickHouseHttpClient,
    feature_path: Path = DEFAULT_FEATURES,
    market_cap_path: Path = DEFAULT_MARKET_CAP,
    output_root: Path = DEFAULT_OUTPUT,
    database: str = "q_live",
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.mkdir(parents=True)
    started = time.time()
    rows = [row for row in iter_jsonl(feature_path) if str(row.get("label")) in {"eligible", "ineligible"}]
    rows.sort(key=lambda row: (str(row["published_at_text"]), str(row["source_id"])))
    if len(rows) != 346_103 or len({str(row["source_id"]) for row in rows}) != len(rows):
        raise ValueError(f"unexpected decisive feature population: {len(rows)}")
    cap_by_id = {str(row["source_id"]): row for row in iter_jsonl(market_cap_path)}
    if set(cap_by_id) != {str(row["source_id"]) for row in rows}:
        raise ValueError("market-cap/feature membership mismatch")
    train_rows = [row for row in rows if str(row["split"]) == "discovery_2025"]
    test_rows = [row for row in rows if str(row["split"]) != "discovery_2025"]
    if len(train_rows) != 203_847 or len(test_rows) != 142_256:
        raise ValueError("unexpected chronological partition counts")

    catalog = load_historical_catalog(client, database)
    historical: dict[str, set[str]] = defaultdict(set)
    catalog_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in catalog:
        historical[str(item["family"])].add(str(item["category"]))
        catalog_by_key[(str(item["family"]), str(item["category"]))] = item
    active: dict[str, set[str]] = defaultdict(set)
    active_support: Counter[tuple[str, str]] = Counter()
    for row in train_rows:
        for family, values in _raw_categories(row, cap_by_id[str(row["source_id"])]).items():
            active[family].update(values)
            active_support.update((family, value) for value in values)
    for family in active:
        active[family].update(("__known_historical_untrained__", "__unseen_post_training__"))

    feature_groups = _fixed_features()
    for family, categories in active.items():
        group = FAMILY_GROUP[family]
        for category in categories:
            feature_groups[f"cat:{family}={category}"] = group
    feature_names = sorted(feature_groups)
    feature_index = {name: index for index, name in enumerate(feature_names)}
    if any(name.startswith("cat:ticker=") for name in feature_names):
        raise ValueError("exact ticker identity entered the feature contract")
    if any("tfidf" in name.casefold() for name in feature_names):
        raise ValueError("TF-IDF entered the structured feature contract")

    x_train = _build_matrix(train_rows, cap_by_id, feature_index, active, historical)
    x_test = _build_matrix(test_rows, cap_by_id, feature_index, active, historical)
    y_train = np.asarray([row["label"] == "eligible" for row in train_rows], dtype=np.int8)
    y_test = np.asarray([row["label"] == "eligible" for row in test_rows], dtype=np.int8)
    sparse.save_npz(output_root / "X_2025_TRAIN.npz", x_train, compressed=True)
    sparse.save_npz(output_root / "X_2026_TEST.npz", x_test, compressed=True)
    np.save(output_root / "Y_2025_TRAIN.npy", y_train, allow_pickle=False)
    np.save(output_root / "Y_2026_TEST.npy", y_test, allow_pickle=False)
    _write_jsonl_new(output_root / "ROWS_2025_TRAIN.jsonl", (_row_index(row, cap_by_id[str(row["source_id"])]) for row in train_rows))
    _write_jsonl_new(output_root / "ROWS_2026_TEST.jsonl", (_row_index(row, cap_by_id[str(row["source_id"])]) for row in test_rows))

    catalog_rows: list[dict[str, Any]] = []
    for item in catalog:
        key = (str(item["family"]), str(item["category"]))
        catalog_rows.append({**item, "training_2025_support": active_support[key], "active_model_category": active_support[key] > 0})
    for family, categories in active.items():
        if family in RAW_CATALOG_FAMILIES:
            continue
        for category in sorted(categories - {"__known_historical_untrained__", "__unseen_post_training__"}):
            catalog_rows.append({
                "family": family, "category": category, "historical_support": "",
                "first_date": "2025-derived", "last_date": "2025-derived",
                "training_2025_support": active_support[(family, category)], "active_model_category": True,
            })
    _write_csv_new(output_root / "CATEGORY_CATALOG_2010_2025.csv", catalog_rows)
    contract = {
        "contract_version": CONTRACT_VERSION,
        "status": "frozen_before_2026_model_evaluation",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "catalog_window": ["2010-01-01", "2025-12-31"],
        "training_window": [
            str(train_rows[0]["published_at_text"]),
            str(train_rows[-1]["published_at_text"]),
        ],
        "test_window": [
            str(test_rows[0]["published_at_text"]),
            str(test_rows[-1]["published_at_text"]),
        ],
        "feature_names": feature_names,
        "feature_groups": [feature_groups[name] for name in feature_names],
        "feature_count": len(feature_names),
        "category_catalog_rows": len(catalog_rows),
        "active_categories": {family: sorted(values) for family, values in sorted(active.items())},
        "historical_category_counts": {family: len(values) for family, values in sorted(historical.items())},
        "normalization": "unicode casefold, trim, collapse whitespace; values over 256 characters retain a prefix plus SHA-256",
        "excluded_inputs": [
            "rendered text and TF-IDF", "source_id as a feature", "exact ticker identity",
            "deterministic synthesis routes, rules, or predictions", "label authority and certification metadata",
            "update_delay_seconds because initial-decision availability is not guaranteed",
        ],
        "inputs": {
            "article_features": {"path": str(feature_path), "sha256": sha256_path(feature_path)},
            "market_cap_features": {"path": str(market_cap_path), "sha256": sha256_path(market_cap_path)},
        },
        "matrix": {
            "train_shape": list(x_train.shape), "train_nnz": int(x_train.nnz),
            "test_shape": list(x_test.shape), "test_nnz": int(x_test.nnz),
            "train_labels": {"eligible": int(y_train.sum()), "ineligible": int(len(y_train) - y_train.sum())},
            "test_labels": {"eligible": int(y_test.sum()), "ineligible": int(len(y_test) - y_test.sum())},
        },
        "build_seconds": time.time() - started,
    }
    contract["feature_dictionary_sha256"] = hashlib.sha256(canonical_json({
        "feature_names": contract["feature_names"], "feature_groups": contract["feature_groups"],
    }).encode()).hexdigest()
    _write_json_new(output_root / "FEATURE_CONTRACT.json", contract)
    return contract


def binary_metrics(y_true: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, Any]:
    predicted = (probability >= threshold).astype(np.int8)
    matrix = confusion_matrix(y_true, predicted, labels=[0, 1])
    return {
        "articles": int(len(y_true)), "threshold": float(threshold),
        "accuracy": float(accuracy_score(y_true, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, predicted)),
        "eligible_precision": float(precision_score(y_true, predicted, zero_division=0)),
        "eligible_recall": float(recall_score(y_true, predicted, zero_division=0)),
        "eligible_f1": float(f1_score(y_true, predicted, zero_division=0)),
        "macro_f1": float(f1_score(y_true, predicted, average="macro", zero_division=0)),
        "ineligible_recall": float(recall_score(1 - y_true, 1 - predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "average_precision": float(average_precision_score(y_true, probability)),
        "brier_score": float(brier_score_loss(y_true, probability)),
        "log_loss": float(log_loss(y_true, np.column_stack((1 - probability, probability)), labels=[0, 1])),
        "confusion_matrix_labels": ["ineligible", "eligible"],
        "confusion_matrix": matrix.tolist(),
    }


def _select_threshold(y_true: np.ndarray, probability: np.ndarray) -> tuple[float, list[dict[str, float]]]:
    curve = []
    for threshold in np.arange(0.20, 0.801, 0.01):
        curve.append({
            "threshold": float(threshold),
            "balanced_accuracy": float(balanced_accuracy_score(y_true, probability >= threshold)),
            "eligible_f1": float(f1_score(y_true, probability >= threshold, zero_division=0)),
        })
    best = max(curve, key=lambda row: (row["balanced_accuracy"], row["eligible_f1"], -abs(row["threshold"] - 0.5)))
    return float(best["threshold"]), curve


def _calibration(y_true: np.ndarray, probability: np.ndarray) -> list[dict[str, Any]]:
    bins = np.minimum(9, (probability * 10).astype(int))
    result = []
    for index in range(10):
        mask = bins == index
        if not np.any(mask):
            continue
        result.append({
            "bin": index, "articles": int(mask.sum()),
            "mean_probability": float(probability[mask].mean()),
            "eligible_rate": float(y_true[mask].mean()),
        })
    return result


def _slice_metrics(index_rows: Sequence[Mapping[str, Any]], y: np.ndarray, probability: np.ndarray, threshold: float, field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for position, row in enumerate(index_rows):
        value = str(row[field]) if field != "month" else str(row["published_at_utc"])[:7]
        groups[value].append(position)
    return [{field: value, **binary_metrics(y[positions], probability[positions], threshold)} for value, positions in sorted(groups.items())]


def _permutation_importance(
    model: RandomForestClassifier,
    x: sparse.csr_matrix,
    y: np.ndarray,
    feature_names: Sequence[str],
    feature_groups: Sequence[str],
    mdi: np.ndarray,
    *,
    sample_size: int = 4_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = np.random.default_rng(SEED)
    positions = np.sort(rng.choice(len(y), size=min(sample_size, len(y)), replace=False))
    dense = x[positions].toarray().astype(np.float32, copy=False)
    truth = y[positions]
    baseline = balanced_accuracy_score(truth, model.predict(dense))
    top = np.argsort(mdi)[::-1][:30]
    individual = []
    for column in top:
        drops = []
        original = dense[:, column].copy()
        for _ in range(2):
            dense[:, column] = original[rng.permutation(len(original))]
            drops.append(float(baseline - balanced_accuracy_score(truth, model.predict(dense))))
        dense[:, column] = original
        individual.append({
            "feature": feature_names[column], "feature_group": feature_groups[column],
            "mdi_importance": float(mdi[column]), "permutation_balanced_accuracy_drop": float(np.mean(drops)),
            "permutation_drop_std": float(np.std(drops)), "sample_articles": len(positions),
        })
    by_group: dict[str, list[int]] = defaultdict(list)
    for column, group in enumerate(feature_groups):
        by_group[str(group)].append(column)
    grouped = []
    for group, columns in sorted(by_group.items()):
        drops = []
        original = dense[:, columns].copy()
        for _ in range(3):
            dense[:, columns] = original[rng.permutation(len(original)), :]
            drops.append(float(baseline - balanced_accuracy_score(truth, model.predict(dense))))
        dense[:, columns] = original
        grouped.append({
            "feature_group": group, "features": len(columns),
            "mdi_importance": float(mdi[columns].sum()),
            "permutation_balanced_accuracy_drop": float(np.mean(drops)),
            "permutation_drop_std": float(np.std(drops)), "sample_articles": len(positions),
        })
    grouped.sort(key=lambda row: -row["permutation_balanced_accuracy_drop"])
    return individual, grouped


def train_and_evaluate(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    contract_path = output_root / "FEATURE_CONTRACT.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("status") != "frozen_before_2026_model_evaluation":
        raise ValueError("feature contract is not frozen")
    x_train = sparse.load_npz(output_root / "X_2025_TRAIN.npz").tocsr()
    x_test = sparse.load_npz(output_root / "X_2026_TEST.npz").tocsr()
    y_train = np.load(output_root / "Y_2025_TRAIN.npy", allow_pickle=False)
    y_test = np.load(output_root / "Y_2026_TEST.npy", allow_pickle=False)
    train_index = list(iter_jsonl(output_root / "ROWS_2025_TRAIN.jsonl"))
    test_index = list(iter_jsonl(output_root / "ROWS_2026_TEST.jsonl"))
    feature_names = list(map(str, contract["feature_names"]))
    feature_groups = list(map(str, contract["feature_groups"]))
    if x_train.shape != (len(train_index), len(feature_names)) or x_test.shape != (len(test_index), len(feature_names)):
        raise ValueError("matrix/contract shape mismatch")

    started = time.time()
    validation_mask = np.asarray([str(row["published_at_utc"]) >= "2025-11-01" for row in train_index])
    development_results = []
    best_development: tuple[tuple[float, float], dict[str, Any], float, list[dict[str, float]]] | None = None
    for candidate_index, candidate in enumerate(DEVELOPMENT_CANDIDATES):
        candidate_parameters = {
            **RF_PARAMETERS, **candidate, "n_estimators": 120,
            "random_state": SEED + candidate_index,
        }
        development_model = RandomForestClassifier(**candidate_parameters)
        development_model.fit(x_train[~validation_mask], y_train[~validation_mask])
        validation_probability = development_model.predict_proba(x_train[validation_mask])[:, 1]
        candidate_threshold, candidate_curve = _select_threshold(
            y_train[validation_mask], validation_probability
        )
        candidate_metrics = binary_metrics(
            y_train[validation_mask], validation_probability, candidate_threshold
        )
        development_results.append({
            "candidate_index": candidate_index,
            "parameters": candidate_parameters,
            "selected_threshold": candidate_threshold,
            "validation_metrics": candidate_metrics,
        })
        rank = (candidate_metrics["balanced_accuracy"], candidate_metrics["eligible_f1"])
        if best_development is None or rank > best_development[0]:
            best_development = (rank, candidate_parameters, candidate_threshold, candidate_curve)
        del development_model
    assert best_development is not None
    _rank, selected_development_parameters, threshold, threshold_curve = best_development
    selected_index = int(selected_development_parameters["random_state"]) - SEED
    validation_metrics = development_results[selected_index]["validation_metrics"]

    final_parameters = {
        **selected_development_parameters,
        "n_estimators": RF_PARAMETERS["n_estimators"],
        "random_state": SEED,
    }
    model = RandomForestClassifier(**final_parameters)
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_test)[:, 1]
    prediction = (probability >= threshold).astype(np.int8)
    metrics = binary_metrics(y_test, probability, threshold)
    metrics_at_half = binary_metrics(y_test, probability, 0.5)
    mdi = np.asarray(model.feature_importances_, dtype=np.float64)
    individual_permutation, grouped_permutation = _permutation_importance(
        model, x_test, y_test, feature_names, feature_groups, mdi,
    )

    x_train_csc = x_train.tocsc()
    x_test_csc = x_test.tocsc()
    binary_mask = np.asarray([name.startswith(("cat:", "bool:", "missing:")) for name in feature_names])
    train_support = np.diff(x_train_csc.indptr)
    test_support = np.diff(x_test_csc.indptr)
    train_eligible_support = np.asarray(x_train_csc.T @ y_train).ravel()
    test_eligible_support = np.asarray(x_test_csc.T @ y_test).ravel()
    strength_rows = []
    for index, name in enumerate(feature_names):
        strength_rows.append({
            "feature": name, "feature_group": feature_groups[index],
            "mdi_importance": float(mdi[index]),
            "train_nonzero_support": int(train_support[index]),
            "test_nonzero_support": int(test_support[index]),
            "train_eligible_rate_when_on": float(train_eligible_support[index] / train_support[index]) if binary_mask[index] and train_support[index] else "",
            "test_eligible_rate_when_on": float(test_eligible_support[index] / test_support[index]) if binary_mask[index] and test_support[index] else "",
        })
    strength_rows.sort(key=lambda row: -float(row["mdi_importance"]))
    _write_csv_new(output_root / "FEATURE_STRENGTH.csv", strength_rows)
    _write_csv_new(output_root / "TOP_FEATURE_PERMUTATION_IMPORTANCE.csv", individual_permutation)
    _write_csv_new(output_root / "GROUPED_PERMUTATION_IMPORTANCE.csv", grouped_permutation)

    predictions_path = output_root / "PREDICTIONS_2026.jsonl"
    disagreement_path = output_root / "LABEL_DISAGREEMENTS_2026.jsonl"
    high_confidence_path = output_root / "HIGH_CONFIDENCE_DISAGREEMENTS_2026.jsonl"
    prediction_rows = []
    disagreement_rows = []
    for row, score, predicted in zip(test_index, probability, prediction, strict=True):
        item = {
            **row, "eligible_probability": float(score),
            "predicted_label": "eligible" if predicted else "ineligible",
            "label_disagreement": bool(predicted != (row["label"] == "eligible")),
        }
        prediction_rows.append(item)
        if item["label_disagreement"]:
            disagreement_rows.append(item)
    _write_jsonl_new(predictions_path, prediction_rows)
    _write_jsonl_new(disagreement_path, disagreement_rows)
    high_confidence = [
        row for row in disagreement_rows
        if (row["predicted_label"] == "eligible" and float(row["eligible_probability"]) >= 0.90)
        or (row["predicted_label"] == "ineligible" and float(row["eligible_probability"]) <= 0.10)
    ]
    _write_jsonl_new(high_confidence_path, high_confidence)
    joblib.dump(model, output_root / "RANDOM_FOREST.joblib", compress=3)

    report = {
        "model_version": MODEL_VERSION, "contract_version": CONTRACT_VERSION,
        "status": "complete", "created_at_utc": datetime.now(UTC).isoformat(),
        "evaluation_role": "chronological retrospective 2026 test; no deterministic synthesis or TF-IDF inputs",
        "rf_parameters": final_parameters, "selected_threshold": threshold,
        "threshold_selection": {
            "development_train_articles": int((~validation_mask).sum()),
            "late_2025_validation_articles": int(validation_mask.sum()),
            "selected_candidate_index": selected_index,
            "candidate_results": development_results,
            "validation_metrics": validation_metrics, "curve": threshold_curve,
        },
        "training": {
            "articles": len(y_train), "eligible": int(y_train.sum()),
            "ineligible": int(len(y_train) - y_train.sum()),
        },
        "test": {
            "articles": len(y_test), "eligible": int(y_test.sum()),
            "ineligible": int(len(y_test) - y_test.sum()),
            "metrics_at_selected_threshold": metrics,
            "metrics_at_0_5": metrics_at_half,
            "calibration": _calibration(y_test, probability),
            "by_source_split": _slice_metrics(test_index, y_test, probability, threshold, "split"),
            "by_month": _slice_metrics(test_index, y_test, probability, threshold, "month"),
            "by_session_segment": _slice_metrics(test_index, y_test, probability, threshold, "session_segment"),
            "by_market_cap_max_bucket": _slice_metrics(test_index, y_test, probability, threshold, "market_cap_max_bucket"),
        },
        "importance": {
            "top_mdi": strength_rows[:100],
            "individual_permutation": individual_permutation,
            "grouped_permutation": grouped_permutation,
        },
        "disagreements": {
            "all": len(disagreement_rows), "share": len(disagreement_rows) / len(y_test),
            "high_confidence": len(high_confidence),
        },
        "train_seconds": time.time() - started,
        "limitations": [
            "2026 labels previously informed other research, so this is chronological comparison evidence rather than a pristine release holdout.",
            "Provider tags can contain people, firms, and other high-cardinality concepts that may proxy identity even though exact tickers are excluded.",
            "Feature importance is associative and correlated dimensions can divide or transfer importance.",
            "Permutation importance uses a deterministic 4,000-article test sample for bounded runtime.",
        ],
        "lineage": {
            "feature_contract_sha256": sha256_path(contract_path),
            "predictions_sha256": sha256_path(predictions_path),
            "disagreements_sha256": sha256_path(disagreement_path),
            "high_confidence_disagreements_sha256": sha256_path(high_confidence_path),
        },
    }
    _write_json_new(output_root / "REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    contract = json.loads((output_root / "FEATURE_CONTRACT.json").read_text(encoding="utf-8"))
    report = json.loads((output_root / "REPORT.json").read_text(encoding="utf-8"))
    x_train = sparse.load_npz(output_root / "X_2025_TRAIN.npz")
    x_test = sparse.load_npz(output_root / "X_2026_TEST.npz")
    train_rows = list(iter_jsonl(output_root / "ROWS_2025_TRAIN.jsonl"))
    test_rows = list(iter_jsonl(output_root / "ROWS_2026_TEST.jsonl"))
    names = list(map(str, contract["feature_names"]))
    required = (
        "FEATURE_CONTRACT.json", "CATEGORY_CATALOG_2010_2025.csv", "X_2025_TRAIN.npz",
        "X_2026_TEST.npz", "Y_2025_TRAIN.npy", "Y_2026_TEST.npy",
        "ROWS_2025_TRAIN.jsonl", "ROWS_2026_TEST.jsonl", "RANDOM_FOREST.joblib",
        "PREDICTIONS_2026.jsonl", "LABEL_DISAGREEMENTS_2026.jsonl",
        "HIGH_CONFIDENCE_DISAGREEMENTS_2026.jsonl", "FEATURE_STRENGTH.csv",
        "TOP_FEATURE_PERMUTATION_IMPORTANCE.csv", "GROUPED_PERMUTATION_IMPORTANCE.csv",
        "REPORT.json",
    )
    validation = {
        "status": "passed", "contract_version": CONTRACT_VERSION, "model_version": MODEL_VERSION,
        "train_rows": len(train_rows), "test_rows": len(test_rows), "features": len(names),
        "train_shape_matches": x_train.shape == (len(train_rows), len(names)),
        "test_shape_matches": x_test.shape == (len(test_rows), len(names)),
        "train_is_2025": all(str(row["published_at_utc"]).startswith("2025-") for row in train_rows),
        "test_is_2026": all(str(row["published_at_utc"]).startswith("2026-") for row in test_rows),
        "no_exact_ticker_features": not any(name.startswith("cat:ticker=") for name in names),
        "no_tfidf_features": not any("tfidf" in name.casefold() for name in names),
        "report_complete": report.get("status") == "complete",
        "all_required_outputs_exist": all((output_root / name).exists() for name in required),
    }
    if not all(value for value in validation.values() if isinstance(value, bool)):
        raise ValueError(f"structured RF validation failed: {validation}")
    validation_path = output_root / "VALIDATION.json"
    _write_json_idempotent(validation_path, validation)
    paths = [output_root / name for name in required] + [validation_path]
    _write_json_idempotent(output_root / "HASH_MANIFEST.json", {
        "contract_version": CONTRACT_VERSION, "model_version": MODEL_VERSION,
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in paths},
    })
    return validation
