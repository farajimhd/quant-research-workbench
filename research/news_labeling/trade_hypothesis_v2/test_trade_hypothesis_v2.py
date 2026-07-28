from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.news_prior_context import prior_news_context

from .contract import REACTION_HORIZONS, validate_hypothesis
from .prepare import historical_market_snapshot
from .runner_common import chat_body


class TradeHypothesisContractTests(unittest.TestCase):
    def test_accepts_exact_fixed_horizon_contract(self) -> None:
        row = {
            "upside_probability": 0.4,
            "downside_probability": 0.3,
            "no_action_probability": 0.3,
            "expected_return_pct": 0.1,
            "favorable_excursion_pct": 0.5,
            "adverse_excursion_pct": 0.4,
            "confidence": 0.4,
            "abstain": False,
        }
        validate_hypothesis(
            {
                "predictions": {horizon: dict(row) for horizon in REACTION_HORIZONS},
                "regime_compatibility": "neutral",
                "evidence": [],
                "conflicts": [],
                "invalidation_conditions": [],
                "uncertainty": "",
            }
        )

    def test_rejects_missing_horizon(self) -> None:
        with self.assertRaisesRegex(ValueError, "every fixed reaction horizon"):
            validate_hypothesis({"predictions": {}})

    def test_rejects_incoherent_probabilities(self) -> None:
        row = {
            "upside_probability": 0.8,
            "downside_probability": 0.4,
            "no_action_probability": 0.2,
            "expected_return_pct": 0.1,
            "favorable_excursion_pct": 0.5,
            "adverse_excursion_pct": 0.4,
            "confidence": 0.8,
            "abstain": False,
        }
        with self.assertRaisesRegex(ValueError, "sum"):
            validate_hypothesis(
                {
                    "predictions": {
                        horizon: dict(row) for horizon in REACTION_HORIZONS
                    },
                    "regime_compatibility": "neutral",
                    "evidence": [],
                    "conflicts": [],
                    "invalidation_conditions": [],
                    "uncertainty": "",
                }
            )

    def test_model_request_never_contains_evaluation_targets(self) -> None:
        body = chat_body(
            {
                "canonical_news_id": "article",
                "context": {"title": "causal input"},
                "targets": {"1m": {"terminal_return_pct": 999.0}},
            },
            model="model",
            max_output_tokens=100,
            reasoning_effort="none",
        )
        serialized = str(body)
        self.assertIn("causal input", serialized)
        self.assertNotIn("terminal_return_pct", serialized)
        self.assertNotIn("999", serialized)

    def test_provider_qualified_symbol_is_valid_without_semantic_table(self) -> None:
        client = FakeClickHouse([""])
        rows = prior_news_context(
            client,
            canonical_news_id="current",
            ticker="X:BTCUSD",
            as_of_utc="2026-07-14T12:00:00Z",
            include_semantic=False,
        )
        self.assertEqual(rows, [])
        self.assertNotIn("`news_semantic_label_v1`", client.queries[0])

    def test_optional_semantic_table_is_detected_before_join(self) -> None:
        client = FakeClickHouse(['{"rows":0}\n', ""])
        rows = prior_news_context(
            client,
            canonical_news_id="current",
            ticker="AAPL",
            as_of_utc="2026-07-14T12:00:00Z",
        )
        self.assertEqual(rows, [])
        self.assertIn("FROM system.tables", client.queries[0])
        self.assertNotIn("`news_semantic_label_v1`", client.queries[1])

    def test_prior_news_result_contract_uses_stable_aliases(self) -> None:
        prior = {
            "canonical_news_id": "prior",
            "published_at_utc": "2026-07-14 11:00:00.000000000",
            "title": "Earlier item",
            "rendered_excerpt": "Text",
            "channels": ["News"],
            "provider_tags": ["company"],
            "semantic_json": "",
        }
        client = FakeClickHouse([json_line(prior), ""])
        rows = prior_news_context(
            client,
            canonical_news_id="current",
            ticker="AAPL",
            as_of_utc="2026-07-14T12:00:00Z",
            include_semantic=False,
        )
        self.assertEqual(rows[0]["canonical_news_id"], "prior")
        self.assertEqual(rows[0]["completed_reactions"], {})
        self.assertIn("AS canonical_news_id", client.queries[0])

    def test_prior_news_schema_drift_reports_returned_keys(self) -> None:
        client = FakeClickHouse(
            ['{"t.canonical_news_id":"prior","published_at_utc":"timestamp"}\n']
        )
        with self.assertRaisesRegex(RuntimeError, "returned=.*t.canonical_news_id"):
            prior_news_context(
                client,
                canonical_news_id="current",
                ticker="AAPL",
                as_of_utc="2026-07-14T12:00:00Z",
                include_semantic=False,
            )

    def test_historical_snapshot_uses_latest_actual_event_day(self) -> None:
        client = FakeClickHouse(
            [
                json_line(
                    {
                        "source_event_date": "2026-07-10",
                        "last_price": 407.59,
                        "last_event_us": 1783737599752944,
                    }
                )
            ]
        )
        result = historical_market_snapshot(
            client,
            "TSLA",
            datetime(2026, 7, 12, 16, tzinfo=UTC),
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["source_event_date"], "2026-07-10")
        self.assertIn("GROUP BY event_date", client.queries[0])
        self.assertIn("ORDER BY last_event_us DESC", client.queries[0])
        self.assertNotIn(" FINAL", client.queries[0])


class FakeClickHouse:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.queries: list[str] = []

    def execute(self, query: str) -> str:
        self.queries.append(query)
        return next(self.responses)


def json_line(value: dict[str, object]) -> str:
    import json

    return json.dumps(value) + "\n"


if __name__ == "__main__":
    unittest.main()
