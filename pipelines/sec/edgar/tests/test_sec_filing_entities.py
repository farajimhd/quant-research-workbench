from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest import mock

from pipelines.sec.edgar import sec_integrity_audit as integrity_audit
from pipelines.sec.edgar.sec_filing_text_extract_parts import parse_filing
from pipelines.sec.edgar.sec_pipeline.archive_accession import build_archive_accession_row
from pipelines.sec.edgar.sec_pipeline.entities import parse_filing_entities, primary_filing_entity
from pipelines.sec.edgar.sec_pipeline.revision import SourceRevision


class SecFilingEntityTests(unittest.TestCase):
    def test_integrity_allows_metadata_only_filings_without_sgml_entities(self) -> None:
        entity_summary = {
            "rows": "8739623",
            "accessions": "5921797",
            "entity_ciks": "448691",
            "missing_roles": "0",
            "missing_ciks": "0",
            "accession_prefix_primary_cik_mismatches": "4649456",
        }
        with (
            mock.patch.object(integrity_audit, "query_one", return_value=entity_summary),
            mock.patch.object(integrity_audit, "scalar_int", side_effect=[0, 124537, 0]) as scalar,
        ):
            result = integrity_audit.check_filing_entities(object(), "q_live")[0]

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["details"]["filings_without_entities"], 124537)
        self.assertEqual(result["details"]["archive_backed_filings_without_entities"], 0)
        self.assertEqual(result["details"]["metadata_only_filings_without_entities"], 124537)
        self.assertIn("sec_filing_archive_accession_current_v3", scalar.call_args_list[2].args[1])

    def test_integrity_fails_when_archive_backed_filing_has_no_entities(self) -> None:
        entity_summary = {
            "rows": "10",
            "accessions": "5",
            "entity_ciks": "3",
            "missing_roles": "0",
            "missing_ciks": "0",
            "accession_prefix_primary_cik_mismatches": "2",
        }
        with (
            mock.patch.object(integrity_audit, "query_one", return_value=entity_summary),
            mock.patch.object(integrity_audit, "scalar_int", side_effect=[0, 2, 1]),
        ):
            result = integrity_audit.check_filing_entities(object(), "q_live")[0]

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["details"]["archive_backed_filings_without_entities"], 1)

    def test_parses_all_roles_and_never_uses_accession_prefix_as_cik(self) -> None:
        raw = b"""<SEC-DOCUMENT>0002143285-26-000002.txt
ACCESSION NUMBER: 0002143285-26-000002
CONFORMED SUBMISSION TYPE: 8-K
<SUBJECT-COMPANY>
 <COMPANY-DATA><CONFORMED-NAME>SUBJECT CO<CIK>0000766421
<FILED-BY>
 <COMPANY-DATA><CONFORMED-NAME>AGENT CO<CIK>0002143285
<DOCUMENT><TYPE>8-K<SEQUENCE>1<FILENAME>main.htm<TEXT>body</TEXT></DOCUMENT>
"""
        filing = parse_filing(raw, "0002143285-26-000002.nc")
        self.assertEqual(filing["cik"], "0000766421")
        self.assertEqual(
            [(entity.role, entity.cik) for entity in filing["entities"]],
            [("subject_company", "0000766421"), ("filed_by", "0002143285")],
        )

    def test_issuer_has_primary_priority_and_duplicate_role_cik_is_collapsed(self) -> None:
        header = """<FILER><COMPANY-DATA><CONFORMED-NAME>FILER<CIK>1
<ISSUER><COMPANY-DATA><CONFORMED-NAME>ISSUER<CIK>2
<ISSUER><COMPANY-DATA><CONFORMED-NAME>ISSUER AGAIN<CIK>2
<REPORTING-OWNER><OWNER-DATA><CONFORMED-NAME>OWNER<CIK>3
"""
        entities = parse_filing_entities(header)
        self.assertEqual(primary_filing_entity(entities).cik, "0000000002")
        self.assertEqual(sum(entity.cik == "0000000002" for entity in entities), 1)

    def test_legacy_header_preserves_source_cik_without_accession_inference(self) -> None:
        entities = parse_filing_entities("COMPANY CONFORMED NAME: LEGACY CO\nCENTRAL INDEX KEY: 1234\n")
        self.assertEqual([(item.role, item.cik) for item in entities], [("submission_entity", "0000001234")])

    def test_archive_inventory_records_embedded_ciks_and_publication_evidence(self) -> None:
        filing = parse_filing(
            b"""ACCESSION NUMBER: 0002143285-26-000002
CONFORMED SUBMISSION TYPE: CORRESP
FILED AS OF DATE: 20260713
ACCEPTANCE-DATETIME: 20260713160450
PUBLIC DOCUMENT COUNT: 2
<PRIVATE-TO-PUBLIC>
<SUBJECT-COMPANY><COMPANY-DATA><CIK>0000766421
<FILED-BY><COMPANY-DATA><CIK>0002143285
<DOCUMENT><TYPE>CORRESP<SEQUENCE>1<FILENAME>letter.htm<TEXT>letter</TEXT></DOCUMENT>
<DOCUMENT><TYPE>GRAPHIC<SEQUENCE>2<FILENAME>image.jpg<TEXT>binary</TEXT></DOCUMENT>
""",
            "0002143285-26-000002.nc",
        )
        revision = SourceRevision(
            source_version_key="version", source_revision_at=datetime(2026, 7, 13, tzinfo=UTC),
            source_revision_rank=1, source_revision_kind="daily_archive", pac_event_id="",
        )

        row = build_archive_accession_row(
            filing=filing, entities=filing["entities"], primary_cik=filing["cik"],
            source_archive_date="2026-07-13", source_archive_member="member.nc",
            source_archive_path="archive.tar.gz", source_header_sha256="header",
            source_content_sha256="content", document_count=len(filing["documents"]),
            header_text=filing["header_text"], revision=revision, source_run_id="run",
            inserted_at="2026-07-14T00:00:00.000Z", source_kind="daily_archive",
        )

        self.assertEqual(row["primary_cik"], "0000766421")
        self.assertEqual(row["entity_ciks"], ["0000766421", "0002143285"])
        self.assertEqual(row["document_count"], 2)
        self.assertEqual(row["public_document_count"], 2)
        self.assertEqual(row["private_to_public"], 1)


if __name__ == "__main__":
    unittest.main()
