from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import torch

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v16 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v16.config import LoaderConfig
from research.news_reaction_model.v16.data import _date_range_indices
from research.news_reaction_model.v16.market_context import (
    CURRENT_MARKET_FEATURE_NAMES,
)
from research.news_reaction_model.v16.market_data import (
    DayMarketData,
    daily_minute_bars_sql,
    parse_minute_bar_rows,
)
from research.news_reaction_model.v16.opportunity import (
    OPPORTUNITY_CLASS_NAMES,
    OPPORTUNITY_SPECS,
    OpportunityClass,
)
from research.news_reaction_model.v16.prepared import close_arrays, open_arrays
from research.news_reaction_model.v16.stock_state import STOCK_STATE_NAMES
from research.news_reaction_model.v16.time_features import EXCHANGE_TZ, parse_published_at_utc
from research.news_reaction_model.v16.prepare_data import q, qi


UTC = dt.timezone.utc
CLASS_NONE = int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY)
CLASS_UP = int(OpportunityClass.UPSIDE_DOMINANT)
CLASS_DOWN = int(OpportunityClass.DOWNSIDE_DOMINANT)
CLASS_ORDER = (CLASS_NONE, CLASS_UP, CLASS_DOWN)
ERROR_STUDY_VERSION = "news_reaction_v16_error_study_v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_MISORDERED_DATASET_VERSION = "news_reaction_openai_market_attention_dataset_v16"


@dataclass(slots=True)
class StudyInputs:
    checkpoint: Path
    predictions: Path
    output_dir: Path
    prepared_root: Path | None
    start: str
    end_exclusive: str
    news_enrichment: bool
    price_paths: bool
    embedding_neighbors: bool
    review_per_stratum: int
    minimum_slice_support: int
    neighbor_top_k: int
    neighbor_candidates: int
    neighbor_projection_dim: int
    neighbor_batch_size: int
    neighbor_device: str
    price_path_workers: int
    seed: int


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a reproducible V16 held-out error-attribution study from the "
            "official evaluation prediction export."
        )
    )
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prepared-root", default="")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    parser.add_argument(
        "--news-enrichment",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--price-paths",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--embedding-neighbors",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--review-per-stratum", type=int, default=100)
    parser.add_argument("--minimum-slice-support", type=int, default=100)
    parser.add_argument("--neighbor-top-k", type=int, default=5)
    parser.add_argument("--neighbor-candidates", type=int, default=128)
    parser.add_argument("--neighbor-projection-dim", type=int, default=64)
    parser.add_argument("--neighbor-batch-size", type=int, default=8_192)
    parser.add_argument("--neighbor-device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--price-path-workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=17)
    return parser.parse_args(list(argv) if argv is not None else None)


def inputs_from_args(args: argparse.Namespace) -> StudyInputs:
    return StudyInputs(
        checkpoint=Path(args.checkpoint),
        predictions=Path(args.predictions),
        output_dir=Path(args.output_dir),
        prepared_root=Path(args.prepared_root) if args.prepared_root else None,
        start=str(args.start),
        end_exclusive=str(args.end_exclusive),
        news_enrichment=bool(args.news_enrichment),
        price_paths=bool(args.price_paths),
        embedding_neighbors=bool(args.embedding_neighbors),
        review_per_stratum=max(1, int(args.review_per_stratum)),
        minimum_slice_support=max(1, int(args.minimum_slice_support)),
        neighbor_top_k=max(1, int(args.neighbor_top_k)),
        neighbor_candidates=max(int(args.neighbor_top_k), int(args.neighbor_candidates)),
        neighbor_projection_dim=max(8, int(args.neighbor_projection_dim)),
        neighbor_batch_size=max(256, int(args.neighbor_batch_size)),
        neighbor_device=str(args.neighbor_device),
        price_path_workers=max(1, int(args.price_path_workers)),
        seed=int(args.seed),
    )


def _decode(value: Any) -> str:
    return (
        bytes(value).split(b"\0", 1)[0].decode("utf-8", errors="strict")
        if isinstance(value, (bytes, np.bytes_))
        else str(value)
    )


def _identity(canonical_news_id: Any, ticker: Any) -> tuple[str, str]:
    return str(canonical_news_id), str(ticker).strip().upper()


def _sha256(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _jsonl_gzip(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
            count += 1
    return count


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _loader_from_checkpoint(checkpoint: Path, prepared_root: Path | None) -> LoaderConfig:
    if not checkpoint.exists():
        raise FileNotFoundError(f"Missing V16 checkpoint: {checkpoint}")
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    version = str(state.get("model_version") or state.get("config", {}).get("model_version") or "")
    if version and version != MODEL_VERSION:
        raise RuntimeError(f"Expected {MODEL_VERSION} checkpoint, received {version}.")
    loader = LoaderConfig(**state["config"]["loader"])
    if prepared_root is not None:
        loader.prepared_dataset_root = prepared_root
    return loader


def _read_predictions(path: Path) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], int]:
    if not path.exists():
        raise FileNotFoundError(f"Missing V16 prediction export: {path}")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = {
                "canonical_news_id",
                "ticker",
                "published_at_utc",
                "horizon",
                "predicted_class",
                "actual_class",
                "confidence",
                "probabilities",
                "actual_high_return",
                "actual_low_return",
            } - row.keys()
            if missing:
                raise RuntimeError(
                    f"Prediction row {line_number} lacks required fields {sorted(missing)}."
                )
            horizon = str(row["horizon"])
            if horizon not in HORIZONS:
                raise RuntimeError(f"Unknown horizon {horizon!r} at row {line_number}.")
            groups[_identity(row["canonical_news_id"], row["ticker"])].append(row)
    for key, rows in groups.items():
        rows.sort(key=lambda value: HORIZONS.index(str(value["horizon"])))
        if len({str(value["horizon"]) for value in rows}) != len(rows):
            raise RuntimeError(f"Duplicate prediction horizon for {key}.")
    return dict(groups), sum(len(rows) for rows in groups.values())


def _strengths(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    upside = max(
        max(float(row["actual_high_return"]), 0.0)
        * 100.0
        / OPPORTUNITY_SPECS[str(row["horizon"])].minimum_span_pct
        for row in rows
    )
    downside = max(
        max(-float(row["actual_low_return"]), 0.0)
        * 100.0
        / OPPORTUNITY_SPECS[str(row["horizon"])].minimum_span_pct
        for row in rows
    )
    return upside, downside


def _actual_decision(upside: float, downside: float) -> int:
    if upside <= 1.0 and downside <= 1.0:
        return CLASS_NONE
    if upside > downside:
        return CLASS_UP
    if downside > upside:
        return CLASS_DOWN
    return CLASS_NONE


def _vote_decision(votes: Counter[int]) -> tuple[int, int, int, bool]:
    ordered = sorted(
        ((int(votes.get(class_id, 0)), class_id) for class_id in CLASS_ORDER),
        key=lambda value: (-value[0], value[1]),
    )
    winner_votes, winner = ordered[0]
    runner_votes = ordered[1][0]
    tied = winner_votes == runner_votes
    return (CLASS_NONE if tied else winner), winner_votes, winner_votes - runner_votes, tied


def _error_type(predicted: int, actual: int) -> str:
    if predicted == actual:
        return "correct"
    if predicted == CLASS_UP and actual == CLASS_DOWN:
        return "false_long"
    if predicted == CLASS_DOWN and actual == CLASS_UP:
        return "false_short"
    if predicted == CLASS_NONE and actual == CLASS_UP:
        return "missed_upside"
    if predicted == CLASS_NONE and actual == CLASS_DOWN:
        return "missed_downside"
    if predicted in (CLASS_UP, CLASS_DOWN) and actual == CLASS_NONE:
        return "false_opportunity"
    return "other_error"


def _entropy(probabilities: np.ndarray) -> float:
    values = np.clip(np.asarray(probabilities, dtype=np.float64), 1e-12, 1.0)
    return float(-(values * np.log(values)).sum())


def _price_bucket(anchor: float) -> str:
    if not math.isfinite(anchor) or anchor <= 0:
        return "missing"
    if anchor < 1:
        return "penny_lt_1"
    if anchor < 20:
        return "small_1_20"
    if anchor < 100:
        return "mid_20_100"
    return "large_ge_100"


def _feature_metadata(
    arrays: Mapping[str, np.ndarray],
    row_index: int,
) -> dict[str, Any]:
    stock = np.asarray(arrays["stock_state"][row_index], dtype=np.float32)
    current = np.asarray(arrays["current_market_features"][row_index], dtype=np.float32)
    stock_values = dict(zip(STOCK_STATE_NAMES, stock.tolist()))
    market_values = dict(zip(CURRENT_MARKET_FEATURE_NAMES, current.tolist()))
    decoded_market_returns = {
        name: math.copysign(math.expm1(abs(float(value))), float(value))
        for name, value in market_values.items()
        if name.endswith(
            (
                "_terminal_return",
                "_high_return",
                "_low_return",
                "_vwap_distance",
            )
        )
    }
    sec_present = sum(
        bool(stock_values[f"sec_{concept}_present"])
        for concept in {
            name[len("sec_") : -len("_present")]
            for name in STOCK_STATE_NAMES
            if name.startswith("sec_") and name.endswith("_present")
        }
    )
    return {
        "prepared_row_index": int(row_index),
        "publication_session": _decode(arrays["publication_session"][row_index]),
        "prior_context_count": int(np.count_nonzero(arrays["context_mask"][row_index])),
        "market_context_count": int(
            np.count_nonzero(arrays["market_context_mask"][row_index])
        ),
        "market_leader_count": int(
            np.count_nonzero(arrays["market_leader_mask"][row_index])
        ),
        "sec_concepts_present": int(sec_present),
        "anchor_state_present": bool(stock_values["anchor_present"]),
        "prior_daily_bar_present": bool(stock_values["prior_bar_present"]),
        "short_volume_present": bool(stock_values["short_present"]),
        "market_return_percentile": float(market_values["return_percentile"]),
        "market_volume_percentile": float(market_values["volume_percentile"]),
        "market_dollar_volume_percentile": float(
            market_values["dollar_volume_percentile"]
        ),
        "market_relative_volume_percentile": float(
            market_values["relative_volume_percentile"]
        ),
        "is_top20_gainer": bool(market_values["is_top20_gainer"]),
        "is_top20_loser": bool(market_values["is_top20_loser"]),
        "is_top20_volume": bool(market_values["is_top20_volume"]),
        "is_top20_relative_volume": bool(
            market_values["is_top20_relative_volume"]
        ),
        **decoded_market_returns,
    }


def _prepared_index(
    arrays: Mapping[str, np.ndarray],
    start: str,
    end_exclusive: str,
) -> tuple[dict[tuple[str, str], int], dict[str, list[int]], Counter[str]]:
    lower, upper = _date_range_indices(arrays["published_at_us"], start, end_exclusive)
    identity_to_row: dict[tuple[str, str], int] = {}
    times_by_ticker: dict[str, list[int]] = defaultdict(list)
    frequencies: Counter[str] = Counter()
    for index in range(lower, upper):
        key = _identity(
            _decode(arrays["canonical_news_id"][index]),
            _decode(arrays["ticker"][index]),
        )
        if key in identity_to_row:
            raise RuntimeError(f"Duplicate prepared identity in study range: {key}.")
        identity_to_row[key] = index
        published_us = int(arrays["published_at_us"][index])
        times_by_ticker[key[1]].append(published_us)
        frequencies[key[1]] += 1
    return identity_to_row, dict(times_by_ticker), frequencies


def _concurrent_count(times: Sequence[int], published_us: int, minutes: int) -> int:
    values = np.asarray(times, dtype=np.int64)
    radius = int(minutes) * 60_000_000
    left = int(np.searchsorted(values, published_us - radius, side="left"))
    right = int(np.searchsorted(values, published_us + radius, side="right"))
    return max(0, right - left - 1)


def _frequency_bucket(value: int) -> str:
    if value <= 1:
        return "1"
    if value <= 5:
        return "2_5"
    if value <= 20:
        return "6_20"
    if value <= 100:
        return "21_100"
    return "gt_100"


def build_article_audit(
    prediction_groups: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    arrays: Mapping[str, np.ndarray],
    *,
    start: str,
    end_exclusive: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    identity_to_row, times_by_ticker, frequencies = _prepared_index(
        arrays, start, end_exclusive
    )
    articles: list[dict[str, Any]] = []
    horizon_rows: list[dict[str, Any]] = []
    for key, rows in prediction_groups.items():
        row_index = identity_to_row.get(key)
        if row_index is None:
            raise RuntimeError(f"Prediction identity is missing from V16 prepared arrays: {key}.")
        votes = Counter(int(row["predicted_class"]) for row in rows)
        predicted, winning_votes, vote_margin, vote_tied = _vote_decision(votes)
        upside_strength, downside_strength = _strengths(rows)
        actual = _actual_decision(upside_strength, downside_strength)
        probabilities = np.asarray(
            [
                [
                    float(row["probabilities"][name])
                    for name in OPPORTUNITY_CLASS_NAMES
                ]
                for row in rows
            ],
            dtype=np.float64,
        )
        mean_probabilities = probabilities.mean(axis=0)
        published_us = int(arrays["published_at_us"][row_index])
        anchors = np.asarray(
            [float(row["anchor_price"]) for row in rows], dtype=np.float64
        )
        anchor = float(np.median(anchors))
        actual_horizon_classes = {int(row["actual_class"]) for row in rows}
        predicted_horizon_classes = {int(row["predicted_class"]) for row in rows}
        metadata = _feature_metadata(arrays, row_index)
        record = {
            "canonical_news_id": key[0],
            "ticker": key[1],
            "published_at_utc": str(rows[0]["published_at_utc"]),
            "published_at_us": published_us,
            "available_horizons": len(rows),
            "available_horizon_codes": [str(row["horizon"]) for row in rows],
            "votes_no_opportunity": int(votes[CLASS_NONE]),
            "votes_upside": int(votes[CLASS_UP]),
            "votes_downside": int(votes[CLASS_DOWN]),
            "winning_votes": winning_votes,
            "vote_margin": vote_margin,
            "vote_share": winning_votes / len(rows),
            "vote_tied": vote_tied,
            "mean_probability_no_opportunity": float(mean_probabilities[CLASS_NONE]),
            "mean_probability_upside": float(mean_probabilities[CLASS_UP]),
            "mean_probability_downside": float(mean_probabilities[CLASS_DOWN]),
            "mean_head_entropy": float(np.mean([_entropy(value) for value in probabilities])),
            "predicted_class": predicted,
            "predicted_decision": OPPORTUNITY_CLASS_NAMES[predicted],
            "actual_class": actual,
            "actual_decision": OPPORTUNITY_CLASS_NAMES[actual],
            "correct": predicted == actual,
            "error_type": _error_type(predicted, actual),
            "upside_strength": float(upside_strength),
            "downside_strength": float(downside_strength),
            "two_sided_actual": upside_strength > 1.0 and downside_strength > 1.0,
            "horizon_prediction_conflict": (
                CLASS_UP in predicted_horizon_classes
                and CLASS_DOWN in predicted_horizon_classes
            ),
            "horizon_actual_conflict": (
                CLASS_UP in actual_horizon_classes and CLASS_DOWN in actual_horizon_classes
            ),
            "timing_mismatch": predicted != actual and predicted in actual_horizon_classes,
            "anchor_price": anchor,
            "price_bucket": _price_bucket(anchor),
            "ticker_2026_frequency": int(frequencies[key[1]]),
            "ticker_frequency_bucket": _frequency_bucket(frequencies[key[1]]),
            "nearby_same_ticker_news_5m": _concurrent_count(
                times_by_ticker[key[1]], published_us, 5
            ),
            "nearby_same_ticker_news_30m": _concurrent_count(
                times_by_ticker[key[1]], published_us, 30
            ),
            **metadata,
        }
        articles.append(record)
        for row in rows:
            horizon_rows.append(
                {
                    "canonical_news_id": key[0],
                    "ticker": key[1],
                    "horizon": str(row["horizon"]),
                    "predicted_class": int(row["predicted_class"]),
                    "actual_class": int(row["actual_class"]),
                    "correct": int(row["predicted_class"]) == int(row["actual_class"]),
                    "confidence": float(row["confidence"]),
                    "position": int(row["position"]),
                    "publication_session": metadata["publication_session"],
                    "anchor_price": anchor,
                    "price_bucket": record["price_bucket"],
                }
            )
    articles.sort(key=lambda value: (value["published_at_us"], value["ticker"], value["canonical_news_id"]))
    return articles, horizon_rows


def news_enrichment_sql(config: LoaderConfig, start: str, end_exclusive: str) -> str:
    table = f"{qi(config.news_database)}.{qi('benzinga_news_normalized_v1')}"
    return f"""
SELECT
 canonical_news_id,
 arrayElement(tickers, 1) AS ticker,
 title,
 teaser,
 author,
 url_domain,
 channels,
 provider_tags,
 links,
 content_quality_flags,
 lengthUTF8(normalized_full_text) AS text_length
FROM {table} FINAL
WHERE published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end_exclusive)}, 9, 'UTC')
 AND length(tickers) = 1
ORDER BY published_at_utc, canonical_news_id
SETTINGS max_threads={config.max_threads_per_query},
 max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


def enrich_news(
    articles: list[dict[str, Any]],
    config: LoaderConfig,
    *,
    start: str,
    end_exclusive: str,
) -> dict[str, Any]:
    from src.backend.news_classification import classify_news

    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    source: dict[tuple[str, str], dict[str, Any]] = {}
    text = client.execute(news_enrichment_sql(config, start, end_exclusive))
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        source[_identity(row["canonical_news_id"], row["ticker"])] = row
    missing = 0
    for article in articles:
        row = source.get(_identity(article["canonical_news_id"], article["ticker"]))
        if row is None:
            missing += 1
            article.update(
                {
                    "news_kind": "missing",
                    "news_origin": "missing",
                    "news_scope": "missing",
                    "news_topics": [],
                    "news_classification_version": "missing",
                    "news_classification_confidence": 0.0,
                    "news_classification_evidence": [],
                    "channels": [],
                    "provider_tags": [],
                    "title": "",
                    "teaser": "",
                    "author": "",
                    "url_domain": "",
                    "text_length": 0,
                    "content_quality_flags": ["missing_news_enrichment"],
                }
            )
            continue
        classification = classify_news(
            {
                **row,
                "text": row.get("teaser") or "",
            },
            ticker_count=1,
        )
        article.update(
            {
                "news_kind": classification.kind,
                "news_origin": classification.origin,
                "news_scope": classification.scope,
                "news_topics": list(classification.topics),
                "news_classification_version": classification.version,
                "news_classification_confidence": classification.confidence,
                "news_classification_evidence": list(classification.evidence),
                "channels": list(row.get("channels") or []),
                "provider_tags": list(row.get("provider_tags") or []),
                "title": str(row.get("title") or ""),
                "teaser": str(row.get("teaser") or ""),
                "author": str(row.get("author") or ""),
                "url_domain": str(row.get("url_domain") or ""),
                "text_length": int(row.get("text_length") or 0),
                "content_quality_flags": list(row.get("content_quality_flags") or []),
            }
        )
    return {"source_rows": len(source), "matched": len(articles) - missing, "missing": missing}


def enrich_neighbor_metadata(
    neighbor_rows: list[dict[str, Any]],
    config: LoaderConfig,
    *,
    start: str = "2019-01-01",
    end_exclusive: str = "2026-01-01",
    batch_size: int = 1_000,
) -> dict[str, int]:
    from src.backend.news_classification import classify_news

    identities = {
        _identity(item["canonical_news_id"], item["ticker"])
        for row in neighbor_rows
        for item in row["neighbors"]
    }
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    table = f"{qi(config.news_database)}.{qi('benzinga_news_normalized_v1')}"
    metadata: dict[tuple[str, str], dict[str, Any]] = {}
    ordered_ids = sorted({canonical_news_id for canonical_news_id, _ in identities})
    for offset in range(0, len(ordered_ids), batch_size):
        values = ordered_ids[offset : offset + batch_size]
        sql = f"""
SELECT canonical_news_id, arrayElement(tickers, 1) AS ticker, title, teaser,
 author, url_domain, channels, provider_tags, links, content_quality_flags,
 lengthUTF8(normalized_full_text) AS text_length
FROM {table} FINAL
WHERE published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end_exclusive)}, 9, 'UTC')
 AND length(tickers) = 1
 AND canonical_news_id IN ({','.join(q(value) for value in values)})
FORMAT JSONEachRow
"""
        for line in client.execute(sql).splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            metadata[_identity(item["canonical_news_id"], item["ticker"])] = item
    for row in neighbor_rows:
        for item in row["neighbors"]:
            source = metadata.get(_identity(item["canonical_news_id"], item["ticker"]))
            if source is None:
                item["metadata_available"] = False
                continue
            classification = classify_news(
                {**source, "text": source.get("teaser") or ""},
                ticker_count=1,
            )
            item.update(
                {
                    "metadata_available": True,
                    "title": str(source.get("title") or ""),
                    "teaser": str(source.get("teaser") or ""),
                    "author": str(source.get("author") or ""),
                    "url_domain": str(source.get("url_domain") or ""),
                    "channels": list(source.get("channels") or []),
                    "provider_tags": list(source.get("provider_tags") or []),
                    "news_kind": classification.kind,
                    "news_topics": list(classification.topics),
                    "text_length": int(source.get("text_length") or 0),
                }
            )
    matched_identities = sum(identity in metadata for identity in identities)
    return {
        "requested": len(identities),
        "matched": matched_identities,
        "missing": len(identities) - matched_identities,
    }


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    radius = (
        z
        * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))
        / denominator
    )
    return max(0.0, center - radius), min(1.0, center + radius)


def _metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    support = len(records)
    exact = sum(bool(row["correct"]) for row in records)
    active = [row for row in records if int(row["predicted_class"]) in (CLASS_UP, CLASS_DOWN)]
    directional = sum(
        int(row["predicted_class"]) == int(row["actual_class"]) for row in active
    )
    long = [row for row in active if int(row["predicted_class"]) == CLASS_UP]
    short = [row for row in active if int(row["predicted_class"]) == CLASS_DOWN]
    low, high = _wilson(exact, support)
    return {
        "support": support,
        "accuracy": exact / max(support, 1),
        "accuracy_ci95_low": low,
        "accuracy_ci95_high": high,
        "coverage": len(active) / max(support, 1),
        "active": len(active),
        "direction_accuracy": directional / max(len(active), 1),
        "long": len(long),
        "long_precision": sum(int(row["actual_class"]) == CLASS_UP for row in long)
        / max(len(long), 1),
        "short": len(short),
        "short_precision": sum(int(row["actual_class"]) == CLASS_DOWN for row in short)
        / max(len(short), 1),
    }


def taxonomy_rows(articles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in articles:
        groups[str(row["error_type"])].append(row)
    result = []
    for name, rows in sorted(groups.items()):
        item = {"error_type": name, **_metrics(rows)}
        item["share"] = len(rows) / max(len(articles), 1)
        item["two_sided_share"] = sum(bool(row["two_sided_actual"]) for row in rows) / len(rows)
        item["timing_mismatch_share"] = sum(bool(row["timing_mismatch"]) for row in rows) / len(rows)
        item["horizon_conflict_share"] = sum(
            bool(row["horizon_prediction_conflict"]) for row in rows
        ) / len(rows)
        result.append(item)
    return result


def _bin_confidence(value: float) -> str:
    boundaries = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    lower = 0.0
    for upper in boundaries:
        if value < upper:
            return f"{lower:.1f}_{upper:.1f}"
        lower = upper
    return "0.9_1.0"


def calibration_rows(
    articles: Sequence[Mapping[str, Any]],
    horizons: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    article_groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in articles:
        article_groups[
            (str(row["predicted_decision"]), _bin_confidence(float(row["vote_share"])))
        ].append(row)
    for (predicted_class, bucket), rows in sorted(article_groups.items()):
        result.append(
            {
                "level": "consolidated_vote_share",
                "horizon": "all",
                "predicted_class": predicted_class,
                "confidence_bin": bucket,
                **_metrics(rows),
                "mean_confidence": float(np.mean([row["vote_share"] for row in rows])),
            }
        )
    horizon_groups: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in horizons:
        horizon_groups[
            (
                str(row["horizon"]),
                _bin_confidence(float(row["confidence"])),
                int(row["predicted_class"]),
            )
        ].append(row)
    for (horizon, bucket, predicted_class), rows in sorted(horizon_groups.items()):
        support = len(rows)
        correct = sum(bool(row["correct"]) for row in rows)
        low, high = _wilson(correct, support)
        result.append(
            {
                "level": "horizon_head",
                "horizon": horizon,
                "predicted_class": OPPORTUNITY_CLASS_NAMES[predicted_class],
                "confidence_bin": bucket,
                "support": support,
                "accuracy": correct / support,
                "accuracy_ci95_low": low,
                "accuracy_ci95_high": high,
                "mean_confidence": float(np.mean([row["confidence"] for row in rows])),
            }
        )
    return result


def expected_calibration_error(
    rows: Sequence[Mapping[str, Any]],
    *,
    level: str,
) -> float:
    selected = [row for row in rows if row["level"] == level]
    total = sum(int(row["support"]) for row in selected)
    return sum(
        int(row["support"])
        * abs(float(row["mean_confidence"]) - float(row["accuracy"]))
        for row in selected
    ) / max(total, 1)


def _context_bucket(value: int) -> str:
    if value == 0:
        return "0"
    if value <= 2:
        return "1_2"
    if value <= 10:
        return "3_10"
    if value <= 50:
        return "11_50"
    return "gt_50"


def _nearby_bucket(value: int) -> str:
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 3:
        return "2_3"
    return "gt_3"


def _text_length_bucket(value: int) -> str:
    if value <= 0:
        return "missing"
    if value <= 500:
        return "1_500"
    if value <= 2_000:
        return "501_2000"
    if value <= 8_000:
        return "2001_8000"
    if value <= 12_000:
        return "8001_12000"
    return "gt_12000"


def slice_rows(
    articles: Sequence[Mapping[str, Any]],
    minimum_support: int,
) -> list[dict[str, Any]]:
    scalar_dimensions: dict[str, Any] = {
        "publication_session": lambda row: row.get("publication_session", "missing"),
        "price_bucket": lambda row: row.get("price_bucket", "missing"),
        "ticker_frequency": lambda row: row.get("ticker_frequency_bucket", "missing"),
        "news_kind": lambda row: row.get("news_kind", "not_enriched"),
        "news_origin": lambda row: row.get("news_origin", "not_enriched"),
        "source_domain": lambda row: row.get("url_domain") or "missing",
        "author": lambda row: row.get("author") or "missing",
        "text_length": lambda row: _text_length_bucket(int(row.get("text_length") or 0)),
        "prior_context": lambda row: _context_bucket(int(row["prior_context_count"])),
        "market_context": lambda row: _context_bucket(int(row["market_context_count"])),
        "nearby_news_5m": lambda row: _nearby_bucket(int(row["nearby_same_ticker_news_5m"])),
        "nearby_news_30m": lambda row: _nearby_bucket(int(row["nearby_same_ticker_news_30m"])),
        "vote_share": lambda row: _bin_confidence(float(row["vote_share"])),
        "vote_margin": lambda row: str(int(row["vote_margin"])),
        "two_sided_actual": lambda row: str(bool(row["two_sided_actual"])).lower(),
        "horizon_prediction_conflict": lambda row: str(
            bool(row["horizon_prediction_conflict"])
        ).lower(),
        "top20_gainer": lambda row: str(bool(row["is_top20_gainer"])).lower(),
        "top20_loser": lambda row: str(bool(row["is_top20_loser"])).lower(),
        "top20_volume": lambda row: str(bool(row["is_top20_volume"])).lower(),
        "top20_relative_volume": lambda row: str(
            bool(row["is_top20_relative_volume"])
        ).lower(),
    }
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in articles:
        for dimension, function in scalar_dimensions.items():
            groups[(dimension, str(function(row)))].append(row)
        for topic in row.get("news_topics") or ("none",):
            groups[("news_topic", str(topic))].append(row)
        for channel in row.get("channels") or ("none",):
            groups[("channel", str(channel))].append(row)
        for tag in row.get("provider_tags") or ("none",):
            groups[("provider_tag", str(tag))].append(row)
    result = []
    for (dimension, value), rows in groups.items():
        if len(rows) < minimum_support:
            continue
        result.append({"dimension": dimension, "value": value, **_metrics(rows)})
    result.sort(key=lambda row: (row["dimension"], -int(row["support"]), row["value"]))
    return result


def _review_stratum(row: Mapping[str, Any]) -> str | None:
    error = str(row["error_type"])
    confident = float(row["vote_share"]) >= 0.6 or int(row["vote_margin"]) >= 3
    if error == "false_long" and confident:
        return "confident_false_long"
    if error == "false_short" and confident:
        return "confident_false_short"
    if error == "missed_upside":
        return "missed_upside"
    if error == "missed_downside":
        return "missed_downside"
    if bool(row["correct"]) and confident:
        return "correct_high_confidence"
    if bool(row["two_sided_actual"]) or bool(row["horizon_prediction_conflict"]):
        return "ambiguous_or_horizon_conflict"
    return None


def stratified_review_sample(
    articles: Sequence[Mapping[str, Any]],
    *,
    per_stratum: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in articles:
        stratum = _review_stratum(row)
        if stratum is not None:
            groups[stratum].append(row)
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    counts: dict[str, Any] = {}
    for stratum in (
        "confident_false_long",
        "confident_false_short",
        "missed_upside",
        "missed_downside",
        "correct_high_confidence",
        "ambiguous_or_horizon_conflict",
    ):
        candidates = list(groups.get(stratum, ()))
        rng.shuffle(candidates)
        chosen = candidates[:per_stratum]
        counts[stratum] = {"available": len(candidates), "selected": len(chosen)}
        for row in chosen:
            selected.append(
                {
                    "review_stratum": stratum,
                    **dict(row),
                    "manual_primary_reason": "",
                    "manual_secondary_reason": "",
                    "manual_label_quality": "",
                    "manual_notes": "",
                }
            )
    selected.sort(key=lambda row: (row["review_stratum"], row["published_at_us"]))
    return selected, counts


def _neighbor_device(requested: str) -> torch.device:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA neighbor search was requested but CUDA is unavailable.")
        return torch.device("cuda")
    if requested == "cpu":
        return torch.device("cpu")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _targets_for_row(
    arrays: Mapping[str, np.ndarray],
    row_index: int,
) -> dict[str, str]:
    targets = np.asarray(arrays["return_targets"][row_index], dtype=np.float64)
    mask = np.asarray(arrays["label_mask"][row_index], dtype=bool)
    values: dict[str, str] = {}
    for horizon_index, horizon in enumerate(HORIZONS):
        if not mask[horizon_index]:
            continue
        class_id = OPPORTUNITY_SPECS[horizon].classify(
            float(targets[horizon_index, 1]),
            float(targets[horizon_index, 2]),
        )
        if class_id >= 0:
            values[horizon] = OPPORTUNITY_CLASS_NAMES[class_id]
    return values


def embedding_neighbor_rows(
    review_rows: Sequence[Mapping[str, Any]],
    arrays: Mapping[str, np.ndarray],
    *,
    train_end_exclusive: str,
    top_k: int,
    candidate_count: int,
    projection_dim: int,
    batch_size: int,
    device_name: str,
    seed: int,
) -> Iterator[dict[str, Any]]:
    query_indices = np.asarray(
        list(dict.fromkeys(int(row["prepared_row_index"]) for row in review_rows)),
        dtype=np.int64,
    )
    if query_indices.size == 0:
        return
    _, train_upper = _date_range_indices(
        arrays["published_at_us"], "1900-01-01", train_end_exclusive
    )
    train_indices = np.arange(0, train_upper, dtype=np.int64)
    embeddings = arrays["openai_embedding"]
    device = _neighbor_device(device_name)
    generator = torch.Generator(device=device)
    generator.manual_seed(seed)
    projection = torch.randn(
        embeddings.shape[1],
        projection_dim,
        generator=generator,
        device=device,
        dtype=torch.float32,
    ) / math.sqrt(projection_dim)
    query = torch.from_numpy(
        np.asarray(embeddings[query_indices], dtype=np.float32)
    ).to(device)
    query = torch.nn.functional.normalize(query, dim=1)
    query_projection = torch.nn.functional.normalize(query @ projection, dim=1)
    best_scores = torch.full(
        (query_indices.size, candidate_count),
        -float("inf"),
        device=device,
    )
    best_indices = torch.full(
        (query_indices.size, candidate_count),
        -1,
        dtype=torch.long,
        device=device,
    )
    for offset in range(0, train_indices.size, batch_size):
        block_indices = train_indices[offset : offset + batch_size]
        block = torch.from_numpy(
            np.asarray(embeddings[block_indices], dtype=np.float32)
        ).to(device)
        block_projection = torch.nn.functional.normalize(block @ projection, dim=1)
        scores = query_projection @ block_projection.T
        block_k = min(candidate_count, scores.shape[1])
        block_scores, block_local = torch.topk(scores, k=block_k, dim=1)
        block_global = torch.from_numpy(block_indices).to(device)[block_local]
        merged_scores = torch.cat((best_scores, block_scores), dim=1)
        merged_indices = torch.cat((best_indices, block_global), dim=1)
        best_scores, selected = torch.topk(merged_scores, k=candidate_count, dim=1)
        best_indices = torch.gather(merged_indices, 1, selected)
        if offset == 0 or (offset // batch_size + 1) % 10 == 0:
            print(
                f"NEIGHBORS projected={min(offset + batch_size, train_indices.size):,}/"
                f"{train_indices.size:,} queries={query_indices.size:,} device={device}",
                flush=True,
            )
    candidates = best_indices.cpu().numpy()
    query_cpu = query.cpu().numpy()
    for query_position, query_index in enumerate(query_indices.tolist()):
        candidate_indices = candidates[query_position]
        candidate_vectors = np.asarray(embeddings[candidate_indices], dtype=np.float32)
        norms = np.linalg.norm(candidate_vectors, axis=1, keepdims=True)
        candidate_vectors = candidate_vectors / np.maximum(norms, 1e-12)
        exact = candidate_vectors @ query_cpu[query_position]
        order = np.argsort(-exact)[:top_k]
        yield {
            "canonical_news_id": _decode(arrays["canonical_news_id"][query_index]),
            "ticker": _decode(arrays["ticker"][query_index]),
            "published_at_utc": _decode(arrays["published_at_utc"][query_index]),
            "actual_opportunities": _targets_for_row(arrays, query_index),
            "neighbors": [
                {
                    "canonical_news_id": _decode(
                        arrays["canonical_news_id"][candidate_indices[position]]
                    ),
                    "ticker": _decode(arrays["ticker"][candidate_indices[position]]),
                    "published_at_utc": _decode(
                        arrays["published_at_utc"][candidate_indices[position]]
                    ),
                    "cosine_similarity": float(exact[position]),
                    "actual_opportunities": _targets_for_row(
                        arrays, int(candidate_indices[position])
                    ),
                }
                for position in order
            ],
        }


def neighbor_diagnostics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    similarities: list[float] = []
    agreements = comparisons = 0
    mixed_queries = 0
    for row in rows:
        query_targets = dict(row.get("actual_opportunities") or {})
        mixed = False
        for horizon, query_class in query_targets.items():
            values = [
                neighbor.get("actual_opportunities", {}).get(horizon)
                for neighbor in row["neighbors"]
            ]
            values = [value for value in values if value is not None]
            if len(set(values)) > 1:
                mixed = True
            agreements += sum(value == query_class for value in values)
            comparisons += len(values)
        mixed_queries += int(mixed)
        similarities.extend(
            float(neighbor["cosine_similarity"]) for neighbor in row["neighbors"]
        )
    return {
        "queries": len(rows),
        "neighbor_pairs": len(similarities),
        "mean_cosine_similarity": float(np.mean(similarities)) if similarities else 0.0,
        "median_cosine_similarity": float(np.median(similarities)) if similarities else 0.0,
        "horizon_label_agreement": agreements / max(comparisons, 1),
        "horizon_label_comparisons": comparisons,
        "queries_with_mixed_neighbor_outcomes": mixed_queries,
        "mixed_neighbor_outcome_share": mixed_queries / max(len(rows), 1),
    }


def _session_date(published_at_utc: Any) -> dt.date:
    return parse_published_at_utc(published_at_utc).astimezone(EXCHANGE_TZ).date()


def _path_for_session(
    config: LoaderConfig,
    session_date: dt.date,
    tickers: Sequence[str],
) -> tuple[dt.date, DayMarketData]:
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    rows = parse_minute_bar_rows(
        client.execute(
            daily_minute_bars_sql(config, session_date, tickers=tickers)
        )
    )
    return session_date, DayMarketData(
        session_date,
        rows,
        rows_chronological=True,
    )


def _path_features(
    bars: Sequence[Mapping[str, Any]],
    proxy_bars: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    published_us: int,
    anchor_price: float,
) -> dict[str, Any]:
    pre = [
        row for row in bars
        if int(row["minute_end_us"]) <= published_us
        and int(row["minute_end_us"]) > published_us - 30 * 60_000_000
    ]
    post = [row for row in bars if int(row["minute_end_us"]) > published_us]
    post_30m = [
        row for row in post
        if int(row["minute_end_us"]) <= published_us + 30 * 60_000_000
    ]
    pre_return = (
        float(pre[-1]["close"]) / float(pre[0]["open"]) - 1.0
        if pre and float(pre[0]["open"]) > 0
        else 0.0
    )
    if post and anchor_price > 0:
        post_high = max(float(row["high"]) for row in post) / anchor_price - 1.0
        post_low = min(float(row["low"]) for row in post) / anchor_price - 1.0
        terminal = float(post[-1]["close"]) / anchor_price - 1.0
    else:
        post_high = post_low = terminal = 0.0
    proxy_returns: dict[str, float] = {}
    for proxy, values in proxy_bars.items():
        selected = [row for row in values if int(row["minute_end_us"]) > published_us]
        proxy_returns[proxy] = (
            float(selected[-1]["close"]) / float(selected[0]["open"]) - 1.0
            if selected and float(selected[0]["open"]) > 0
            else 0.0
        )
    return {
        "pre_30m_return": pre_return,
        "movement_started_before_publication": abs(pre_return) >= 0.005,
        "post_session_high_return": post_high,
        "post_session_low_return": post_low,
        "post_session_terminal_return": terminal,
        "post_peak_then_fade": post_high >= 0.005 and terminal <= post_high * 0.5,
        "post_dip_then_recover": post_low <= -0.005 and terminal >= post_low * 0.5,
        "post_30m_trade_count": int(
            sum(int(row["trade_count"]) for row in post_30m)
        ),
        "post_30m_volume": float(sum(float(row["volume"]) for row in post_30m)),
        "sparse_post_30m": bool(post_30m) and sum(
            int(row["trade_count"]) for row in post_30m
        ) < 20,
        "large_jump_bar_present": any(
            bool(row["large_jump_from_previous_close"]) for row in bars
        ),
        "spy_post_session_return": proxy_returns.get("SPY", 0.0),
        "qqq_post_session_return": proxy_returns.get("QQQ", 0.0),
    }


def price_path_rows(
    review_rows: Sequence[Mapping[str, Any]],
    config: LoaderConfig,
    *,
    workers: int,
    audit: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    audit = audit if audit is not None else {}
    audit.update(
        {
            "records": 0,
            "with_ticker_bars": 0,
            "empty_ticker_bars": 0,
            "diagnostic_flag_counts": {},
            "diagnostic_flags_by_error_type": {},
        }
    )
    requests: dict[dt.date, set[str]] = defaultdict(set)
    cases: dict[tuple[dt.date, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in review_rows:
        session_date = _session_date(row["published_at_utc"])
        ticker = str(row["ticker"]).upper()
        requests[session_date].add(ticker)
        requests[session_date].update(("SPY", "QQQ"))
        cases[(session_date, ticker)].append(row)
    days: dict[dt.date, DayMarketData] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v16-error-path") as pool:
        future_to_date = {
            pool.submit(_path_for_session, config, session_date, sorted(tickers)): session_date
            for session_date, tickers in requests.items()
        }
        for completed, future in enumerate(as_completed(future_to_date), start=1):
            session_date, day = future.result()
            days[session_date] = day
            print(
                f"PRICE PATHS sessions={completed:,}/{len(future_to_date):,} "
                f"date={session_date} tickers={len(day.tickers):,}",
                flush=True,
            )
    for (session_date, ticker), matching_cases in sorted(cases.items()):
        bars = days[session_date].minute_rows(ticker)
        market_proxy_bars = {
            proxy: days[session_date].minute_rows(proxy)
            for proxy in ("SPY", "QQQ")
        }
        for case in matching_cases:
            published_us = int(case["published_at_us"])
            previous_close: float | None = None
            rendered = []
            for bar in bars:
                minute_end = int(bar["minute_end_us"])
                local = dt.datetime.fromtimestamp(minute_end / 1_000_000, tz=UTC).astimezone(
                    EXCHANGE_TZ
                )
                jump = (
                    max(
                        abs(float(bar["high"]) / previous_close - 1.0),
                        abs(float(bar["low"]) / previous_close - 1.0),
                    )
                    if previous_close and previous_close > 0
                    else 0.0
                )
                rendered.append(
                    {
                        **bar,
                        "minute_end_utc": dt.datetime.fromtimestamp(
                            minute_end / 1_000_000, tz=UTC
                        ).isoformat(),
                        "minute_end_et": local.isoformat(),
                        "relative_to_news_seconds": (minute_end - published_us) / 1_000_000,
                        "session_segment": (
                            "premarket"
                            if local.hour * 60 + local.minute <= 570
                            else "regular"
                            if local.hour * 60 + local.minute <= 960
                            else "afterhours"
                        ),
                        "large_jump_from_previous_close": jump >= 0.10,
                    }
                )
                previous_close = float(bar["close"])
            audit["records"] += 1
            if rendered:
                audit["with_ticker_bars"] += 1
            else:
                audit["empty_ticker_bars"] += 1
            features = _path_features(
                rendered,
                market_proxy_bars,
                published_us=published_us,
                anchor_price=float(case["anchor_price"]),
            )
            boolean_flags = (
                "movement_started_before_publication",
                "post_peak_then_fade",
                "post_dip_then_recover",
                "sparse_post_30m",
                "large_jump_bar_present",
            )
            error_counts = audit["diagnostic_flags_by_error_type"].setdefault(
                str(case["error_type"]),
                {},
            )
            for name in boolean_flags:
                if bool(features[name]):
                    audit["diagnostic_flag_counts"][name] = (
                        int(audit["diagnostic_flag_counts"].get(name, 0)) + 1
                    )
                    error_counts[name] = int(error_counts.get(name, 0)) + 1
            yield {
                "canonical_news_id": case["canonical_news_id"],
                "ticker": ticker,
                "published_at_utc": case["published_at_utc"],
                "session_date": session_date.isoformat(),
                "error_type": case["error_type"],
                "path_available": bool(rendered),
                "path_features": features,
                "bars": rendered,
                "market_proxy_bars": market_proxy_bars,
            }


def _summary(
    articles: Sequence[Mapping[str, Any]],
    taxonomy: Sequence[Mapping[str, Any]],
    calibration: Sequence[Mapping[str, Any]],
    slices: Sequence[Mapping[str, Any]],
    *,
    prepared_dataset_version: str,
) -> dict[str, Any]:
    confusion = np.zeros((3, 3), dtype=np.int64)
    for row in articles:
        confusion[int(row["actual_class"]), int(row["predicted_class"])] += 1
    actual_counts = confusion.sum(axis=1)
    return {
        "articles": len(articles),
        "overall": _metrics(articles),
        "majority_class_accuracy": float(actual_counts.max()) / max(len(articles), 1),
        "class_order": list(OPPORTUNITY_CLASS_NAMES),
        "confusion_actual_rows_predicted_columns": confusion.tolist(),
        "actual_class_counts": {
            name: int(actual_counts[index])
            for index, name in enumerate(OPPORTUNITY_CLASS_NAMES)
        },
        "taxonomy": list(taxonomy),
        "calibration_rows": len(calibration),
        "consolidated_expected_calibration_error": expected_calibration_error(
            calibration, level="consolidated_vote_share"
        ),
        "horizon_head_expected_calibration_error": expected_calibration_error(
            calibration, level="horizon_head"
        ),
        "slice_rows": len(slices),
        "actual_contract": (
            "Across available authoritative horizons, normalize positive high and "
            "negative low excursion by that horizon's minimum meaningful span. "
            "No opportunity if neither exceeds 1; otherwise the larger normalized "
            "excursion determines direction."
        ),
        "decision_contract": (
            "Hard plurality over available horizon argmax classes; an exact vote "
            "tie abstains. Vote confidence is winning votes / available horizons."
        ),
        "interpretation_warning": (
            "The official V16 export includes only horizons with authoritative "
            "realized labels and anchors. This study diagnoses held-out errors; "
            "it is not a production all-ten-head availability simulation."
        ),
        "known_input_integrity": {
            "minute_bar_field_order_affected": (
                prepared_dataset_version == LEGACY_MISORDERED_DATASET_VERSION
            ),
            "detail": (
                "The legacy V16 prepared dataset was built while the typed SQL "
                "transfer emitted open,close,high,low but DayMarketData consumed "
                "open,high,low,close. Its V16 market-context channels are therefore "
                "not trustworthy. Labels and V8 article/stock inputs are unaffected. "
                "The corrected v2 prepared contract requires a fresh build."
                if prepared_dataset_version == LEGACY_MISORDERED_DATASET_VERSION
                else "The prepared dataset declares the corrected OHLC transfer contract."
            ),
        },
    }


def write_markdown_report(
    path: Path,
    summary: Mapping[str, Any],
    slices: Sequence[Mapping[str, Any]],
) -> None:
    overall = summary["overall"]
    integrity = summary["known_input_integrity"]
    ranked = sorted(
        slices,
        key=lambda row: (
            abs(float(row["accuracy"]) - float(overall["accuracy"])),
            int(row["support"]),
        ),
        reverse=True,
    )[:20]
    lines = [
        "# V16 2026 Error Study",
        "",
        "## Verdict",
        "",
        (
            f"Hard-vote accuracy is {float(overall['accuracy']):.2%} "
            f"(95% CI {float(overall['accuracy_ci95_low']):.2%}-"
            f"{float(overall['accuracy_ci95_high']):.2%}); active direction "
            f"accuracy is {float(overall['direction_accuracy']):.2%} at "
            f"{float(overall['coverage']):.2%} coverage."
        ),
        "",
        f"Input-integrity warning: {integrity['detail']}",
        "",
        "## Error taxonomy",
        "",
        "| Error | Cases | Share | Two-sided | Timing mismatch | Horizon conflict |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["taxonomy"]:
        lines.append(
            f"| {row['error_type']} | {int(row['support']):,} | "
            f"{float(row['share']):.2%} | {float(row['two_sided_share']):.2%} | "
            f"{float(row['timing_mismatch_share']):.2%} | "
            f"{float(row['horizon_conflict_share']):.2%} |"
        )
    lines.extend(
        [
            "",
            "## Calibration",
            "",
            (
                "- Consolidated vote-share ECE: "
                f"{float(summary['consolidated_expected_calibration_error']):.4f}"
            ),
            (
                "- Individual horizon-head ECE: "
                f"{float(summary['horizon_head_expected_calibration_error']):.4f}"
            ),
            "",
            "## Largest supported slice deviations",
            "",
            "| Dimension | Value | Cases | Accuracy | 95% CI | Direction accuracy |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in ranked:
        lines.append(
            f"| {row['dimension']} | {row['value']} | {int(row['support']):,} | "
            f"{float(row['accuracy']):.2%} | "
            f"{float(row['accuracy_ci95_low']):.2%}-"
            f"{float(row['accuracy_ci95_high']):.2%} | "
            f"{float(row['direction_accuracy']):.2%} |"
        )
    lines.extend(
        [
            "",
            "## Review workflow",
            "",
            "Review `human_review_sample.csv` together with "
            "`embedding_neighbors.jsonl.gz` and `price_paths.jsonl.gz`. Apply "
            "the reason and label-quality codes in `ERROR_STUDY_GUIDE.md`. "
            "Treat 2026 as development evidence after this analysis; validate "
            "subsequent changes on a later untouched period.",
            "",
        ]
    )
    neighbors = summary.get("neighbor_diagnostics") or {}
    if neighbors.get("enabled"):
        lines.extend(
            [
                "## Embedding-neighbor diagnostic",
                "",
                (
                    f"- Mean exact cosine similarity: "
                    f"{float(neighbors['mean_cosine_similarity']):.4f}"
                ),
                (
                    f"- Horizon-label agreement with the reviewed query: "
                    f"{float(neighbors['horizon_label_agreement']):.2%}"
                ),
                (
                    f"- Queries whose nearest neighbors have mixed outcomes: "
                    f"{float(neighbors['mixed_neighbor_outcome_share']):.2%}"
                ),
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_study(inputs: StudyInputs) -> dict[str, Any]:
    started = time.perf_counter()
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    loader = _loader_from_checkpoint(inputs.checkpoint, inputs.prepared_root)
    inputs.output_dir.mkdir(parents=True, exist_ok=True)
    prediction_groups, prediction_rows = _read_predictions(inputs.predictions)
    arrays, prepared_manifest = open_arrays(loader)
    try:
        articles, horizons = build_article_audit(
            prediction_groups,
            arrays,
            start=inputs.start,
            end_exclusive=inputs.end_exclusive,
        )
        print(
            f"AUDIT READY | articles={len(articles):,} horizons={len(horizons):,}",
            flush=True,
        )
        news_audit: dict[str, Any] = {"enabled": False}
        if inputs.news_enrichment:
            news_audit = {
                "enabled": True,
                **enrich_news(
                    articles,
                    loader,
                    start=inputs.start,
                    end_exclusive=inputs.end_exclusive,
                ),
            }
            print(f"NEWS READY | {news_audit}", flush=True)
        taxonomy = taxonomy_rows(articles)
        calibration = calibration_rows(articles, horizons)
        slices = slice_rows(articles, inputs.minimum_slice_support)
        review_rows, review_counts = stratified_review_sample(
            articles,
            per_stratum=inputs.review_per_stratum,
            seed=inputs.seed,
        )
        _jsonl_gzip(inputs.output_dir / "article_audit.jsonl.gz", articles)
        _write_csv(
            inputs.output_dir / "error_taxonomy.csv",
            taxonomy,
            (
                "error_type",
                "support",
                "share",
                "accuracy",
                "coverage",
                "direction_accuracy",
                "two_sided_share",
                "timing_mismatch_share",
                "horizon_conflict_share",
            ),
        )
        calibration_fields = tuple(
            dict.fromkeys(
                key for row in calibration for key in row
            )
        )
        _write_csv(
            inputs.output_dir / "confidence_calibration.csv",
            calibration,
            calibration_fields,
        )
        slice_fields = tuple(dict.fromkeys(key for row in slices for key in row))
        _write_csv(inputs.output_dir / "slice_metrics.csv", slices, slice_fields)
        review_fields = (
            "review_stratum",
            "canonical_news_id",
            "ticker",
            "published_at_utc",
            "publication_session",
            "predicted_decision",
            "actual_decision",
            "error_type",
            "votes_no_opportunity",
            "votes_upside",
            "votes_downside",
            "vote_share",
            "vote_margin",
            "upside_strength",
            "downside_strength",
            "two_sided_actual",
            "timing_mismatch",
            "horizon_prediction_conflict",
            "anchor_price",
            "price_bucket",
            "nearby_same_ticker_news_5m",
            "nearby_same_ticker_news_30m",
            "news_kind",
            "news_topics",
            "title",
            "teaser",
            "author",
            "url_domain",
            "manual_primary_reason",
            "manual_secondary_reason",
            "manual_label_quality",
            "manual_notes",
        )
        csv_review_rows = [
            {
                **row,
                "news_topics": "|".join(row.get("news_topics") or []),
            }
            for row in review_rows
        ]
        _write_csv(
            inputs.output_dir / "human_review_sample.csv",
            csv_review_rows,
            review_fields,
        )
        neighbor_count = 0
        neighbor_news_audit: dict[str, Any] = {"enabled": False}
        neighbor_summary: dict[str, Any] = {"enabled": False}
        if inputs.embedding_neighbors:
            neighbor_rows = list(
                embedding_neighbor_rows(
                    review_rows,
                    arrays,
                    train_end_exclusive="2026-01-01",
                    top_k=inputs.neighbor_top_k,
                    candidate_count=inputs.neighbor_candidates,
                    projection_dim=inputs.neighbor_projection_dim,
                    batch_size=inputs.neighbor_batch_size,
                    device_name=inputs.neighbor_device,
                    seed=inputs.seed,
                )
            )
            review_strata = {
                _identity(row["canonical_news_id"], row["ticker"]): row["review_stratum"]
                for row in review_rows
            }
            for row in neighbor_rows:
                row["review_stratum"] = review_strata.get(
                    _identity(row["canonical_news_id"], row["ticker"]),
                    "unassigned",
                )
            if inputs.news_enrichment:
                neighbor_news_audit = {
                    "enabled": True,
                    **enrich_neighbor_metadata(neighbor_rows, loader),
                }
            neighbor_summary = {"enabled": True, **neighbor_diagnostics(neighbor_rows)}
            neighbor_count = _jsonl_gzip(
                inputs.output_dir / "embedding_neighbors.jsonl.gz",
                neighbor_rows,
            )
        path_count = 0
        path_audit: dict[str, Any] = {"enabled": False}
        if inputs.price_paths:
            path_audit = {"enabled": True}
            path_count = _jsonl_gzip(
                inputs.output_dir / "price_paths.jsonl.gz",
                price_path_rows(
                    review_rows,
                    loader,
                    workers=inputs.price_path_workers,
                    audit=path_audit,
                ),
            )
        summary = _summary(
            articles,
            taxonomy,
            calibration,
            slices,
            prepared_dataset_version=loader.prepared_dataset_version,
        )
        summary.update(
            {
                "study_version": ERROR_STUDY_VERSION,
                "model_version": MODEL_VERSION,
                "range": [inputs.start, inputs.end_exclusive],
                "prediction_rows": prediction_rows,
                "review": review_counts,
                "review_rows": len(review_rows),
                "neighbor_rows": neighbor_count,
                "neighbor_news_enrichment": neighbor_news_audit,
                "neighbor_diagnostics": neighbor_summary,
                "price_path_rows": path_count,
                "price_path_audit": path_audit,
                "news_enrichment": news_audit,
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
        _json_dump(inputs.output_dir / "error_study_summary.json", summary)
        write_markdown_report(
            inputs.output_dir / "error_study_report.md",
            summary,
            slices,
        )
        manifest = {
            "study_version": ERROR_STUDY_VERSION,
            "status": "complete",
            "model_version": MODEL_VERSION,
            "checkpoint": str(inputs.checkpoint),
            "checkpoint_sha256": _sha256(inputs.checkpoint),
            "predictions": str(inputs.predictions),
            "predictions_sha256": _sha256(inputs.predictions),
            "prepared_root": str(loader.prepared_dataset_root),
            "prepared_representation_sha256": str(
                prepared_manifest["representation_sha256"]
            ),
            "range": [inputs.start, inputs.end_exclusive],
            "seed": inputs.seed,
            "configuration": {
                "news_enrichment": inputs.news_enrichment,
                "price_paths": inputs.price_paths,
                "embedding_neighbors": inputs.embedding_neighbors,
                "review_per_stratum": inputs.review_per_stratum,
                "minimum_slice_support": inputs.minimum_slice_support,
                "neighbor_top_k": inputs.neighbor_top_k,
                "neighbor_candidates": inputs.neighbor_candidates,
                "neighbor_projection_dim": inputs.neighbor_projection_dim,
                "neighbor_batch_size": inputs.neighbor_batch_size,
                "neighbor_device": inputs.neighbor_device,
                "price_path_workers": inputs.price_path_workers,
            },
            "outputs": sorted(
                path.name for path in inputs.output_dir.iterdir() if path.is_file()
            ),
        }
        _json_dump(inputs.output_dir / "manifest.json", manifest)
        print(
            f"COMPLETED | articles={len(articles):,} review={len(review_rows):,} "
            f"neighbors={neighbor_count:,} paths={path_count:,} "
            f"output={inputs.output_dir}",
            flush=True,
        )
        return summary
    finally:
        close_arrays(arrays)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    run_study(inputs_from_args(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
