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
        self.assertEqual(result["temporal_novelty"]["decision_role"], "trace_only_v5")

    def test_v2_zero_exception_tag_routes_context_only(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga", "provider_tags": ["bzi-shorthist"],
            "title": "Short interest history",
        })
        self.assertEqual(result["route"], "context_only")
        self.assertEqual(result["content_family"], "short_interest_history")

    def test_v2_options_only_channel_routes_context_only(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga", "channels": ["Options"],
            "title": "Unusual option activity",
        })
        self.assertEqual(result["route"], "context_only")
        self.assertEqual(result["content_family"], "options_market_activity")

    def test_v2_movers_only_channel_requires_semantic_rescue(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga", "channels": ["Movers"],
            "title": "Why shares are moving",
        })
        self.assertEqual(result["route"], "semantic_rescue_required")

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

    def test_v4_audited_analyst_channel_subset_routes_context_only(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga",
            "channels": ["Analyst Ratings", "Hot", "News", "Upgrades"],
            "title": "Analyst Raises Price Target On ACME",
        })
        self.assertEqual(result["route"], "context_only")
        self.assertEqual(result["content_family"], "analyst_rating_roundup")
        self.assertEqual(
            result["metadata_evidence"]["matched_context_channel_subset"],
            ["analyst ratings", "hot"],
        )

    def test_v4_mixed_tag_overrides_audited_context_subset(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga",
            "provider_tags": ["most accurate analysts"],
            "channels": ["Analyst Ratings", "Hot", "Price Target"],
            "title": "What Wall Street Expects From ACME",
        })
        self.assertEqual(result["route"], "semantic_rescue_required")

    def test_v4_long_ideas_markets_pair_requires_semantic_rescue(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga",
            "channels": ["Long Ideas", "Markets", "News"],
            "title": "ACME Wins A Material Contract",
        })
        self.assertEqual(result["route"], "semantic_rescue_required")
        self.assertEqual(result["content_family"], "investment_idea_or_issuer_event")

    def test_v4_two_ticker_m_and_a_signature_is_event_prior_not_forced_label(self) -> None:
        result = classify_provider_context({
            "provider": "benzinga",
            "channels": ["M&A", "News"],
            "provider_tags": [],
            "tickers": ["ACME", "BUYER"],
            "title": "Transaction Update",
        })
        self.assertEqual(result["route"], "forecast_candidate")
        self.assertEqual(result["content_family"], "merger_acquisition_event_prior")
        self.assertTrue(result["metadata_evidence"]["matched_event_prior"])

    def test_v5_small_issuer_mover_rule_routes_context_only(self) -> None:
        result = classify_provider_context({
            "source_timestamp": "2026-08-20T15:00:00+00:00",
            "provider": "benzinga",
            "channels": ["Movers"],
            "tickers": ["NANO", "MICR", "SMAL"],
            "title": "Three Stocks Moving Today",
            "market_cap_tickers": [
                {"ticker": "NANO", "market_cap": 40_000_000, "market_cap_bucket": "nano_lt_50m", "market_cap_source": "provider_snapshot", "market_cap_available_at_utc": "2026-08-20T14:00:00+00:00"},
                {"ticker": "MICR", "market_cap": 100_000_000, "market_cap_bucket": "micro_50m_300m", "market_cap_source": "provider_snapshot", "market_cap_available_at_utc": "2026-08-20T14:00:00+00:00"},
                {"ticker": "SMAL", "market_cap": 500_000_000, "market_cap_bucket": "small_300m_2b", "market_cap_source": "provider_snapshot", "market_cap_available_at_utc": "2026-08-20T14:00:00+00:00"},
            ],
        })
        self.assertEqual(result["route"], "context_only")
        self.assertEqual(result["content_family"], "small_issuer_multi_band_mover")

    def test_v5_many_micro_cap_tickers_route_context_only(self) -> None:
        result = classify_provider_context({
            "source_timestamp": "2026-08-20T15:00:00+00:00",
            "provider": "benzinga",
            "tickers": [f"T{index}" for index in range(11)],
            "title": "Stocks To Watch",
            "market_cap_tickers": [
                {"ticker": f"T{index}", "market_cap": 100_000_000, "market_cap_bucket": "micro_50m_300m", "market_cap_source": "provider_snapshot", "market_cap_available_at_utc": "2026-08-20T14:00:00+00:00"}
                for index in range(11)
            ],
        })
        self.assertEqual(result["route"], "context_only")
        self.assertEqual(result["content_family"], "micro_cap_many_ticker_list")

    def test_v5_noncausal_market_cap_fails_open(self) -> None:
        result = classify_provider_context({
            "source_timestamp": "2026-08-20T15:00:00+00:00",
            "provider": "benzinga",
            "channels": ["Movers"],
            "tickers": ["NANO", "MICR", "SMAL"],
            "title": "Three Stocks Moving Today",
            "market_cap_tickers": [
                {"ticker": "NANO", "market_cap": 40_000_000, "market_cap_bucket": "nano_lt_50m", "market_cap_source": "provider_snapshot", "market_cap_available_at_utc": "2026-08-20T16:00:00+00:00"},
            ],
        })
        self.assertNotEqual(result["route"], "context_only")
        self.assertFalse(result["metadata_evidence"]["market_cap"]["causal"])
