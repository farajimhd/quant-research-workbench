from __future__ import annotations

import unittest

from services.reference_gateway.table_groups import OWNED_REFERENCE_TABLES
from src.backend.application_registry import (
    COMPATIBILITY_ALIASES,
    CONFIGURATION_SCHEMAS,
    CONTAINER_DEFINITIONS,
    DISCOVERY_FIELD_PRESENTATIONS,
    FIELD_DEFINITIONS,
    LINK_CONTRACTS,
    MARKET_SOURCES,
    PRODUCT_DEFINITIONS,
    QUERY_PLANS,
    application_registry_payload,
    runtime_capability_registry_payload,
    validate_application_registry,
)


class ApplicationRegistryTests(unittest.TestCase):
    def test_registry_is_unique_and_every_reference_table_has_a_known_path(self) -> None:
        validate_application_registry()
        field_ids = [field.field_id for field in FIELD_DEFINITIONS]
        self.assertEqual(len(field_ids), len(set(field_ids)))
        sources = {source for plan in QUERY_PLANS for source in plan.source_paths}
        self.assertTrue({f"q_live.{table}" for table in OWNED_REFERENCE_TABLES}.issubset(sources))
        self.assertIn("qmd.scanner.snapshot.v1", {plan.plan_id for plan in QUERY_PLANS})
        daily_plan = {
            plan.plan_id: plan for plan in QUERY_PLANS
        }["market.daily_session_bars.v1"]
        self.assertEqual(
            daily_plan.implementation,
            "src.backend.query_plans.market_daily_bars_v1:daily_session_trade_bars",
        )
        self.assertEqual(daily_plan.availability_clock, "available_at_us")
        presentation_plan = {
            plan.plan_id: plan for plan in QUERY_PLANS
        }["market.ticker_presentation.v1"]
        self.assertEqual(
            presentation_plan.implementation,
            "src.backend.query_plans.market_ticker_presentation_v1:ticker_presentation",
        )
        self.assertIn(
            "q_live.market_presentation_asset_v1",
            presentation_plan.source_paths,
        )
        self.assertTrue(
            {
                "news.company_asof.v1",
                "news.scanner_company_asof.v1",
                "sec.filing_asof.v1",
                "sec.scanner_filing_asof.v1",
                "sec.ticker_identity_batch.v1",
            }.issubset({plan.plan_id for plan in QUERY_PLANS})
        )

    def test_baseline_covers_scanner_chart_strategy_and_diagnostic_enrichments(self) -> None:
        fields = {field.field_id: field for field in FIELD_DEFINITIONS}
        for field_id in (
            "identity.symbol_id",
            "tradability.is_tradable",
            "reference.market_cap",
            "reference.float_shares",
            "reference.short_interest",
            "event.split.factor",
            "event.split.days_to_event",
            "event.ipo.days_to_event",
            "coverage.state",
            "news.score",
            "sec.accession",
            "fundamental.revenue",
            "fundamental.free_cash_flow",
            "xbrl.quality_score",
            "fundamental.quality_score",
            "signal.news_labeled",
            "signal.sec_labeled",
            "embedding.news.vector",
        ):
            self.assertIn(field_id, fields)
            self.assertTrue(fields[field_id].query_plan_id)
            self.assertTrue(fields[field_id].available_at)
            self.assertTrue(fields[field_id].source_path)
            self.assertTrue(fields[field_id].freshness_policy)
            self.assertTrue(fields[field_id].null_reasons)
        self.assertGreaterEqual(len(fields), 180)

    def test_deferred_producer_fields_are_registered_without_claiming_readiness(self) -> None:
        fields = {field.field_id: field for field in FIELD_DEFINITIONS}
        self.assertEqual(fields["news.score"].status, "integration_pending")
        self.assertEqual(fields["signal.news_labeled"].status, "integration_pending")
        self.assertEqual(fields["event.ipo.days_to_event"].status, "implemented")
        self.assertEqual(fields["event.split.days_to_event"].status, "implemented")
        self.assertEqual(fields["model.market_hypothesis.payload"].status, "integration_pending")
        self.assertEqual(fields["reference.borrow_fee"].historical_support, "live_observation_only")

    def test_payload_includes_versions_and_counts(self) -> None:
        payload = application_registry_payload()
        self.assertEqual(payload["schema_version"], 4)
        self.assertEqual(payload["counts"]["fields"], len(FIELD_DEFINITIONS))
        self.assertEqual(payload["counts"]["query_plans"], len(QUERY_PLANS))
        self.assertEqual(payload["counts"]["market_sources"], len(MARKET_SOURCES))
        self.assertEqual(payload["counts"]["products"], len(PRODUCT_DEFINITIONS))
        self.assertEqual(payload["counts"]["containers"], len(CONTAINER_DEFINITIONS))
        self.assertEqual(payload["counts"]["link_contracts"], len(LINK_CONTRACTS))
        self.assertEqual(payload["counts"]["configuration_schemas"], len(CONFIGURATION_SCHEMAS))
        self.assertEqual(payload["counts"]["compatibility_aliases"], len(COMPATIBILITY_ALIASES))
        schemas = {row["schema_id"]: row for row in payload["configuration_schemas"]}
        self.assertEqual(schemas["trading_configuration"]["version"], 18)
        self.assertEqual(
            payload["counts"]["market_discovery_fields"],
            len(DISCOVERY_FIELD_PRESENTATIONS),
        )

    def test_market_discovery_presentations_register_columns_and_filter_operators(self) -> None:
        rows = {row.source_id: row for row in DISCOVERY_FIELD_PRESENTATIONS}
        self.assertEqual(rows["market.last_price"].column_id, "last_price")
        self.assertIn("greater_or_equal", rows["market.last_price"].filter_operators)
        self.assertEqual(
            rows["signal.company_news.score"].field_id,
            "news.score",
        )
        self.assertFalse(rows["event.ipo.date"].filterable)
        self.assertTrue(rows["event.ipo.days_to_event"].filterable)

    def test_market_sources_declare_coverage_and_watermarks(self) -> None:
        sources = {source.source_id: source for source in MARKET_SOURCES}
        for source_id in ("qmd.live_memory", "qmd.recent_events", "qmd.archive_events", "qmd.daily_bars"):
            self.assertIn(source_id, sources)
            self.assertTrue(sources[source_id].coverage_path)
            self.assertTrue(sources[source_id].watermark_path)

    def test_products_containers_links_and_schemas_are_cross_referenced(self) -> None:
        validate_application_registry()
        products = {product.product_id for product in PRODUCT_DEFINITIONS}
        links = {link.link_id for link in LINK_CONTRACTS}
        self.assertIn("qmd.chart", products)
        self.assertIn("qmd.scanner", products)
        self.assertIn("workspace.symbol_context", links)
        self.assertTrue(all(set(container.product_ids).issubset(products) for container in CONTAINER_DEFINITIONS))
        self.assertTrue(all((set(container.input_links) | set(container.output_links)).issubset(links) for container in CONTAINER_DEFINITIONS))
        self.assertIn("strategy_intent", {schema.schema_id for schema in CONFIGURATION_SCHEMAS})

    def test_compatibility_aliases_are_explicitly_retirement_governed(self) -> None:
        aliases = {alias.alias_id: alias for alias in COMPATIBILITY_ALIASES}
        scanner_alias = aliases["qmd.stream.scanner_primitives"]
        self.assertEqual(scanner_alias.alias_path, "/stream/scanner-primitives")
        self.assertEqual(scanner_alias.canonical_path, "/stream/signals")
        self.assertEqual(scanner_alias.retirement_state, "deprecated")
        self.assertTrue(scanner_alias.removal_condition)

    def test_runtime_capability_registry_requires_qmd_authority_and_hash(self) -> None:
        payload = runtime_capability_registry_payload({
            "authority": "qmd_runtime_catalog",
            "provider": "qmd-gateway",
            "content_hash": "abc123",
            "capability_catalog": [{"key": "opening_range"}],
            "indicator_catalog": [{"key": "opening_range"}],
            "signal_catalog": [],
        })
        self.assertEqual(payload["authority"], "qmd_runtime_catalog")
        self.assertEqual(payload["counts"]["capabilities"], 1)
        with self.assertRaisesRegex(ValueError, "authority"):
            runtime_capability_registry_payload({"authority": "backend_fallback_snapshot"})
        with self.assertRaisesRegex(ValueError, "content hash"):
            runtime_capability_registry_payload({
                "authority": "qmd_runtime_catalog",
                "capability_catalog": [{"key": "opening_range"}],
            })


if __name__ == "__main__":
    unittest.main()
