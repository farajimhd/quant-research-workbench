from __future__ import annotations

import unittest
from datetime import date

from services.reference_gateway.sec_company_profiles import (
    normalize_address_country,
    normalize_country,
    materialize_sec_company_profiles,
    parse_company_profile_ixbrl,
    sec_country_sql,
)


class SecCompanyProfileTests(unittest.TestCase):
    def test_sec_jurisdictions_are_not_treated_as_iso_codes(self) -> None:
        self.assertEqual(normalize_country("CA"), "US")
        self.assertEqual(normalize_country("E9"), "KY")
        self.assertEqual(normalize_country("K3"), "HK")
        self.assertEqual(normalize_country("P7"), "NL")
        self.assertEqual(normalize_country("Ontario, Canada"), "CA")
        self.assertEqual(normalize_country("Delaware"), "US")
        self.assertEqual(normalize_country("Wisconsin"), "US")
        self.assertEqual(normalize_country("GB"), "GB")
        self.assertIsNone(normalize_country("ON"))
        self.assertEqual(normalize_address_country("IN"), "IN")
        self.assertEqual(normalize_address_country("CA"), "CA")
        self.assertIsNone(normalize_country("unreviewed jurisdiction"))

    def test_ixbrl_parser_extracts_company_address_and_continuation(self) -> None:
        source = """
        <html><body>
          <ix:nonNumeric name="dei:EntityRegistrantName">Example <b>Holdings</b><ix:exclude>not part of fact</ix:exclude></ix:nonNumeric>
          <ix:nonNumeric name="dei:EntityIncorporationStateCountryCode">Cayman Islands</ix:nonNumeric>
          <ix:nonNumeric name="dei:EntityAddressAddressLine1" continuedAt="address-more">One Main</ix:nonNumeric>
          <ix:continuation id="address-more">Street</ix:continuation>
          <ix:nonNumeric name="dei:EntityAddressCityOrTown">George Town</ix:nonNumeric>
          <ix:nonNumeric name="dei:EntityAddressCountry">Cayman Islands</ix:nonNumeric>
        </body></html>
        """

        profile = parse_company_profile_ixbrl(source)

        self.assertEqual(profile["issuer_name"], "Example Holdings")
        self.assertEqual(profile["issuer_legal_country_code"], "KY")
        self.assertEqual(profile["business_address_line1"], "One Main Street")
        self.assertEqual(profile["business_address_city"], "George Town")
        self.assertEqual(profile["issuer_business_country_code"], "KY")

    def test_sql_mapping_fails_closed_for_unknown_codes(self) -> None:
        sql = sec_country_sql("state_code", "state_description")

        self.assertIn("CAST(NULL, 'Nullable(String)')", sql)
        self.assertIn("'E9'", sql)
        self.assertIn("'KY'", sql)

    def test_historical_materialization_uses_bounded_inventory_and_filing_batches(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.inserts: list[str] = []

            def iter_json_each_row(self, sql: str):
                if "id_sec_market_bridge_v3" in sql:
                    return iter([{"cik": "0000000001", "market_issuer_id": "issuer:1"}])
                if "market_issuer_company_profile_v1" in sql:
                    return iter([])
                if "SELECT filing_id, cik, accession_number, document_id" in sql:
                    self.assert_partition_bound(sql)
                    return iter([{"filing_id": "filing:1", "cik": "0000000001", "accession_number": "0000000001-25-000001", "document_id": "document:1", "content_format": "html", "content_sha256": "abc", "source_revision_rank": 7}])
                if "FROM `q_live`.`sec_filing_v3`" in sql:
                    self.assertIn("PREWHERE (cik, accession_number) IN (('0000000001', '0000000001-25-000001'))", sql)
                    self.assertIn("LIMIT 1 BY cik, accession_number", sql)
                    return iter([{"filing_id": "filing:1", "cik": "0000000001", "accession_number": "0000000001-25-000001", "accepted_at_utc": "2025-01-15 12:00:00.000000000", "form_type": "20-F"}])
                self.assertIn("(cik, accession_number, document_id, content_format, source_revision_rank) IN", sql)
                self.assertIn("'document:1', 'html', 7", sql)
                self.assertIn("positionCaseInsensitive(source_text, '<ix:') > 0", sql)
                if "sec_filing_text_v3` FINAL" in sql:
                    raise AssertionError("bounded source-text query must not use FINAL")
                if "LIMIT 1 BY" in sql or "ORDER BY source_revision_rank" in sql:
                    raise AssertionError("exact-revision source-text query must not sort full document bodies")
                self.assert_partition_bound(sql)
                return iter([{
                    "filing_id": "filing:1", "document_id": "document:1", "source_archive_path": "D:/archive.tar.gz",
                    "source_archive_member": "member.nc", "content_sha256": "abc", "source_revision_rank": 1,
                    "source_text": '<ix:nonNumeric name="dei:EntityRegistrantName">Example NV</ix:nonNumeric><ix:nonNumeric name="dei:EntityAddressCountry">Netherlands</ix:nonNumeric>',
                }])

            def execute(self, sql: str) -> str:
                self.inserts.append(sql)
                return ""

            def assertIn(self, expected: str, actual: str) -> None:
                if expected not in actual:
                    raise AssertionError(f"{expected!r} missing from SQL")

            def assert_partition_bound(self, sql: str) -> None:
                self.assertIn("source_archive_date >=", sql)
                self.assertIn("source_archive_date <", sql)

        client = FakeClient()

        result = materialize_sec_company_profiles(
            client,  # type: ignore[arg-type]
            read_database="q_live",
            write_database="q_test",
            sec_database="sec_core",
            start_date=date(2025, 1, 15),
            end_date=date(2025, 1, 16),
            run_id="test",
            include_current_submissions=False,
            batch_size=10,
        )

        self.assertEqual(result.filing_rows_read, 1)
        self.assertEqual(result.filing_rows_written, 1)
        self.assertEqual(result.filing_rows_rejected, 0)
        self.assertEqual(result.filing_rows_skipped, 0)
        self.assertEqual(len(client.inserts), 1)
        self.assertIn('"issuer_business_country_code":"NL"', client.inserts[0])


if __name__ == "__main__":
    unittest.main()
