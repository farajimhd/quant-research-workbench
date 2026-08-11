from __future__ import annotations

import unittest

from services.reference_gateway.table_groups import OWNED_REFERENCE_TABLES
from src.backend.application_registry import (
    FIELD_DEFINITIONS,
    QUERY_PLANS,
    application_registry_payload,
    validate_application_registry,
)


class ApplicationRegistryTests(unittest.TestCase):
    def test_registry_is_unique_and_every_reference_table_has_a_known_path(self) -> None:
        validate_application_registry()
        field_ids = [field.field_id for field in FIELD_DEFINITIONS]
        self.assertEqual(len(field_ids), len(set(field_ids)))
        sources = {source for plan in QUERY_PLANS for source in plan.source_paths}
        self.assertTrue({f"q_live.{table}" for table in OWNED_REFERENCE_TABLES}.issubset(sources))

    def test_baseline_covers_scanner_chart_strategy_and_diagnostic_enrichments(self) -> None:
        fields = {field.field_id: field for field in FIELD_DEFINITIONS}
        for field_id in (
            "identity.symbol_id",
            "tradability.is_tradable",
            "reference.market_cap",
            "reference.float_shares",
            "reference.short_interest",
            "event.split.factor",
            "coverage.state",
            "news.score",
            "sec.accession",
            "fundamental.revenue",
            "fundamental.free_cash_flow",
            "xbrl.quality_score",
            "embedding.news.vector",
        ):
            self.assertIn(field_id, fields)
            self.assertTrue(fields[field_id].query_plan_id)
            self.assertTrue(fields[field_id].available_at)
            self.assertTrue(fields[field_id].source_path)
        self.assertGreaterEqual(len(fields), 180)

    def test_deferred_producer_fields_are_registered_without_claiming_readiness(self) -> None:
        fields = {field.field_id: field for field in FIELD_DEFINITIONS}
        self.assertEqual(fields["news.score"].status, "integration_pending")
        self.assertEqual(fields["model.market_hypothesis.payload"].status, "integration_pending")
        self.assertEqual(fields["reference.borrow_fee"].historical_support, "live_observation_only")

    def test_payload_includes_versions_and_counts(self) -> None:
        payload = application_registry_payload()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["counts"]["fields"], len(FIELD_DEFINITIONS))
        self.assertEqual(payload["counts"]["query_plans"], len(QUERY_PLANS))


if __name__ == "__main__":
    unittest.main()
