from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from src.backend.sec_canvas_service import filing_list_sql


class SecCanvasFilterTests(unittest.TestCase):
    def test_all_displayed_intelligence_fields_filter_before_top_n(self) -> None:
        cutoff = datetime(2026, 7, 24, 16, 0, tzinfo=UTC)
        sql = filing_list_sql(
            cutoff=cutoff,
            database="q_live",
            label="offering",
            limit=101,
            lookback_hours=168,
            search="Morgan",
            ticker="MS",
            before=None,
            before_accession="",
            content="readable",
            window_start=cutoff - timedelta(days=7),
            impact="4",
            label_state="classified",
            role="primary_event",
            origin="regulatory_primary",
            direction="positive",
            security_scope="reporting issuer securities",
            ticker_label="MS",
            eligibility_filters={
                "forecast_eligible": "eligible",
                "reaction_eligible": "ineligible",
                "history_eligible": "eligible",
                "prior_context_eligible": "eligible",
                "followup_eligible": "ineligible",
            },
        )

        self.assertIn("f.accession_number IN (", sql)
        self.assertIn("INNER JOIN `q_live`.scoped_text_labels_v5", sql)
        self.assertIn("PREWHERE source_archive_date >=", sql)
        self.assertIn("countIf(l.content_role = 'primary_event') > 0", sql)
        self.assertIn("countIf(l.source_origin = 'regulatory_primary') > 0", sql)
        self.assertIn("max(l.forecast_trigger_eligible) = 1", sql)
        self.assertIn("max(l.reaction_evaluation_eligible) = 0", sql)
        self.assertIn("JSONExtractBool(l.classification_json, 'prior_primary_context_eligible')", sql)
        self.assertIn("JSONExtractBool(l.classification_json, 'episode_followup_eligible')", sql)
        self.assertIn("filing_label = 'offering'", sql)
        self.assertIn("impact_score = 4", sql)
        self.assertIn("lowerUTF8(affected_security_scope) = 'reporting issuer securities'", sql)
        self.assertLess(sql.index("scoped_text_labels_v5"), sql.rindex("LIMIT 101"))

    def test_pending_means_no_v5_document_label(self) -> None:
        cutoff = datetime(2026, 7, 24, 16, 0, tzinfo=UTC)
        sql = filing_list_sql(
            cutoff=cutoff,
            database="q_live",
            label="",
            limit=26,
            lookback_hours=24,
            search="",
            ticker="",
            before=None,
            before_accession="",
            label_state="pending",
        )

        self.assertIn("f.accession_number NOT IN (", sql)
        self.assertIn("l.labeling_version = 'scoped_text_labeling_v5'", sql)


if __name__ == "__main__":
    unittest.main()
