from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipelines.sec.edgar.sec_pipeline.text_renderer import SEC_PACKED_TEXT_RENDERER_VERSION
from pipelines.sec.edgar.sec_filing_text_extract_parts import FilingParent, build_rows


class SecTextRendererIntegrationTests(unittest.TestCase):
    def test_single_character_supported_text_is_persisted_without_a_cap(self) -> None:
        parent = FilingParent(
            filing_id="filing-id",
            accession_number="0000000001-26-000002",
            accession_number_compact="000000000126000002",
            cik="0000000001",
            form_type="8-K",
            accepted_at_utc="2026-07-15 12:00:00.000",
            primary_document="filing.txt",
            primary_document_url="",
            filing_detail_url="",
        )
        document = {
            "document_type": "8-K",
            "document_name": "filing.txt",
            "payload": "X",
            "payload_bytes": 1,
            "payload_char_count": 1,
            "sequence_number": 1,
            "description": "",
        }
        payload = {"source_run_id": "test-run", "sample_text_chars": 500}
        with tempfile.TemporaryDirectory() as tmp:
            _doc, source_row, rendered_row, skip_row, _sample = build_rows(
                payload,
                Path(tmp) / "20260715.nc.tar.gz",
                "2026-07-15",
                "filing.nc",
                parent,
                document,
                "2026-07-15 12:01:00.000",
            )
        self.assertEqual(source_row["source_text"], "X")
        self.assertEqual(rendered_row["text"], "X")
        self.assertIsNone(skip_row)

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

    def test_live_and_historical_shared_row_builder_renders_primary_nport_xml(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission>
  <headerData><submissionType>NPORT-P</submissionType></headerData>
  <formData>
    <genInfo><regName>Example Registered Fund</regName></genInfo>
    <fundInfo><totAssets>123456789</totAssets><totLiabs>1234567</totLiabs></fundInfo>
  </formData>
</edgarSubmission>"""
        parent = FilingParent(
            filing_id="filing-id",
            accession_number="0002000324-26-002949",
            accession_number_compact="000200032426002949",
            cik="0001722388",
            form_type="NPORT-P",
            accepted_at_utc="2026-07-21 15:32:00.000",
            primary_document="primary_doc.xml",
            primary_document_url="",
            filing_detail_url="",
        )
        document = {
            "document_type": "NPORT-P",
            "document_name": "primary_doc.xml",
            "payload": xml,
            "payload_bytes": len(xml.encode("utf-8")),
            "payload_char_count": len(xml),
            "sequence_number": 1,
            "description": "Monthly portfolio report",
        }
        with tempfile.TemporaryDirectory() as tmp:
            document_row, source_row, rendered_row, skip_row, _sample = build_rows(
                {"source_run_id": "test-run", "sample_text_chars": 500},
                Path(tmp) / "20260721.nc.tar.gz",
                "2026-07-21",
                "filing.nc",
                parent,
                document,
                "2026-07-21 15:33:00.000",
            )

        self.assertIsNotNone(source_row)
        self.assertIsNotNone(rendered_row)
        self.assertIsNone(skip_row)
        assert source_row is not None and rendered_row is not None
        self.assertEqual(source_row["source_text"], xml)
        self.assertEqual(document_row["extraction_status"], "text_extracted")
        self.assertEqual(rendered_row["normalizer_version"], SEC_PACKED_TEXT_RENDERER_VERSION)
        self.assertIn("<edgarSubmission/headerData>", rendered_row["text"])
        self.assertIn("<headerData/submissionType>: NPORT-P", rendered_row["text"])
        self.assertIn("<genInfo/regName>: Example Registered Fund", rendered_row["text"])
        self.assertIn("<fundInfo/totAssets>: 123456789", rendered_row["text"])

    def test_live_and_historical_shared_row_builder_keeps_image_only_html_visible(self) -> None:
        source = """<html><head><title>Legal opinion</title></head><body>
        <img src="opinion-1.jpg" title="page1" width="791" height="1024">
        <img src="opinion-2.jpg" title="page2" width="791" height="1024">
        </body></html>"""
        parent = FilingParent(
            filing_id="filing-id",
            accession_number="0000000001-26-000003",
            accession_number_compact="000000000126000003",
            cik="0000000001",
            form_type="S-8",
            accepted_at_utc="2026-07-15 12:00:00.000",
            primary_document="filing.htm",
            primary_document_url="",
            filing_detail_url="",
        )
        document = {
            "document_type": "EX-5",
            "document_name": "opinion.htm",
            "payload": source,
            "payload_bytes": len(source.encode("utf-8")),
            "payload_char_count": len(source),
            "sequence_number": 2,
            "description": "Legal opinion",
        }
        with tempfile.TemporaryDirectory() as tmp:
            _doc, source_row, rendered_row, skip_row, _sample = build_rows(
                {"source_run_id": "test-run", "sample_text_chars": 500},
                Path(tmp) / "20260715.nc.tar.gz",
                "2026-07-15",
                "filing.nc",
                parent,
                document,
                "2026-07-15 12:01:00.000",
            )

        self.assertIsNotNone(source_row)
        self.assertIsNotNone(rendered_row)
        self.assertIsNone(skip_row)
        assert rendered_row is not None
        self.assertIn("Image references: 2", rendered_row["text"])
        self.assertIn("image_content_not_ocr_extracted", rendered_row["quality_flags"])


if __name__ == "__main__":
    unittest.main()
