from __future__ import annotations

import unittest
from datetime import UTC, datetime

from src.backend.application_registry import QUERY_PLANS
from src.backend.query_plans import sec_canvas_v1
from src.backend import sec_canvas_service


class SecCanvasQueryPlanTests(unittest.TestCase):
    def test_plan_is_registered_with_complete_sec_canvas_sources(self) -> None:
        plan = next(row for row in QUERY_PLANS if row.plan_id == sec_canvas_v1.QUERY_PLAN_ID)
        self.assertEqual(plan.version, sec_canvas_v1.QUERY_PLAN_VERSION)
        self.assertIn("q_live.sec_filing_text_v3", plan.source_paths)
        self.assertIn("q_live.sec_disclosure_taxonomy_v3", plan.source_paths)
        self.assertIn("q_live.scoped_text_labels_v5", plan.source_paths)

    def test_service_compatibility_names_resolve_to_plan_implementation(self) -> None:
        self.assertIs(sec_canvas_service.filing_list_sql, sec_canvas_v1.filing_list_sql)
        self.assertIs(sec_canvas_service.filing_detail_sql, sec_canvas_v1.filing_detail_sql)
        self.assertIs(sec_canvas_service.detail_text_page_sql, sec_canvas_v1.detail_text_page_sql)

    def test_empty_identity_sets_execute_no_source_read(self) -> None:
        cutoff = datetime(2026, 8, 11, tzinfo=UTC)
        self.assertIn("WHERE 0", sec_canvas_v1.filing_document_ids_sql([], cutoff, "q_live"))
        self.assertIn("WHERE 0", sec_canvas_v1.coverage_sql([], cutoff, "q_live"))
        self.assertIn("WHERE 0", sec_canvas_v1.identity_sql([], cutoff, "q_live"))


if __name__ == "__main__":
    unittest.main()
