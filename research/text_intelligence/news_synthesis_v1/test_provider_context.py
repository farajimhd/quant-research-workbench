from __future__ import annotations

import unittest

from .provider_context import ROUTER_VERSION, classify_provider_context
from .engine import _envelope


class ProviderContextTests(unittest.TestCase):
    def test_engine_envelope_carries_provider_route_trace(self) -> None:
        envelope = _envelope(
            "ACME Is Scheduled To Report Earnings",
            "ACME is scheduled to report after the bell.",
            {"provider": "benzinga", "provider_tags": ["bzi-ep"]},
        )
        self.assertEqual(envelope["provider_context"]["route"], "context_only")

    def test_exact_validated_template_routes_to_context_only(self) -> None:
        result = classify_provider_context({
            "provider": "Benzinga",
            "provider_tags": ["BZI-EP"],
            "channels": ["Earnings"],
            "title": "ACME Is Scheduled To Report Earnings After The Bell",
        })
        self.assertEqual(result["router_version"], ROUTER_VERSION)
        self.assertEqual(result["route"], "context_only")
        self.assertEqual(result["content_family"], "scheduled_earnings_preview")

    def test_validated_context_template_is_not_overridden_by_generic_material_words(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga",
            "provider_tags": ["bzi-ep"],
            "title": "ACME Reports Q2 Earnings Results And Raises Guidance",
        })
        self.assertEqual(result["route"], "context_only")
        self.assertTrue(result["material_language_detected"])
        self.assertIn("material_language_not_authoritative_for_validated_template", result["reason_codes"])

    def test_material_semantics_keep_unclassified_article_in_forecast_lane(self) -> None:
        result = classify_provider_context({"title": "ACME Raises Guidance After Q2 Earnings Results"})
        self.assertEqual(result["route"], "forecast_candidate")
        self.assertEqual(result["content_family"], "material_issuer_event")

    def test_mixed_recap_never_hard_rejects(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga",
            "provider_tags": ["bzi-recaps"],
            "title": "ACME Shares Move In Tuesday's Earnings Recap",
        })
        self.assertEqual(result["route"], "semantic_rescue_required")
        self.assertEqual(result["content_family"], "earnings_recap")

    def test_mixed_template_takes_precedence_over_context_tag(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga",
            "provider_tags": ["bzi-pod", "bzi-recaps"],
            "title": "Earnings Recap",
        })
        self.assertEqual(result["route"], "semantic_rescue_required")

    def test_text_only_roundup_requires_semantic_rescue(self) -> None:
        result = classify_provider_context({"title": "Top Upgrades For Friday"})
        self.assertEqual(result["route"], "semantic_rescue_required")
        self.assertEqual(result["metadata_evidence"]["matched_context_tags"], [])

    def test_temporal_novelty_is_traced_but_not_decisive(self) -> None:
        result = classify_provider_context({
            "title": "ACME Opens A New Facility",
            "any_ticker_first_session": True,
            "min_ticker_session_ordinal": 1,
        })
        self.assertEqual(result["route"], "forecast_candidate")
        self.assertTrue(result["temporal_novelty"]["available"])
        self.assertEqual(result["temporal_novelty"]["decision_role"], "trace_only_v1")

    def test_provider_tag_without_benzinga_authority_fails_open(self) -> None:
        result = classify_provider_context({
            "provider": "another-provider",
            "provider_tags": "bzi-pod",
            "title": "ACME Price Update",
        })
        self.assertEqual(result["route"], "forecast_candidate")

    def test_string_false_novelty_is_not_coerced_true(self) -> None:
        result = classify_provider_context({
            "title": "ACME Price Update",
            "any_ticker_first_session": "false",
        })
        self.assertFalse(result["temporal_novelty"]["any_ticker_first_session"])
