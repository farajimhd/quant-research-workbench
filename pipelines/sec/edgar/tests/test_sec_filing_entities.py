from __future__ import annotations

import unittest

from pipelines.sec.edgar.sec_filing_text_extract_parts import parse_filing
from pipelines.sec.edgar.sec_pipeline.entities import parse_filing_entities, primary_filing_entity


class SecFilingEntityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
