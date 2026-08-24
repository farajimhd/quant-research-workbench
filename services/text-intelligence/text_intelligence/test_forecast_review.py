from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from .forecast_review import FUNNEL_CONTRACT, ForecastReviewRuntime, ReactionRequest, ReviewRequest


class ForecastReviewRuntimeTests(unittest.TestCase):
    def runtime(self, mode: str = "manual") -> ForecastReviewRuntime:
        config = SimpleNamespace(
            review_trigger_mode=mode,
            forecast_funnel_enabled=False,
            forecast_release_manifest=None,
            forecast_model_device="cpu",
            forecast_eligibility_threshold=0.5,
            review_prompt_path=None,
            news_hypothesis_url="http://127.0.0.1:8811",
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
    def test_synthesis_rejection_does_not_block_deepfm(self, insert: mock.Mock) -> None:
        runtime = self.runtime("manual")
        runtime.release = mock.Mock()
        runtime.release.score.return_value = {
            "forecast_eligibility": "eligible",
            "eligible_probability": 0.91,
            "threshold": 0.5,
            "release_id": "release-v1",
            "release_hash": "release-hash",
        }
        runtime._ticker_history = mock.Mock(return_value={})
        runtime._market_cap_context = mock.Mock(return_value={})
        result = runtime.process_funnel(
            {"source_id": "news-1", "source_timestamp": "2026-08-24T14:00:00Z"},
            {"production": {"engine_version": "engine-v1"}, "final": {"forecast_eligibility": "ineligible"}},
        )
        self.assertEqual(result["stage"], "deepfm_eligible")
        self.assertEqual(result["deterministic_engine_version"], "engine-v1")
        runtime.release.score.assert_called_once()
        self.assertEqual(runtime.release.score.call_args.kwargs["threshold"], 0.5)
        self.assertEqual(insert.call_args.args[2], "news_forecast_funnel_v1")
        self.assertEqual(result["contract_version"], "news_forecast_funnel_deepfm_only_serving_v2")

    def test_funnel_current_requires_exact_deepfm_release_and_threshold(self) -> None:
        runtime = self.runtime("manual")
        runtime.release = SimpleNamespace(release_id="release-v2", release_hash="release-hash-v2")
        runtime.client.execute.return_value = "1"

        self.assertTrue(runtime.funnel_current("news-1", "source-hash"))

        sql = runtime.client.execute.call_args.args[0]
        self.assertIn(FUNNEL_CONTRACT, sql)
        self.assertIn("stage IN ('deepfm_eligible','deepfm_filtered')", sql)
        self.assertIn("model_release_id='release-v2'", sql)
        self.assertIn("model_release_hash='release-hash-v2'", sql)
        self.assertIn("abs(threshold-0.5)<1e-12", sql)

    def test_funnel_is_never_current_without_loaded_deepfm_release(self) -> None:
        runtime = self.runtime("manual")

        self.assertFalse(runtime.funnel_current("news-1", "source-hash"))
        runtime.client.execute.assert_not_called()

    @mock.patch("text_intelligence.forecast_review.insert_json_each_row")
    def test_automatic_mode_queues_deepfm_candidate(self, insert: mock.Mock) -> None:
        runtime = self.runtime("automatic")
        runtime.release = mock.Mock()
        runtime.release.score.return_value = {
            "forecast_eligibility": "eligible",
            "eligible_probability": 0.91,
            "threshold": 0.5,
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

        self.assertEqual(result["stage"], "deepfm_eligible")
        self.assertEqual(runtime.queue.qsize(), 1)
        work = runtime.queue.get_nowait()
        self.assertEqual(work.trigger_mode, "automatic")
        self.assertEqual(work.request.requested_by, "automatic-funnel")
        self.assertEqual(insert.call_args_list[-1].args[2], "news_llm_issuer_review_v1")

    @mock.patch("text_intelligence.forecast_review._post_json", return_value={"status": "queued"})
    def test_manual_reaction_queues_only_reviewed_eligible_issuers(self, post: mock.Mock) -> None:
        runtime = self.runtime("manual")
        runtime.status = mock.Mock(return_value={
            "status": "complete",
            "issuer_labels_json": '{"issuers":[{"ticker":"ACME","forecast_relevance_probability":0.91},{"ticker":"NOPE","forecast_relevance_probability":0.2}]}',
        })
        runtime._load_source = mock.Mock(return_value={
            "source_timestamp": "2026-08-24T14:00:00Z",
            "title": "Acme update",
            "text": "Acme disclosed an update.",
        })
        result = runtime.request_reaction(ReactionRequest(
            canonical_news_id="news-1",
            published_at_utc="2026-08-24T14:00:00Z",
        ))
        self.assertEqual(result["tickers"], ["ACME"])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[1]["ticker"], "ACME")
        self.assertTrue(post.call_args.args[1]["session_id"].startswith("operator:"))
        self.assertEqual(runtime.metrics["reaction_queued"], 1)

    def test_reaction_requires_completed_review(self) -> None:
        runtime = self.runtime("manual")
        runtime.status = mock.Mock(return_value={"status": "queued"})
        with self.assertRaisesRegex(ValueError, "must complete"):
            runtime.request_reaction(ReactionRequest(
                canonical_news_id="news-1",
                published_at_utc="2026-08-24T14:00:00Z",
            ))

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
