from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from research.news_labeling.gpt_oss_v1.data import read_jsonl
from research.news_labeling.gpt_oss_v1.taxonomy import SENTIMENT_DIMENSIONS

from .compare import write_multi_model_comparison
from .config import DEFAULT_PROFILES, MODEL_REGISTRY, BatchConfig
from .pipeline import (
    _batch_request,
    _materialize_model_result,
    build_plan,
)


def article(identifier: str = "n1") -> dict:
    rendered = "The company cuts full-year guidance today."
    return {
        "canonical_news_id": identifier,
        "published_at_utc": "2026-07-14T13:41:00Z",
        "title": "Company cuts guidance",
        "rendered_text": rendered,
        "text_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "tickers": ["XYZ"],
        "deterministic": {"kind": "company"},
    }


def valid_label() -> dict:
    return {
        "source": {
            "origin": "editorial_original",
            "role": "primary_event",
            "issuer_relationship": "reported_issuer_event",
            "company_announcement": True,
            "confidence": 0.95,
        },
        "events": [
            {
                "family": "guidance",
                "subtype": "cut",
                "direction": "negative",
                "intensity": 3,
                "time": "forward",
                "modality": "confirmed",
                "confidence": 0.98,
            }
        ],
        "sentiment": {
            "overall": "negative",
            "score": -80,
            "confidence": 0.96,
            "dimensions": [
                {
                    "name": name,
                    "label": (
                        "negative" if name == "forward_outlook" else "not_applicable"
                    ),
                    "intensity": 3 if name == "forward_outlook" else 0,
                }
                for name in SENTIMENT_DIMENSIONS
            ],
        },
        "novelty": {"class": "new_event", "impact_horizon": "near_term"},
        "quality": [],
        "evidence": [
            {"supports": "events.0", "quote": "cuts full-year guidance"}
        ],
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_answer_key(path: Path) -> Path:
    write_jsonl(path, [{"canonical_news_id": "n1", "label": valid_label()}])
    return path


class OpenAIBatchV1Tests(unittest.TestCase):
    def test_registry_is_exact_approved_matrix(self) -> None:
        self.assertEqual(
            DEFAULT_PROFILES,
            (
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
                "gpt-4.1-mini",
                "gpt-4.1-nano",
            ),
        )
        self.assertEqual(set(DEFAULT_PROFILES), set(MODEL_REGISTRY))

    def test_request_contract_differs_only_where_model_requires_it(self) -> None:
        modern = _batch_request(article(), MODEL_REGISTRY["gpt-5.6-sol"], 1_536)
        legacy = _batch_request(article(), MODEL_REGISTRY["gpt-4.1-mini"], 1_536)
        self.assertEqual(modern["url"], "/v1/chat/completions")
        self.assertEqual(modern["body"]["reasoning_effort"], "none")
        self.assertNotIn("reasoning_effort", legacy["body"])
        self.assertTrue(
            modern["body"]["response_format"]["json_schema"]["strict"]
        )
        self.assertEqual(modern["body"]["temperature"], 0)

    def test_real_request_plan_is_bounded_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample_path = root / "sample.jsonl"
            write_jsonl(sample_path, [article()])
            config = BatchConfig(runtime_root=root / "run", sample_path=sample_path)
            plan = build_plan(config, [article()])
            self.assertEqual(plan["sample_rows"], 1)
            self.assertEqual(len(plan["models"]), 7)
            self.assertLess(
                Decimal(plan["protected_cost_usd"]), config.hard_max_cost_usd
            )
            for row in plan["models"]:
                self.assertTrue(Path(row["input_path"]).exists())
                self.assertEqual(row["request_rows"], 1)

    def test_terminal_output_is_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample = [article()]
            sample_path = root / "sample.jsonl"
            write_jsonl(sample_path, sample)
            config = BatchConfig(
                runtime_root=root / "run",
                sample_path=sample_path,
                profiles=("gpt-5.6-sol",),
            )
            model_root = config.model_root("gpt-5.6-sol")
            write_jsonl(
                model_root / "raw_output.jsonl",
                [
                    {
                        "custom_id": "n1",
                        "response": {
                            "status_code": 200,
                            "body": {
                                "choices": [
                                    {
                                        "message": {
                                            "content": json.dumps(valid_label())
                                        }
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 100,
                                    "completion_tokens": 50,
                                    "total_tokens": 150,
                                },
                            },
                        },
                    }
                ],
            )
            _materialize_model_result(
                config,
                MODEL_REGISTRY["gpt-5.6-sol"],
                sample,
                {"batch_id": "batch_1", "status": "completed"},
            )
            labels = read_jsonl(model_root / "labels.jsonl")
            self.assertEqual(len(labels), 1)
            self.assertEqual(labels[0]["label"]["sentiment"]["overall"], "negative")
            manifest = json.loads(
                (model_root / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["completed_rows"], 1)
            self.assertEqual(manifest["failed_rows"], 0)
            self.assertTrue((model_root / "AUDIT.md").exists())

    def test_comparison_requires_same_frozen_identity_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample_path = root / "sample.jsonl"
            sample = [article()]
            write_jsonl(sample_path, sample)
            model_roots: list[Path] = []
            for name in ("model-a", "model-b"):
                model_root = root / name
                write_jsonl(
                    model_root / "labels.jsonl",
                    [
                        {
                            "canonical_news_id": "n1",
                            "text_sha256": sample[0]["text_sha256"],
                            "status": "completed",
                            "label": valid_label(),
                        }
                    ],
                )
                (model_root / "manifest.json").write_text(
                    json.dumps(
                        {
                            "failed_rows": 0,
                            "prompt_tokens": 100,
                            "completion_tokens": 50,
                            "conservative_actual_cost_usd": "0.001",
                        }
                    ),
                    encoding="utf-8",
                )
                model_roots.append(model_root)
            report = write_multi_model_comparison(
                sample_path=sample_path,
                model_roots=model_roots,
                output_root=root / "comparison",
                disagreement_limit=5,
                answer_key_path=write_answer_key(root / "answer_key.jsonl"),
            )
            self.assertTrue(report.exists())
            payload = json.loads(
                (root / "comparison" / "comparison.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["all_model_common_rows"], 1)
            pair = payload["pairwise"]["model-a__model-b"]
            self.assertEqual(pair["event_exact_match"], 1.0)
            self.assertEqual(payload["accuracy"]["model-a"]["event_f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
