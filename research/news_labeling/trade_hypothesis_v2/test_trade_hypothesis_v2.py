from __future__ import annotations

import unittest

from .contract import REACTION_HORIZONS, validate_hypothesis
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


if __name__ == "__main__":
    unittest.main()
