from __future__ import annotations

import unittest

from .candidate_contract import enrich_candidate_rows, explicit_us_ticker_evidence


class CandidateContractTests(unittest.TestCase):
    def test_announced_prelisting_symbol_is_discovered(self) -> None:
        rows = enrich_candidate_rows(
            [{"ticker": "C", "identity_evidence": ["symbol:C"]}],
            title="Primerica IPO Preview (PRI)",
            teaser="",
            rendered_text=(
                "The company is being spun off from Citigroup (NYSE: C) and will "
                "trade on the New York Stock Exchange under the ticker PRI."
            ),
        )
        self.assertEqual(
            [row["canonical_instrument_id"] for row in rows], ["C", "PRI"]
        )
        self.assertTrue(
            any("announced_us_listing" in value for value in rows[1]["identity_evidence"])
        )

    def test_foreign_exchange_symbols_are_not_us_candidates(self) -> None:
        evidence = explicit_us_ticker_evidence(
            "China Merchants Bank (3968.HK, 600036.SH) weakened."
        )
        self.assertEqual(evidence, {})

    def test_generic_parenthetical_capitals_are_not_guessed(self) -> None:
        evidence = explicit_us_ticker_evidence(
            "The Food and Drug Administration (FDA) reviewed earnings per share (EPS)."
        )
        self.assertEqual(evidence, {})

    def test_exchange_qualified_symbol_is_discovered_and_deduplicated(self) -> None:
        rows = enrich_candidate_rows(
            [{"ticker": "AAPL", "identity_evidence": ["symbol:AAPL"]}],
            title="Apple update",
            teaser="NASDAQ: AAPL",
            rendered_text="Apple Inc. (NASDAQ: AAPL) provided an update.",
        )
        self.assertEqual([row["canonical_instrument_id"] for row in rows], ["AAPL"])
        self.assertGreaterEqual(len(rows[0]["identity_evidence"]), 2)

    def test_authoritative_non_equity_instrument_is_preserved(self) -> None:
        rows = enrich_candidate_rows(
            [{"ticker": "X:UNIUSD", "identity_evidence": ["provider_link"]}],
            title="Uniswap update",
            teaser="",
            rendered_text="",
        )
        self.assertEqual(
            rows,
            [
                {
                    "canonical_instrument_id": "X:UNIUSD",
                    "display_symbol": "UNI/USD",
                    "instrument_type": "crypto_pair",
                    "identity_evidence": ["provider_link"],
                }
            ],
        )

    def test_provider_identifier_is_merged_into_typed_roster(self) -> None:
        rows = enrich_candidate_rows(
            [],
            title="Uniswap declines",
            teaser="Uniswap (CRYPTO: UNI) declined.",
            rendered_text="",
            authoritative_identifiers=["X:UNIUSD"],
        )
        self.assertEqual(rows[0]["canonical_instrument_id"], "X:UNIUSD")
        self.assertEqual(rows[0]["display_symbol"], "UNI/USD")


if __name__ == "__main__":
    unittest.main()
