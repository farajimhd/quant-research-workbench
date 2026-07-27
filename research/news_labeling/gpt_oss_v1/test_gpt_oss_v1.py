from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .audit import write_audit
from .data import fit_sample_to_context, stratify
from .prompt import build_messages
from .schema import validate_label
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


class GptOssV1Tests(unittest.TestCase):
    def test_taxonomy_has_unique_family_and_subtype_contracts(self) -> None:
        self.assertEqual(len(EVENT_FAMILY_CODES), len(set(EVENT_FAMILY_CODES)))
        self.assertTrue(all(EVENT_SUBTYPES[family] for family in EVENT_FAMILY_CODES))
        self.assertIn("reverse_split", EVENT_SUBTYPES["capital_structure"])
        self.assertIn("public_offering", EVENT_SUBTYPES["financing"])

    def test_validator_accepts_supported_verbatim_label(self) -> None:
        errors = validate_label(valid_label(), "The company cuts full-year guidance today.")
        self.assertEqual(errors, [])

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
        }]
        sample = stratify(rows, 1)
        self.assertEqual(sample[0]["deterministic"]["kind"], "analyst")
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

        from .config import LabelingConfig
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


if __name__ == "__main__":
    unittest.main()
