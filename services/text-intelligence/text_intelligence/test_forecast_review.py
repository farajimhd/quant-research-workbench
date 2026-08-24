from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from .forecast_review import ForecastReviewRuntime, ReviewRequest


class ForecastReviewRuntimeTests(unittest.TestCase):
    def runtime(self, mode: str = "manual") -> ForecastReviewRuntime:
        config = SimpleNamespace(
            review_trigger_mode=mode,
            forecast_funnel_enabled=False,
            forecast_release_manifest=None,
            forecast_model_device="cpu",
            review_prompt_path=None,
        )
        client = mock.Mock()
        client.execute.return_value = "0"
        return ForecastReviewRuntime(config, client, "q_live")

    def test_manual_mode_rejects_automatic_enqueue(self) -> None:
        runtime = self.runtime("manual")
        request = ReviewRequest(
            canonical_news_id="news-1",
            published_at_utc="2026-08-24T14:00:00Z",
            force=True,
        )
        with self.assertRaisesRegex(PermissionError, "automatic LLM review is disabled"):
            runtime.enqueue(request, trigger_mode="automatic")

    @mock.patch("text_intelligence.forecast_review.insert_json_each_row")
    def test_manual_enqueue_remains_available(self, insert: mock.Mock) -> None:
        runtime = self.runtime("manual")
        result = runtime.enqueue(
            ReviewRequest(
                canonical_news_id="news-1",
                published_at_utc="2026-08-24T14:00:00Z",
                force=True,
            ),
            trigger_mode="manual",
        )
        self.assertEqual(result["status"], "queued")
        self.assertEqual(runtime.queue.qsize(), 1)
        self.assertEqual(insert.call_args.args[2], "news_llm_issuer_review_v1")

    @mock.patch("text_intelligence.forecast_review.insert_json_each_row")
    def test_deterministic_rejection_never_invokes_deepfm(self, insert: mock.Mock) -> None:
        runtime = self.runtime("manual")
        result = runtime.process_funnel(
            {"source_id": "news-1", "source_timestamp": "2026-08-24T14:00:00Z"},
            {"production": {"engine_version": "engine-v1"}, "final": {"forecast_eligibility": "ineligible"}},
        )
        self.assertEqual(result["stage"], "deterministic_rejected")
        self.assertEqual(result["deterministic_engine_version"], "engine-v1")
        self.assertEqual(insert.call_args.args[2], "news_forecast_funnel_v1")

    @mock.patch("text_intelligence.forecast_review.insert_json_each_row")
    def test_automatic_mode_queues_deepfm_candidate(self, insert: mock.Mock) -> None:
        runtime = self.runtime("automatic")
        runtime.release = mock.Mock()
        runtime.release.score.return_value = {
            "forecast_eligibility": "eligible",
            "eligible_probability": 0.91,
            "threshold": 0.38,
            "release_id": "release-v1",
            "release_hash": "release-hash",
        }
        runtime._ticker_history = mock.Mock(return_value={})
        runtime._market_cap_context = mock.Mock(return_value={})

        result = runtime.process_funnel(
            {
                "source_id": "news-automatic",
                "source_timestamp": "2026-08-24T14:00:00Z",
                "rendered_text_hash": "source-hash",
            },
            {
                "production": {"engine_version": "engine-v1"},
                "final": {"forecast_eligibility": "eligible"},
            },
        )

        self.assertEqual(result["stage"], "deepfm_candidate")
        self.assertEqual(runtime.queue.qsize(), 1)
        work = runtime.queue.get_nowait()
        self.assertEqual(work.trigger_mode, "automatic")
        self.assertEqual(work.request.requested_by, "automatic-funnel")
        self.assertEqual(insert.call_args_list[-1].args[2], "news_llm_issuer_review_v1")

    def test_table_contract_includes_append_only_llm_history(self) -> None:
        runtime = self.runtime("manual")
        runtime._ensure_tables()
        ddl = "\n".join(call.args[0] for call in runtime.client.execute.call_args_list)
        self.assertIn("news_llm_issuer_review_history_v1", ddl)
        self.assertIn("ENGINE=MergeTree", ddl)

    @mock.patch("src.backend.ticker_facts_service.ticker_facts_payload", side_effect=ValueError("invalid ticker"))
    def test_invalid_source_ticker_is_explicit_missing_market_cap(self, _facts: mock.Mock) -> None:
        context = self.runtime("manual")._market_cap_context({
            "source_timestamp": "2026-08-24T14:00:00Z",
            "tickers": ["NOT/A/TICKER"],
        })
        self.assertEqual(context["market_cap_coverage"], "missing")
        self.assertEqual(context["market_cap_tickers"][0]["market_cap_source"], "invalid_ticker")


if __name__ == "__main__":
    unittest.main()
