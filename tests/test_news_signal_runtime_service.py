from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from src.backend.news_signal_runtime_service import NewsSignalRuntime, all_news_synthesis_events, news_synthesis_events
from src.trading_runtime.journal import TradingJournal


def configuration() -> dict:
    columns = [
        ("symbol", "identity.symbol"),
        ("last_price", "market.last_price"),
        ("spread_bps", "market.spread_bps"),
        ("news_deepfm_probability", "news.deepfm.eligible_probability"),
        ("news_forecast_eligible", "news.deepfm.forecast_eligible"),
        ("canonical_news_id", "news.canonical_news_id"),
        ("news_published_at", "news.published_at"),
    ]
    return {
        "market_discovery": {
            "column_catalog": [
                {"column_id": column_id, "source_id": source_id, "field_ref": source_id}
                for column_id, source_id in columns
            ],
            "rule_sets": [
                {
                    "rule_set_id": "watchlist-news-bullish",
                    "conditions": [
                        {"left_field_ref": "news.deepfm.forecast_eligible", "comparator": "is_true"},
                    ],
                }
            ],
            "signal_streams": [
                {
                    "signal_stream_id": "bullish-news-v1",
                    "name": "Bullish News",
                    "enabled": True,
                    "revision": 1,
                    "source_type": "news_events",
                    "source_id": "q_live.news_synthesis_v1",
                    "inclusion_rule_sets": ["watchlist-news-bullish"],
                    "columns": [column_id for column_id, _ in columns],
                    "column_intervals": {},
                    "watchlist_routes": [],
                }
            ],
        }
    }


def source_row(updated_at: str) -> dict:
    return {
        "canonical_news_id": "news-1",
        "published_at_utc": "2026-08-17T14:59:00+00:00",
        "updated_at_utc": updated_at,
        "source_tickers": ["ACME"],
        "funnel_forecast_eligibility": "eligible",
        "funnel_eligible_probability": 0.91,
        "funnel_stage": "deepfm_eligible",
        "synthesis_json": json.dumps(
            {
                "entities": [{"entity_id": "issuer-1", "ticker": "ACME"}],
                "issuer_views": [
                    {
                        "entity_id": "issuer-1",
                        "composite_sentiment": "positive",
                        "positive_strength": 3,
                        "negative_strength": 0,
                    }
                ],
                "eligibility": [
                    {
                        "entity_id": "issuer-1",
                        "product": "forecast_trigger",
                        "eligible": True,
                    }
                ],
            }
        ),
    }


class NewsSignalRuntimeTests(unittest.TestCase):
    def test_clickhouse_bounds_compare_native_datetimes_not_string_aliases(self) -> None:
        class Client:
            sql = ""

            def iter_json_each_row(self, sql: str):
                self.sql = sql
                return iter([{
                    "canonical_news_id": "news-1",
                    "published_at_text": "2026-08-17 14:59:00.000",
                    "updated_at_text": "2026-08-17 15:00:00.000",
                }])

        client = Client()
        rows = news_synthesis_events(
            start_at=datetime(2026, 8, 17, 14, 0, tzinfo=UTC),
            as_of=datetime(2026, 8, 17, 16, 0, tzinfo=UTC),
            client=client,
        )

        self.assertIn("toString(greatest(s.updated_at_utc", client.sql)
        self.assertNotIn("toString(updated_at_utc) AS updated_at_utc", client.sql)
        self.assertIn("greatest(s.updated_at_utc", client.sql)
        self.assertEqual(rows[0]["updated_at_utc"], "2026-08-17 15:00:00.000")
        self.assertEqual(rows[0]["published_at_utc"], "2026-08-17 14:59:00.000")

    def test_complete_news_interval_uses_stable_keyset_pages(self) -> None:
        calls: list[tuple[datetime | None, str]] = []
        rows = [
            {"canonical_news_id": f"news-{index}", "updated_at_utc": f"2026-08-17T15:00:0{index}+00:00"}
            for index in range(5)
        ]

        def loader(**kwargs):
            calls.append((kwargs["after_updated_at"], kwargs["after_canonical_id"]))
            start = 0
            if kwargs["after_updated_at"] is not None:
                cursor = kwargs["after_updated_at"].isoformat()
                start = next(
                    index + 1
                    for index, row in enumerate(rows)
                    if datetime.fromisoformat(row["updated_at_utc"]).isoformat() == cursor
                    and row["canonical_news_id"] == kwargs["after_canonical_id"]
                )
            return rows[start:start + kwargs["limit"]]

        result = all_news_synthesis_events(
            start_at=datetime(2026, 8, 17, 15, 0, tzinfo=UTC),
            as_of=datetime(2026, 8, 17, 16, 0, tzinfo=UTC),
            page_size=2,
            loader=loader,
        )
        self.assertEqual(result, rows)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][1], "news-1")

    def test_fresh_bullish_issuer_event_is_frozen_and_dispatchable(self) -> None:
        now = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        runtime = NewsSignalRuntime(
            loader=lambda **_: [source_row("2026-08-17T14:59:50+00:00")],
            publisher=lambda _stream_id, rows: {"new_occurrences": rows},
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            result = runtime.refresh(
                configuration(),
                [{"ticker": "ACME", "last_price": 12.5, "spread_bps": 20.0}],
                as_of=now,
                journal=journal,
            )
            journal.close()

        self.assertEqual(len(result["occurrences"]), 1)
        self.assertEqual(len(result["new_occurrences"]), 1)
        occurrence = result["occurrences"][0]
        self.assertEqual(occurrence["ticker"], "ACME")
        self.assertEqual(occurrence["news_deepfm_probability"], 0.91)
        self.assertEqual(occurrence["last_price"], 12.5)

    def test_synthesis_opinion_cannot_generate_a_signal(self) -> None:
        now = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        config = configuration()
        config["market_discovery"]["rule_sets"][0]["conditions"] = [{
            "left_field_ref": "news.composite_sentiment",
            "comparator": "equals",
            "value": "positive",
        }]
        runtime = NewsSignalRuntime(
            loader=lambda **_: [source_row("2026-08-17T14:59:50+00:00")],
            publisher=lambda _stream_id, rows: {"new_occurrences": rows},
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            result = runtime.refresh(config, [{"ticker": "ACME"}], as_of=now, journal=journal)
            journal.close()
        self.assertEqual(result["occurrences"], [])

    def test_stale_bootstrap_event_is_visible_but_not_live_dispatchable(self) -> None:
        now = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        runtime = NewsSignalRuntime(
            loader=lambda **_: [source_row("2026-08-17T14:00:00+00:00")],
            publisher=lambda _stream_id, rows: {"new_occurrences": rows},
        )
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            result = runtime.refresh(
                configuration(),
                [{"ticker": "ACME", "last_price": 12.5, "spread_bps": 20.0}],
                as_of=now,
                journal=journal,
            )
            journal.close()

        self.assertEqual(len(result["occurrences"]), 1)
        self.assertEqual(result["new_occurrences"], [])

    def test_manual_llm_review_fields_can_generate_a_signal(self) -> None:
        now = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        config = configuration()
        config["market_discovery"]["column_catalog"].append({
            "column_id": "llm_forecast_eligible",
            "source_id": "news.llm.forecast_eligible",
            "field_ref": "news.llm.forecast_eligible",
        })
        config["market_discovery"]["rule_sets"][0]["conditions"] = [{
            "left_field_ref": "news.llm.forecast_eligible",
            "left_source_id": "news.llm.forecast_eligible",
            "comparator": "is_true",
        }]
        source = {
            "canonical_news_id": "news-manual-1",
            "published_at_utc": "2026-08-17T14:59:00+00:00",
            "updated_at_utc": "2026-08-17T14:59:50+00:00",
            "event_authority": "llm_review",
            "issuer_labels_json": json.dumps({"issuers": [{
                "issuer_name": "Acme", "ticker": "ACME",
                "forecast_relevance_probability": 0.91,
                "positive_implication_probability": 0.8,
                "negative_implication_probability": 0.1,
            }]}),
        }
        runtime = NewsSignalRuntime(loader=lambda **_: [source], publisher=lambda _stream_id, rows: {"new_occurrences": rows})
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            result = runtime.refresh(config, [{"ticker": "ACME"}], as_of=now, journal=journal)
            journal.close()
        self.assertEqual(len(result["new_occurrences"]), 1)
        self.assertTrue(result["new_occurrences"][0]["llm_forecast_eligible"])

    def test_reaction_fields_can_generate_a_signal(self) -> None:
        now = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        config = configuration()
        config["market_discovery"]["column_catalog"].append({
            "column_id": "reaction_expected_return",
            "source_id": "news.reaction.5m.expected_return_pct",
            "field_ref": "news.reaction.5m.expected_return_pct",
        })
        config["market_discovery"]["rule_sets"][0]["conditions"] = [{
            "left_field_ref": "news.reaction.5m.expected_return_pct",
            "comparator": "greater_than",
            "value": 1.0,
        }]
        source = {
            "canonical_news_id": "news-reaction-1",
            "published_at_utc": "2026-08-17T14:59:00+00:00",
            "updated_at_utc": "2026-08-17T14:59:50+00:00",
            "event_authority": "reaction",
            "ticker": "ACME",
            "hypothesis_json": json.dumps({
                "regime_compatibility": "supportive",
                "predictions": {"5m": {"expected_return_pct": 1.4}},
            }),
        }
        runtime = NewsSignalRuntime(loader=lambda **_: [source], publisher=lambda _stream_id, rows: {"new_occurrences": rows})
        with tempfile.TemporaryDirectory() as directory:
            journal = TradingJournal(Path(directory) / "journal.sqlite3")
            result = runtime.refresh(config, [{"ticker": "ACME"}], as_of=now, journal=journal)
            journal.close()
        self.assertEqual(len(result["new_occurrences"]), 1)
        self.assertEqual(result["new_occurrences"][0]["reaction_expected_return"], 1.4)


if __name__ == "__main__":
    unittest.main()
