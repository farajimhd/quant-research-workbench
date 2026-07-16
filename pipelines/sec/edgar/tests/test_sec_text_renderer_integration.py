from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipelines.market_sip.events.sec_packed_text_renderer import SEC_PACKED_TEXT_RENDERER_VERSION
from pipelines.sec.edgar.sec_filing_text_extract_parts import FilingParent, build_rows


class SecTextRendererIntegrationTests(unittest.TestCase):
    def test_live_and_historical_shared_row_builder_renders_non_xbrl_xml(self) -> None:
        xml = "<proxyVoteTable>" + "".join(
            f"<proxyTable><issuerName>Issuer {index}</issuerName><sharesVoted>{index + 1}</sharesVoted></proxyTable>"
            for index in range(3)
        ) + "</proxyVoteTable>"
        parent = FilingParent(
            filing_id="filing-id",
            accession_number="0000000001-26-000001",
            accession_number_compact="000000000126000001",
            cik="0000000001",
            form_type="N-PX",
            accepted_at_utc="2026-07-15 12:00:00.000",
            primary_document="filing.htm",
            primary_document_url="",
            filing_detail_url="",
        )
        document = {
            "document_type": "PROXY VOTING RECORD",
            "document_name": "ProxyVotingTable.xml",
            "payload": xml,
            "payload_bytes": len(xml.encode("utf-8")),
            "payload_char_count": len(xml),
            "sequence_number": 2,
            "description": "Proxy voting record",
        }
        payload = {
            "source_run_id": "test-run",
            "min_text_chars": 1,
            "max_text_chars": 0,
            "sample_text_chars": 500,
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "20260715.nc.tar.gz"
            _doc, source_row, rendered_row, skip_row, _sample = build_rows(
                payload,
                archive,
                "2026-07-15",
                "filing.nc",
                parent,
                document,
                "2026-07-15 12:01:00.000",
            )

        self.assertIsNotNone(source_row)
        self.assertIsNotNone(rendered_row)
        self.assertIsNone(skip_row)
        assert source_row is not None and rendered_row is not None
        self.assertEqual(source_row["source_text"], xml)
        self.assertEqual(rendered_row["normalizer_version"], SEC_PACKED_TEXT_RENDERER_VERSION)
        self.assertEqual(rendered_row["extraction_method"], SEC_PACKED_TEXT_RENDERER_VERSION)
        self.assertIn("<proxyTable> issuerName=Issuer 0; sharesVoted=1", rendered_row["text"])


if __name__ == "__main__":
    unittest.main()
