from __future__ import annotations

import unittest

from services.reference_gateway.ibkr_contract_identity import (
    company_names_compatible,
    ibkr_search_symbols,
    normalize_equity_symbol,
    resolve_massive_ibkr_contract,
)


class IbkrContractIdentityTests(unittest.TestCase):
    def test_symbol_normalization_preserves_share_class(self) -> None:
        self.assertEqual(normalize_equity_symbol("BRK.A"), "BRK A")
        self.assertEqual(normalize_equity_symbol("BRK B"), "BRK B")
        self.assertNotEqual(normalize_equity_symbol("BRK.A"), normalize_equity_symbol("BRK.B"))
        self.assertEqual(ibkr_search_symbols("BRK.A"), ("BRK.A", "BRK A"))

    def test_company_name_matching_accepts_legal_suffix_and_truncation(self) -> None:
        self.assertTrue(company_names_compatible("ABM Industries Incorporated", "ABM INDUSTRIES INC"))
        self.assertTrue(
            company_names_compatible(
                "Alexandria Real Estate Equities, Inc.",
                "ALEXANDRIA REAL ESTATE EQUIT",
            )
        )
        self.assertFalse(company_names_compatible("American Eagle Outfitters", "Aeorema Communications Group"))

    def test_resolver_selects_same_us_primary_security_from_symbol_collisions(self) -> None:
        resolution = resolve_massive_ibkr_contract(
            massive_ticker="AEO",
            massive_name="American Eagle Outfitters, Inc.",
            massive_exchange="XNYS",
            definitions=[
                {
                    "conid": 4_725_951,
                    "ticker": "AEO",
                    "name": "AMERICAN EAGLE OUTFITTERS",
                    "assetClass": "STK",
                    "type": "COMMON",
                    "currency": "USD",
                    "countryCode": "US",
                    "isUS": True,
                    "listingExchange": "NYSE",
                },
                {
                    "conid": 748_086_778,
                    "ticker": "AEO",
                    "name": "AEOREMA COMMUNICATIONS GROUP",
                    "assetClass": "STK",
                    "type": "COMMON",
                    "currency": "GBP",
                    "countryCode": "GB",
                    "isUS": False,
                    "listingExchange": "LSE",
                },
            ],
        )

        self.assertTrue(resolution.accepted)
        self.assertEqual(resolution.conid, 4_725_951)

    def test_resolver_accepts_ibkr_space_share_class_only_when_class_matches(self) -> None:
        resolution = resolve_massive_ibkr_contract(
            massive_ticker="BRK.B",
            massive_name="Berkshire Hathaway Inc. Class B",
            massive_exchange="XNYS",
            definitions=[
                {
                    "conid": 72_063_691,
                    "ticker": "BRK B",
                    "name": "BERKSHIRE HATHAWAY INC-CL B",
                    "assetClass": "STK",
                    "type": "COMMON",
                    "currency": "USD",
                    "countryCode": "US",
                    "isUS": True,
                    "listingExchange": "NYSE",
                }
            ],
        )

        self.assertTrue(resolution.accepted)
        self.assertEqual(resolution.conid, 72_063_691)

    def test_resolver_accepts_exact_primary_listing_but_flags_company_name_difference(self) -> None:
        resolution = resolve_massive_ibkr_contract(
            massive_ticker="TEST",
            massive_name="Expected Issuer Incorporated",
            massive_exchange="XNAS",
            definitions=[
                {
                    "conid": 123,
                    "ticker": "TEST",
                    "name": "UNRELATED COMPANY",
                    "assetClass": "STK",
                    "type": "COMMON",
                    "currency": "USD",
                    "countryCode": "US",
                    "isUS": True,
                    "listingExchange": "NASDAQ",
                }
            ],
        )

        self.assertTrue(resolution.accepted)
        self.assertEqual(resolution.reason, "unique_primary_listing_company_name_differs")
        self.assertFalse(resolution.company_name_match)

    def test_resolver_blocks_multiple_same_security_candidates(self) -> None:
        definitions = [
            {
                "conid": conid,
                "ticker": "TEST",
                "name": "TEST ISSUER",
                "assetClass": "STK",
                "type": "COMMON",
                "currency": "USD",
                "countryCode": "US",
                "isUS": True,
                "listingExchange": "NASDAQ",
            }
            for conid in (123, 456)
        ]
        resolution = resolve_massive_ibkr_contract(
            massive_ticker="TEST",
            massive_name="Test Issuer Inc.",
            massive_exchange="XNAS",
            definitions=definitions,
        )

        self.assertFalse(resolution.accepted)
        self.assertEqual(resolution.reason, "multiple_matching_primary_contracts")

    def test_resolver_keeps_conid_audit_independent_of_product_subtype_label(self) -> None:
        resolution = resolve_massive_ibkr_contract(
            massive_ticker="PREF",
            massive_name="Example Depositary Shares",
            massive_exchange="XNAS",
            definitions=[
                {
                    "conid": 789,
                    "ticker": "PREF",
                    "name": "EXAMPLE DEPOSITARY SHARES",
                    "assetClass": "STK",
                    "type": "PREFERRED",
                    "currency": "USD",
                    "countryCode": "US",
                    "isUS": True,
                    "listingExchange": "NASDAQ",
                }
            ],
        )

        self.assertTrue(resolution.accepted)
        self.assertEqual(resolution.conid, 789)


if __name__ == "__main__":
    unittest.main()
