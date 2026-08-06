from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .audit import write_audit
from .client import LocalModelError, LocalModelHttpError, build_request_payload, label_article
from .compare import compare_runs
from .config import MODEL_PROFILES, LabelingConfig
from .data import fit_sample_to_context, stratify
from .prompt import build_messages
from .schema import TRANSPORT_SCHEMA, VLLM_TRANSPORT_SCHEMA, validate_label
from .taxonomy import EVENT_FAMILY_CODES, EVENT_SUBTYPES, SENTIMENT_DIMENSIONS


def valid_label() -> dict:
    return {
        "source": {
            "origin": "editorial_original",
            "role": "primary_event",
            "issuer_relationship": "reported_issuer_event",
            "company_announcement": True,
            "confidence": 0.91,
        },
        "events": [{
            "family": "guidance",
            "subtype": "cut",
            "direction": "negative",
            "intensity": 3,
            "time": "forward",
            "modality": "confirmed",
            "confidence": 0.98,
        }],
        "sentiment": {
            "overall": "negative",
            "score": -82,
            "confidence": 0.96,
            "dimensions": [
                {
                    "name": name,
                    "label": "negative" if name == "forward_outlook" else "not_applicable",
                    "intensity": 3 if name == "forward_outlook" else 0,
                }
                for name in SENTIMENT_DIMENSIONS
            ],
        },
        "novelty": {"class": "new_event", "impact_horizon": "near_term"},
        "quality": [],
        "evidence": [{"supports": "events.0", "quote": "cuts full-year guidance"}],
    }


def contains_key(value, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(contains_key(item, target) for item in value.values())
    if isinstance(value, list):
        return any(contains_key(item, target) for item in value)
    return False


class GptOssV1Tests(unittest.TestCase):
    def test_taxonomy_has_unique_family_and_subtype_contracts(self) -> None:
        self.assertEqual(len(EVENT_FAMILY_CODES), len(set(EVENT_FAMILY_CODES)))
        self.assertTrue(all(EVENT_SUBTYPES[family] for family in EVENT_FAMILY_CODES))
        self.assertIn("reverse_split", EVENT_SUBTYPES["capital_structure"])
        self.assertIn("public_offering", EVENT_SUBTYPES["financing"])

    def test_validator_accepts_supported_verbatim_label(self) -> None:
        errors = validate_label(valid_label(), "The company cuts full-year guidance today.")
        self.assertEqual(errors, [])

    def test_vllm_transport_schema_omits_unsupported_unique_items(self) -> None:
        self.assertTrue(TRANSPORT_SCHEMA["properties"]["quality"]["uniqueItems"])
        self.assertFalse(contains_key(VLLM_TRANSPORT_SCHEMA, "uniqueItems"))
        payload = build_request_payload(
            {
                "canonical_news_id": "n1",
                "published_at_utc": "2026-07-14 13:41:00.000000000",
                "title": "Company cuts guidance",
                "rendered_text": "The company cuts full-year guidance today.",
                "tickers": ["XYZ"],
                "deterministic": {"kind": "company"},
            },
            LabelingConfig(),
        )
        transmitted = payload["response_format"]["json_schema"]["schema"]
        self.assertFalse(contains_key(transmitted, "uniqueItems"))

    def test_python_validator_retains_quality_uniqueness_contract(self) -> None:
        label = valid_label()
        label["quality"] = ["ambiguous_source", "ambiguous_source"]
        errors = validate_label(label, "The company cuts full-year guidance today.")
        self.assertIn("quality flags must be unique", errors)

    def test_permanent_http_400_is_not_retried(self) -> None:
        article = {
            "canonical_news_id": "n1",
            "published_at_utc": "2026-07-14 13:41:00.000000000",
            "title": "Company cuts guidance",
            "rendered_text": "The company cuts full-year guidance today.",
            "tickers": ["XYZ"],
            "deterministic": {"kind": "company"},
        }
        with (
            patch(
                "research.news_labeling.gpt_oss_v1.client._post_json",
                side_effect=LocalModelHttpError(400, "unsupported schema"),
            ) as post,
            patch("research.news_labeling.gpt_oss_v1.client.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(LocalModelError, "after 1 attempts"):
                label_article(article, LabelingConfig(attempts=3))
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_transient_http_503_is_retried(self) -> None:
        article = {
            "canonical_news_id": "n1",
            "published_at_utc": "2026-07-14 13:41:00.000000000",
            "title": "Company cuts guidance",
            "rendered_text": "The company cuts full-year guidance today.",
            "tickers": ["XYZ"],
            "deterministic": {"kind": "company"},
        }
        response = {
            "choices": [{"message": {"content": json.dumps(valid_label())}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        with (
            patch(
                "research.news_labeling.gpt_oss_v1.client._post_json",
                side_effect=[LocalModelHttpError(503, "temporarily unavailable"), response],
            ) as post,
            patch("research.news_labeling.gpt_oss_v1.client.time.sleep") as sleep,
        ):
            _label, usage = label_article(article, LabelingConfig(attempts=3))
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once()
        self.assertEqual(usage["attempt"], 2)

    def test_validator_rejects_invented_evidence_and_invalid_subtype(self) -> None:
        label = valid_label()
        label["events"][0]["subtype"] = "stock_split"
        label["evidence"][0]["quote"] = "not present"
        errors = validate_label(label, "The company cuts full-year guidance today.")
        self.assertTrue(any("subtype" in value for value in errors))
        self.assertTrue(any("verbatim" in value for value in errors))

    def test_company_announcement_requires_issuer_event_relationship(self) -> None:
        label = valid_label()
        label["source"]["issuer_relationship"] = "analyst_opinion"
        errors = validate_label(label, "The company cuts full-year guidance today.")
        self.assertTrue(any("company_announcement" in value for value in errors))

    def test_prompt_marks_deterministic_classification_as_evidence(self) -> None:
        article = {
            "canonical_news_id": "n1",
            "published_at_utc": "2026-07-14 13:41:00.000000000",
            "title": "Company cuts guidance",
            "rendered_text": "Company cuts full-year guidance.",
            "tickers": ["XYZ"],
            "deterministic": {"kind": "company"},
        }
        messages = build_messages(article)
        payload = json.loads(messages[1]["content"].split("INPUT:\n", 1)[1])
        self.assertEqual(payload["deterministic_evidence"]["kind"], "company")
        self.assertIn("not automatically a company announcement", messages[0]["content"])

    def test_stratifier_adds_hash_and_deterministic_evidence(self) -> None:
        rows = [{
            "canonical_news_id": "n1",
            "published_at_utc": "2026-07-14 13:41:00.000000000",
            "title": "Analyst lowers target",
            "rendered_text": "An analyst lowers the price target.",
            "tickers": ["XYZ"],
            "channels": ["Price Target"],
            "provider_tags": [],
            "author": "Desk",
            "url_domain": "example.com",
            "quality_flags": [],
            "synthesis_json": json.dumps({
                "contract_version": "news_synthesis_contract_v1",
                "envelope": {
                    "document_structure": {"value": "analyst_note"},
                    "communication_purpose": {"value": "report_event"},
                    "information_origin": {"value": "analyst"},
                    "production_method": {"value": "editorial"},
                },
                "quality_flags": [],
            }),
        }]
        sample = stratify(rows, 1)
        self.assertEqual(sample[0]["deterministic"]["information_origin"], "analyst")
        self.assertEqual(len(sample[0]["text_sha256"]), 64)

    def test_audit_writes_readable_sample(self) -> None:
        article = {
            "canonical_news_id": "n1",
            "published_at_utc": "2026-07-14 13:41:00.000000000",
            "title": "Company cuts guidance",
            "rendered_text": "The company cuts full-year guidance today.",
            "tickers": ["XYZ"],
            "deterministic": {"kind": "company"},
        }
        result = {
            "canonical_news_id": "n1",
            "status": "completed",
            "label": valid_label(),
        }
        with tempfile.TemporaryDirectory() as directory:
            report = write_audit(Path(directory), [article], [result])
            self.assertTrue(report.exists())
            self.assertIn("Overall Sentiment", report.read_text(encoding="utf-8"))
            sample_text = next((Path(directory) / "samples").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("Certified rendered article", sample_text)

    def test_context_fitting_uses_complete_prompt_budget(self) -> None:
        class FakeTokenizer:
            def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
                self.assertions = (tokenize, add_generation_prompt)
                return list(range(sum(len(item["content"]) for item in messages) // 4))

        from unittest.mock import patch

        article = {
            "canonical_news_id": "n1",
            "published_at_utc": "2026-07-14 13:41:00.000000000",
            "title": "Title",
            "rendered_text": "x" * 20_000,
            "tickers": ["XYZ"],
            "deterministic": {"kind": "company"},
            "text_sha256": "before",
        }
        config = LabelingConfig(max_model_len=6_000, max_output_tokens=1_000)
        with patch("transformers.AutoTokenizer.from_pretrained", return_value=FakeTokenizer()):
            fitted = fit_sample_to_context([article], config)[0]
        self.assertLessEqual(fitted["prompt_tokens"], 5_000)
        self.assertTrue(fitted["truncated_for_context"])
        self.assertNotEqual(fitted["text_sha256"], "before")

    def test_model_profiles_keep_model_and_tokenizer_identity_aligned(self) -> None:
        self.assertEqual(MODEL_PROFILES["20b"].model, "openai/gpt-oss-20b")
        self.assertEqual(MODEL_PROFILES["120b"].tokenizer, "openai/gpt-oss-120b")
        self.assertEqual(MODEL_PROFILES["20b"].workers, MODEL_PROFILES["120b"].workers)

    def test_default_runtime_root_is_outside_source_repository(self) -> None:
        self.assertEqual(
            LabelingConfig().runtime_root,
            Path(r"D:\TradingML\runtimes\news_labeling\gpt_oss_v1"),
        )

    def test_comparison_uses_frozen_hashes_and_reports_disagreement(self) -> None:
        sample = [{
            "canonical_news_id": "n1",
            "published_at_utc": "2026-07-14 13:41:00.000000000",
            "title": "Company cuts guidance",
            "rendered_text": "The company cuts full-year guidance today.",
            "tickers": ["XYZ"],
            "text_sha256": "frozen",
        }]
        first_label = valid_label()
        second_label = valid_label()
        second_label["sentiment"]["overall"] = "mixed"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "shared"
            first = root / "models" / "20b"
            second = root / "models" / "120b"
            for path in (shared, first, second):
                path.mkdir(parents=True)
            (shared / "sample.jsonl").write_text(
                json.dumps(sample[0]) + "\n",
                encoding="utf-8",
            )
            for model_root, model, label, seconds in (
                (first, "openai/gpt-oss-20b", first_label, 1.0),
                (second, "openai/gpt-oss-120b", second_label, 2.0),
            ):
                result = {
                    "canonical_news_id": "n1",
                    "status": "completed",
                    "model": model,
                    "text_sha256": "frozen",
                    "label": label,
                    "usage": {
                        "total_seconds": seconds,
                        "completion_tokens_per_second": 10.0 / seconds,
                    },
                }
                (model_root / "labels.jsonl").write_text(
                    json.dumps(result) + "\n",
                    encoding="utf-8",
                )
                (model_root / "manifest.json").write_text(
                    json.dumps({
                        "workers": 4,
                        "elapsed_seconds": seconds,
                        "articles_per_second": 1.0 / seconds,
                    }),
                    encoding="utf-8",
                )
            report = compare_runs(
                sample_path=shared / "sample.jsonl",
                first_root=first,
                second_root=second,
                output_root=root / "comparison",
                answer_key_path=None,
                disagreement_limit=1,
            )
            text = report.read_text(encoding="utf-8")
            self.assertIn("sentiment.overall", text)
            self.assertIn("Agreement is not accuracy", text)
            self.assertEqual(len(list((root / "comparison" / "disagreements").glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
