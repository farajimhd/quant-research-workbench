from __future__ import annotations

import unittest

from services.reference_gateway.table_groups import OWNED_REFERENCE_TABLES
from src.backend.application_registry import (
    CONFIGURATION_BINDINGS,
    COMPATIBILITY_ALIASES,
    CONFIGURATION_SCHEMAS,
    CONTAINER_DEFINITIONS,
    DISCOVERY_FIELD_PRESENTATIONS,
    FIELD_DEFINITIONS,
    LINK_CONTRACTS,
    MARKET_SOURCES,
    PRODUCT_DEFINITIONS,
    QUERY_PLANS,
    REGISTRY_TYPES,
    application_registry_payload,
    information_registry_payload,
    runtime_capability_registry_payload,
    validate_application_registry,
    _field_presentation_label,
)


class ApplicationRegistryTests(unittest.TestCase):
    def test_presentation_labels_preserve_semantic_context_and_market_acronyms(self) -> None:
        self.assertEqual(_field_presentation_label("event.ipo.date"), "IPO Date")
        self.assertEqual(_field_presentation_label("event.split.days_to_event"), "Split Days to Event")
        self.assertEqual(_field_presentation_label("qmd.field.SIP timestamp"), "SIP Timestamp")
        self.assertEqual(_field_presentation_label("qmd.field.ht_dcperiod"), "Hilbert Transform Dominant Cycle Period")
        self.assertEqual(_field_presentation_label("qmd.field.price_vs_vwap_pct"), "Price vs VWAP %")
        self.assertEqual(
            _field_presentation_label("signal.liquidity_dislocation.score"),
            "Liquidity Dislocation Score",
        )

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
        universe_plan = {
            plan.plan_id: plan for plan in QUERY_PLANS
        }["market.tradable_universe.v1"]
        self.assertEqual(
            universe_plan.implementation,
            "src.backend.query_plans.market_tradable_universe_v1:full_tradable_universe",
        )
        identity_plan = {
            plan.plan_id: plan for plan in QUERY_PLANS
        }["reference.identity_for_symbol.v1"]
        self.assertEqual(
            identity_plan.implementation,
            "src.backend.query_plans.reference_ticker_facts_v1:identity_anchor",
        )
        facts_plan = {
            plan.plan_id: plan for plan in QUERY_PLANS
        }["reference.ticker_facts.v1"]
        self.assertEqual(
            facts_plan.implementation,
            "src.backend.query_plans.reference_ticker_facts_v1:reference_fact_queries",
        )
        self.assertIn(
            "market_sip_compact.daily_session_bars_by_symbol_time_v1",
            facts_plan.source_paths,
        )
        scanner_reference_plan = {
            plan.plan_id: plan for plan in QUERY_PLANS
        }["reference.scanner_asof.v1"]
        self.assertEqual(
            scanner_reference_plan.implementation,
            "src.backend.query_plans.reference_scanner_asof_v1:scanner_reference_projection",
        )
        self.assertIn(
            "q_live.feature_tradable_universe_v1",
            scanner_reference_plan.source_paths,
        )
        fundamentals_plan = {
            plan.plan_id: plan for plan in QUERY_PLANS
        }["sec.fundamentals_asof.v1"]
        self.assertEqual(
            fundamentals_plan.implementation,
            "src.backend.query_plans.sec_fundamentals_asof_v1:fundamental_fact_queries",
        )
        self.assertIn(
            "q_live.feature_tradable_universe_v1",
            fundamentals_plan.source_paths,
        )
        self.assertTrue(
            {
                "news.company_asof.v1",
                "news.scanner_company_asof.v1",
                "news.detail_asof.v1",
                "news.operations_intraday.v1",
                "sec.filing_asof.v1",
                "sec.operations_intraday.v1",
                "sec.scanner_filing_asof.v1",
                "sec.ticker_identity_batch.v1",
                "intelligence.published_consumer.v1",
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
        for field in fields.values():
            for input_field_id in field.input_field_ids:
                self.assertIn(
                    input_field_id,
                    fields,
                    f"{field.field_id} documents an unregistered input {input_field_id}",
                )

    def test_deferred_producer_fields_are_registered_without_claiming_readiness(self) -> None:
        fields = {field.field_id: field for field in FIELD_DEFINITIONS}
        self.assertEqual(fields["news.score"].status, "integration_pending")
        self.assertEqual(fields["signal.news_labeled"].status, "integration_pending")
        self.assertEqual(fields["event.ipo.days_to_event"].status, "implemented")
        self.assertEqual(fields["event.split.days_to_event"].status, "implemented")
        self.assertEqual(fields["event.ipo.date"].query_plan_id, "reference.scanner_asof.v1")
        self.assertEqual(fields["model.market_hypothesis.payload"].status, "integration_pending")
        self.assertEqual(fields["reference.borrow_fee"].historical_support, "live_observation_only")

    def test_payload_includes_versions_and_counts(self) -> None:
        payload = application_registry_payload()
        self.assertEqual(payload["schema_version"], 6)
        self.assertEqual(payload["fields"][0]["presentation_value_type"], "price")
        self.assertTrue(all(row["presentation_value_type"] for row in payload["market_discovery_fields"]))
        self.assertEqual(payload["counts"]["fields"], len(FIELD_DEFINITIONS))
        self.assertEqual(payload["counts"]["query_plans"], len(QUERY_PLANS))
        self.assertEqual(payload["counts"]["market_sources"], len(MARKET_SOURCES))
        self.assertEqual(payload["counts"]["products"], len(PRODUCT_DEFINITIONS))
        self.assertEqual(payload["counts"]["containers"], len(CONTAINER_DEFINITIONS))
        self.assertEqual(payload["counts"]["link_contracts"], len(LINK_CONTRACTS))
        self.assertEqual(payload["counts"]["configuration_schemas"], len(CONFIGURATION_SCHEMAS))
        self.assertEqual(payload["counts"]["compatibility_aliases"], len(COMPATIBILITY_ALIASES))
        schemas = {row["schema_id"]: row for row in payload["configuration_schemas"]}
        self.assertEqual(schemas["trading_configuration"]["version"], 30)
        registry_types = {row.kind for row in REGISTRY_TYPES}
        self.assertTrue({"trading_action", "action_policy"} <= registry_types)
        self.assertEqual(
            payload["counts"]["market_discovery_fields"],
            len(DISCOVERY_FIELD_PRESENTATIONS),
        )

    def test_market_discovery_presentations_register_columns_and_filter_operators(self) -> None:
        rows = {row.source_id: row for row in DISCOVERY_FIELD_PRESENTATIONS}
        self.assertEqual(rows["market.last_price"].column_id, "last_price")
        self.assertEqual(rows["market.previous_close"].column_id, "previous_close")
        self.assertEqual(rows["market.quality_state"].column_id, "market_quality_state")
        self.assertEqual(rows["market.liquidity_rank"].column_id, "liquidity_rank")
        self.assertEqual(rows["market.is_halted"].column_id, "market_is_halted")
        self.assertIn("is_true", rows["market.is_halted"].filter_operators)
        fields = {row.field_id: row for row in FIELD_DEFINITIONS}
        self.assertEqual(fields["market.is_halted"].status, "implemented")
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
        container_ids = {container.container_id for container in CONTAINER_DEFINITIONS}
        self.assertIn("qmd.chart", products)
        self.assertIn("qmd.scanner", products)
        self.assertIn("workspace.symbol_context", links)
        self.assertEqual(len(container_ids), 23)
        self.assertIn("strategy_activity", container_ids)
        self.assertIn("signal_stream", container_ids)
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

    def test_information_registry_unifies_qmd_application_and_configuration(self) -> None:
        qmd = {
            "definition_catalog": {
                "schema_version": 1,
                "authority": "qmd_core_definition_registry",
                "definitions": [
                    {
                        "registry_id": "qmd.derivation.momentum_core",
                        "kind": "derivation",
                        "label": "Core momentum",
                        "description": "Closed-bar momentum fields.",
                        "owner": "qmd_core",
                        "version": 1,
                        "status": "implemented",
                        "tags": ["qmd"],
                        "configurable": True,
                        "configuration_mode": "parameterized_reference",
                        "input_field_ids": ["qmd.field.close"],
                        "output_field_ids": ["qmd.field.rsi_14"],
                        "execution_scopes": ["watchlist"],
                        "parameters": [],
                        "producer_id": None,
                        "presentation": {
                            "kind_label": "Derivation",
                            "icon": "sigma",
                            "accent": "violet",
                        },
                    },
                    {
                        "registry_id": "qmd.processing_step.event_order_sequence",
                        "kind": "processing_step",
                        "label": "Event ordering",
                        "description": "Canonical sequence state.",
                        "owner": "qmd_core",
                        "version": 1,
                        "status": "implemented",
                        "tags": ["qmd"],
                        "configurable": False,
                        "configuration_mode": "locked",
                        "input_field_ids": [],
                        "output_field_ids": [],
                        "execution_scopes": ["universal_ingest"],
                        "parameters": [],
                        "producer_id": None,
                        "presentation": {
                            "kind_label": "Processing step",
                            "icon": "cable",
                            "accent": "cyan",
                        },
                    },
                    {
                        "registry_id": "qmd.field.close",
                        "kind": "field",
                        "label": "Close",
                        "description": "Closed price.",
                        "owner": "qmd_core",
                        "version": 1,
                        "status": "implemented",
                        "tags": ["qmd"],
                        "configurable": True,
                        "configuration_mode": "select_reference",
                        "input_field_ids": [],
                        "output_field_ids": [],
                        "execution_scopes": [],
                        "parameters": [],
                        "producer_id": None,
                        "presentation": {
                            "kind_label": "Field",
                            "icon": "database",
                            "accent": "blue",
                        },
                    },
                    {
                        "registry_id": "qmd.field.rsi_14",
                        "kind": "field",
                        "label": "RSI 14",
                        "description": "Momentum output.",
                        "owner": "qmd_core",
                        "version": 1,
                        "status": "implemented",
                        "tags": ["qmd"],
                        "configurable": True,
                        "configuration_mode": "select_reference",
                        "input_field_ids": [],
                        "output_field_ids": [],
                        "execution_scopes": [],
                        "parameters": [],
                        "producer_id": "qmd.derivation.momentum_core",
                        "presentation": {
                            "kind_label": "Field",
                            "icon": "database",
                            "accent": "blue",
                        },
                    },
                ],
            }
        }
        configuration = {
            "market_discovery": {
                "core_scan": {},
                "calculation_catalog": [
                        {
                            "capability_id": "instrument-identity",
                            "name": "Instrument eligibility and identity",
                            "description": "Reference-backed identity eligibility.",
                            "capability_type": "system",
                            "fields": ["identity.symbol"],
                            "configurable": False,
                            "system_required": True,
                            "implementation_status": "implemented",
                        },
                        {
                            "capability_id": "news-events",
                            "name": "News observations",
                            "description": "Point-in-time company-news events.",
                            "capability_type": "event",
                            "fields": ["signal.news_labeled"],
                            "configurable": True,
                            "system_required": False,
                            "implementation_status": "implemented",
                        },
                    ],
                "rule_sets": [{"rule_set_id": "gainers", "name": "Gainers", "description": "Positive change rule.", "atomic": True, "conditions": [{"condition_id": "change-positive", "left_source_id": "market.change_pct", "comparator": "greater_than", "value": 0}]}],
                "classifications": [
                    {"classification_id": "float.tiny", "name": "Tiny", "minimum": 0, "maximum": 500_000, "unit": "shares", "source_id": "reference.float_shares"},
                    {"classification_id": "float.broad", "name": "Broad Float", "minimum": 100_000_000, "maximum": None, "unit": "shares", "source_id": "reference.float_shares"},
                ],
                "watchlists": [{
                    "watchlist_id": "top-gainers",
                    "name": "Top gainers",
                    "columns": ["last_price"],
                    "inclusion_rule_sets": ["gainers"],
                    "calculations": ["qmd.family.momentum_core"],
                }],
            }
        }

        payload = information_registry_payload(qmd, configuration)

        definitions = {row["registry_id"]: row for row in payload["definitions"]}
        data_definitions = [
            row for row in payload["definitions"]
            if row["kind"] in {"field", "derivation", "signal"}
        ]
        presentation_labels = [row["presentation_label"] for row in data_definitions]
        self.assertTrue(all(label.strip() for label in presentation_labels))
        self.assertEqual(len(presentation_labels), len({label.casefold() for label in presentation_labels}))
        self.assertFalse({
            "date", "days to event", "score", "confidence", "direction", "clock",
            "status", "payload", "vector", "value", "state", "count", "close",
            "open", "high", "low",
        } & {label.casefold() for label in presentation_labels})
        for row in data_definitions:
            documentation = row["documentation"]
            self.assertTrue(documentation["source_location"], row["registry_id"])
            self.assertTrue(documentation["source_fields"], row["registry_id"])
            self.assertTrue(documentation["operation_steps"], row["registry_id"])
            self.assertIn(documentation["documentation_status"], {"complete", "partial"})
        self.assertEqual(payload["authority"], "application_information_registry")
        self.assertIn("qmd.derivation.momentum_core", definitions)
        self.assertEqual(
            definitions["qmd.derivation.momentum_core"]["configuration_binding_id"],
            "market_discovery.core_scan",
        )
        self.assertEqual(
            definitions["qmd.derivation.momentum_core"]["relationships"]["output_field_ids"],
            ["qmd.field.rsi_14"],
        )
        self.assertIn("reference.market_cap", definitions)
        self.assertEqual(definitions["event.ipo.date"]["presentation_label"], "IPO Date")
        self.assertEqual(
            definitions["event.split.days_to_event"]["presentation_label"],
            "Split Days to Event",
        )
        self.assertEqual(definitions["qmd.field.rsi_14"]["presentation_label"], "RSI 14")
        float_documentation = definitions["classification.float"]["documentation"]
        self.assertEqual(float_documentation["source_location"], "q_live.market_security_float_v1")
        self.assertEqual(float_documentation["source_fields"], ["float_shares"])
        self.assertEqual(len(float_documentation["classification_bands"]), 2)
        change_documentation = definitions["market.change_pct"]["documentation"]
        self.assertIn("last price / previous close", change_documentation["calculation_summary"])
        self.assertEqual(
            change_documentation["input_field_ids"],
            ["market.last_price", "market.previous_close"],
        )
        self.assertEqual(change_documentation["unit"], "percent")
        self.assertEqual(change_documentation["entity_grain"], "security_at_market_clock")
        self.assertIn("QMD last price", change_documentation["source_summary"])
        self.assertTrue(definitions["qmd.derivation.momentum_core"]["documentation"]["source_summary"])
        self.assertEqual(
            definitions["qmd.derivation.momentum_core"]["documentation"]["input_field_ids"],
            ["qmd.field.close"],
        )
        self.assertEqual(
            definitions["qmd.field.rsi_14"]["documentation"]["calculation_summary"],
            "Closed-bar momentum fields.",
        )
        self.assertIn("column.last_price", definitions)
        self.assertIn("rule_set.gainers", definitions)
        self.assertFalse(definitions["rule_set.gainers"]["configurable"])
        self.assertEqual(definitions["rule_set.gainers"]["configuration_mode"], "locked")
        self.assertEqual(definitions["signal.company_news"]["kind"], "signal")
        self.assertEqual(
            definitions["signal.company_news"]["relationships"]["producer_ids"],
            ["news_gateway", "text_intelligence"],
        )
        self.assertIn("condition.gainers.change-positive", definitions)
        self.assertEqual(definitions["instrument-identity"]["kind"], "processing_step")
        self.assertFalse(definitions["instrument-identity"]["configurable"])
        self.assertEqual(definitions["news-events"]["kind"], "product")
        self.assertEqual(definitions["news-events"]["configuration_binding_id"], "market_discovery.products")
        self.assertEqual(
            definitions["rule_set.gainers"]["relationships"]["condition_ids"],
            ["condition.gainers.change-positive"],
        )
        self.assertIn("watchlist.top-gainers", definitions)
        self.assertEqual(
            {row["alias_id"]: row["registry_id"] for row in payload["aliases"]}["qmd.family.momentum_core"],
            "qmd.derivation.momentum_core",
        )
        aliases = {row["alias_id"]: row["registry_id"] for row in payload["aliases"]}
        self.assertEqual(aliases["qmd.primitive.event-order-sequence"], "qmd.processing_step.event_order_sequence")
        self.assertEqual(aliases["qmd.primitive.event_order_sequence"], "qmd.processing_step.event_order_sequence")
        self.assertTrue(payload["content_hash"])

    def test_every_configuration_binding_targets_a_registered_type(self) -> None:
        kinds = [row.kind for row in REGISTRY_TYPES]
        binding_ids = [row.binding_id for row in CONFIGURATION_BINDINGS]
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertEqual(len(binding_ids), len(set(binding_ids)))
        self.assertTrue(all(row.kind in kinds for row in CONFIGURATION_BINDINGS))

    def test_information_registry_fails_closed_without_qmd_definition_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "QMD definition registry authority"):
            information_registry_payload({}, {})


if __name__ == "__main__":
    unittest.main()
