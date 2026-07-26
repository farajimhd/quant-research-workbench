from __future__ import annotations

import datetime as dt
import unittest
from collections import Counter
from types import SimpleNamespace

import numpy as np

from research.news_reaction_model.v16 import HORIZONS
from research.news_reaction_model.v16.error_study import (
    CLASS_DOWN,
    CLASS_NONE,
    CLASS_UP,
    _actual_decision,
    _error_type,
    _path_features,
    _strengths,
    _vote_decision,
    build_article_audit,
    calibration_rows,
    embedding_neighbor_rows,
    slice_rows,
    stratified_review_sample,
    taxonomy_rows,
)
from research.news_reaction_model.v16.market_context import (
    CURRENT_MARKET_FEATURE_DIM,
    CURRENT_MARKET_FEATURE_NAMES,
)
from research.news_reaction_model.v16.market_data import (
    DayMarketData,
    daily_minute_bars_sql,
)
from research.news_reaction_model.v16.stock_state import STOCK_STATE_DIM


def prediction(
    *,
    canonical_news_id: str,
    ticker: str,
    published_at: str,
    horizon: str,
    predicted: int,
    actual: int,
    high: float,
    low: float,
    confidence: float = 0.7,
) -> dict[str, object]:
    probabilities = [0.1, 0.1, 0.1]
    probabilities[predicted] = confidence
    remaining = (1.0 - confidence) / 2.0
    for index in range(3):
        if index != predicted:
            probabilities[index] = remaining
    return {
        "canonical_news_id": canonical_news_id,
        "ticker": ticker,
        "published_at_utc": published_at,
        "horizon": horizon,
        "predicted_class": predicted,
        "actual_class": actual,
        "confidence": confidence,
        "probabilities": {
            "no_meaningful_opportunity": probabilities[0],
            "upside_dominant": probabilities[1],
            "downside_dominant": probabilities[2],
        },
        "position": 0 if predicted == CLASS_NONE else 1 if predicted == CLASS_UP else -1,
        "anchor_price": 10.0,
        "actual_high_return": high,
        "actual_low_return": low,
    }


class ErrorStudyTests(unittest.TestCase):
    def test_vote_and_actual_contracts_are_explicit(self) -> None:
        decision, winning, margin, tied = _vote_decision(
            Counter({CLASS_UP: 6, CLASS_DOWN: 2, CLASS_NONE: 2})
        )
        self.assertEqual((decision, winning, margin, tied), (CLASS_UP, 6, 4, False))
        decision, _, _, tied = _vote_decision(
            Counter({CLASS_UP: 4, CLASS_DOWN: 4, CLASS_NONE: 2})
        )
        self.assertEqual(decision, CLASS_NONE)
        self.assertTrue(tied)
        rows = [
            prediction(
                canonical_news_id="a",
                ticker="AAA",
                published_at="2026-01-02 15:00:00+00:00",
                horizon="1m",
                predicted=CLASS_UP,
                actual=CLASS_UP,
                high=0.002,
                low=-0.0001,
            ),
            prediction(
                canonical_news_id="a",
                ticker="AAA",
                published_at="2026-01-02 15:00:00+00:00",
                horizon="5m",
                predicted=CLASS_UP,
                actual=CLASS_UP,
                high=0.003,
                low=-0.001,
            ),
        ]
        upside, downside = _strengths(rows)
        self.assertGreater(upside, downside)
        self.assertEqual(_actual_decision(upside, downside), CLASS_UP)
        self.assertEqual(_error_type(CLASS_DOWN, CLASS_UP), "false_short")

    def test_article_audit_reuses_prepared_context(self) -> None:
        published = (
            dt.datetime(2026, 1, 2, 15, tzinfo=dt.timezone.utc).timestamp()
            * 1_000_000
        )
        arrays = {
            "canonical_news_id": np.asarray([b"a"], dtype="S64"),
            "ticker": np.asarray([b"AAA"], dtype="S32"),
            "published_at_utc": np.asarray(
                [b"2026-01-02 15:00:00+00:00"], dtype="S40"
            ),
            "published_at_us": np.asarray([published], dtype=np.int64),
            "publication_session": np.asarray([b"regular"], dtype="S16"),
            "stock_state": np.zeros((1, STOCK_STATE_DIM), dtype=np.float32),
            "current_market_features": np.zeros(
                (1, CURRENT_MARKET_FEATURE_DIM), dtype=np.float32
            ),
            "context_mask": np.asarray([[True, False, False, False]]),
            "market_context_mask": np.asarray([[True, True, False]]),
            "market_leader_mask": np.asarray([[True, False]]),
        }
        arrays["stock_state"][0, -10] = 1.0
        rank_index = CURRENT_MARKET_FEATURE_NAMES.index("is_top20_gainer")
        arrays["current_market_features"][0, rank_index] = 1.0
        rows = [
            prediction(
                canonical_news_id="a",
                ticker="AAA",
                published_at="2026-01-02 15:00:00+00:00",
                horizon="1m",
                predicted=CLASS_UP,
                actual=CLASS_UP,
                high=0.002,
                low=-0.0001,
            )
        ]
        articles, horizons = build_article_audit(
            {("a", "AAA"): rows},
            arrays,
            start="2026-01-01",
            end_exclusive="2027-01-01",
        )
        self.assertEqual(len(horizons), 1)
        self.assertEqual(articles[0]["prior_context_count"], 1)
        self.assertEqual(articles[0]["market_context_count"], 2)
        self.assertTrue(articles[0]["is_top20_gainer"])
        self.assertEqual(articles[0]["error_type"], "correct")

    def test_reports_and_sampling_are_deterministic(self) -> None:
        rows = []
        for index in range(120):
            predicted = CLASS_UP
            actual = CLASS_DOWN if index < 110 else CLASS_UP
            rows.append(
                {
                    "canonical_news_id": str(index),
                    "ticker": "AAA",
                    "published_at_us": index,
                    "published_at_utc": "2026-01-01",
                    "publication_session": "regular",
                    "predicted_class": predicted,
                    "predicted_decision": "upside_dominant",
                    "actual_class": actual,
                    "actual_decision": (
                        "downside_dominant" if actual == CLASS_DOWN else "upside_dominant"
                    ),
                    "correct": predicted == actual,
                    "error_type": _error_type(predicted, actual),
                    "vote_share": 0.8,
                    "vote_margin": 4,
                    "two_sided_actual": False,
                    "timing_mismatch": False,
                    "horizon_prediction_conflict": False,
                    "prior_context_count": 1,
                    "market_context_count": 50,
                    "nearby_same_ticker_news_5m": 0,
                    "nearby_same_ticker_news_30m": 0,
                    "price_bucket": "small_1_20",
                    "ticker_frequency_bucket": "gt_100",
                    "is_top20_gainer": False,
                    "is_top20_loser": False,
                    "is_top20_volume": False,
                    "is_top20_relative_volume": False,
                }
            )
        taxonomy = taxonomy_rows(rows)
        self.assertEqual(sum(item["support"] for item in taxonomy), 120)
        self.assertTrue(slice_rows(rows, minimum_support=100))
        first, counts = stratified_review_sample(rows, per_stratum=10, seed=17)
        second, _ = stratified_review_sample(rows, per_stratum=10, seed=17)
        self.assertEqual(
            [row["canonical_news_id"] for row in first],
            [row["canonical_news_id"] for row in second],
        )
        self.assertEqual(counts["confident_false_long"]["selected"], 10)
        calibration = calibration_rows(
            rows,
            [
                {
                    "horizon": "1m",
                    "confidence": 0.8,
                    "correct": value["correct"],
                    "predicted_class": value["predicted_class"],
                }
                for value in rows
            ],
        )
        self.assertTrue(calibration)

    def test_market_query_filters_tickers_and_preserves_ohlc_order(self) -> None:
        config = SimpleNamespace(
            events_table_base="events",
            market_database="market_sip_compact",
            condition_reference_table="event_condition_token_reference",
            market_max_threads=4,
            market_max_memory_usage="16G",
        )
        sql = daily_minute_bars_sql(
            config,
            dt.date(2026, 7, 14),
            tickers=("AAPL", "MSFT", "AAPL"),
        )
        self.assertIn("ticker IN ('AAPL','MSFT')", sql)
        self.assertLess(sql.index(") AS open"), sql.index(") AS high"))
        self.assertLess(sql.index(") AS high"), sql.index(") AS low"))
        self.assertLess(sql.index(") AS low"), sql.index(") AS close"))
        day = DayMarketData(
            dt.date(2026, 7, 14),
            [("AAPL", 100, 10.0, 12.0, 9.0, 11.0, 50.0, 550.0, 3, 4)],
            rows_chronological=True,
        )
        self.assertEqual(
            day.minute_rows("AAPL")[0],
            {
                "minute_end_us": 100,
                "open": 10.0,
                "high": 12.0,
                "low": 9.0,
                "close": 11.0,
                "volume": 50.0,
                "dollar_volume": 550.0,
                "trade_count": 3,
                "quote_count": 4,
            },
        )

    def test_embedding_neighbors_rerank_exact_cosine(self) -> None:
        embeddings = np.asarray(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.9, 0.1, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.95, 0.05, 0.0, 0.0],
            ],
            dtype=np.float32,
        )
        targets = np.zeros((4, len(HORIZONS), 3), dtype=np.float32)
        targets[:, :, 1] = 0.002
        arrays = {
            "openai_embedding": embeddings,
            "published_at_us": np.asarray(
                [
                    1_600_000_000_000_000,
                    1_610_000_000_000_000,
                    1_620_000_000_000_000,
                    1_770_000_000_000_000,
                ],
                dtype=np.int64,
            ),
            "canonical_news_id": np.asarray([b"a", b"b", b"c", b"q"], dtype="S64"),
            "ticker": np.asarray([b"AAA", b"BBB", b"CCC", b"QQQ"], dtype="S32"),
            "published_at_utc": np.asarray(
                [
                    b"2020-01-01",
                    b"2021-01-01",
                    b"2022-01-01",
                    b"2026-02-01",
                ],
                dtype="S40",
            ),
            "return_targets": targets,
            "label_mask": np.ones((4, len(HORIZONS)), dtype=bool),
        }
        rows = list(
            embedding_neighbor_rows(
                [{"prepared_row_index": 3}],
                arrays,
                train_end_exclusive="2026-01-01",
                top_k=1,
                candidate_count=3,
                projection_dim=8,
                batch_size=256,
                device_name="cpu",
                seed=17,
            )
        )
        self.assertEqual(rows[0]["neighbors"][0]["canonical_news_id"], "a")

    def test_price_path_features_detect_pre_move_and_fade(self) -> None:
        published = 10 * 60_000_000
        bars = [
            {
                "minute_end_us": 9 * 60_000_000,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 10.0,
                "trade_count": 2,
                "large_jump_from_previous_close": False,
            },
            {
                "minute_end_us": 10 * 60_000_000,
                "open": 10.0,
                "high": 10.1,
                "low": 10.0,
                "close": 10.1,
                "volume": 10.0,
                "trade_count": 2,
                "large_jump_from_previous_close": False,
            },
            {
                "minute_end_us": 11 * 60_000_000,
                "open": 10.1,
                "high": 11.0,
                "low": 10.1,
                "close": 10.2,
                "volume": 100.0,
                "trade_count": 10,
                "large_jump_from_previous_close": False,
            },
        ]
        features = _path_features(
            bars,
            {"SPY": [], "QQQ": []},
            published_us=published,
            anchor_price=10.1,
        )
        self.assertTrue(features["movement_started_before_publication"])
        self.assertTrue(features["post_peak_then_fade"])
        self.assertEqual(features["post_30m_trade_count"], 10)


if __name__ == "__main__":
    unittest.main()
