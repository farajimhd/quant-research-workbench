from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from .forecast_eligibility_sampling import _balanced_quotas, first_substantive_sentence
from .forecast_eligibility_review import _load_and_validate_reviews
from .storage import load_identity_index


class _IdentityClient:
    def __init__(self) -> None:
        self.query = ""

    def iter_json_each_row(self, query: str):
        self.query = query
        return iter(({
            "ticker": "TEST",
            "issuer_id": "issuer-1",
            "security_id": "security-1",
            "display_name": "Test Corp",
            "aliases": ["Test Corp"],
            "exchange_code": "XNYS",
            "list_date": "2020-01-01",
            "delisted_date": "",
        },))


class ForecastEligibilitySamplingTest(unittest.TestCase):
    def test_balanced_quotas_sum_and_differ_by_at_most_one(self) -> None:
        strata = [(year, session) for year in range(2020, 2027) for session in ("pre", "regular", "after")]
        quotas = _balanced_quotas(5000, strata)
        self.assertEqual(sum(quotas.values()), 5000)
        self.assertLessEqual(max(quotas.values()) - min(quotas.values()), 1)

    def test_first_substantive_sentence_skips_repeated_title(self) -> None:
        title = "Acme Reports Quarterly Results"
        text = "Acme Reports Quarterly Results. Acme said revenue increased 20% during the quarter. More follows."
        self.assertEqual(
            first_substantive_sentence(text, title),
            "Acme said revenue increased 20% during the quarter.",
        )

    def test_first_substantive_sentence_preserves_abbreviations(self) -> None:
        text = "Example Inc. announced a new supply agreement worth $20 million. Additional details follow."
        self.assertEqual(
            first_substantive_sentence(text, "Supply Agreement"),
            "Example Inc. announced a new supply agreement worth $20 million.",
        )

    def test_identity_query_has_stable_json_field_aliases(self) -> None:
        client = _IdentityClient()
        index = load_identity_index(client, "q_live")
        self.assertIn("sec.issuer_id AS issuer_id", client.query)
        self.assertIn("listing.exchange_code AS exchange_code", client.query)
        resolved = index.resolve(text="Test Corp announced results", candidates=("TEST",), timestamp="2024-01-01")
        self.assertEqual(resolved[0]["identity_status"], "resolved")

    def test_review_validation_rejects_population_gaps(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.jsonl"
            path.write_text(
                '{"review_id":"R1","eligibility":"eligible","eligible_tickers":[],"confidence":"high","reason":"current event"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "population mismatch"):
                _load_and_validate_reviews(
                    [path],
                    expected_inputs={"R1": {"tickers": []}, "R2": {"tickers": []}},
                )

    def test_review_validation_rejects_ticker_not_in_blind_input(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.jsonl"
            path.write_text(
                '{"review_id":"R1","eligibility":"eligible","eligible_tickers":["OTHER"],"confidence":"high","reason":"current event"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "non-input ticker"):
                _load_and_validate_reviews(
                    [path],
                    expected_inputs={"R1": {"tickers": ["TEST"]}},
                )


if __name__ == "__main__":
    unittest.main()
