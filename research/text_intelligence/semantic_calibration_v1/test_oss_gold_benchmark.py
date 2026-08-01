from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .comparison import CollectionItem
from .oss_gold_benchmark import (
    OssBenchmarkConfig,
    OSS_PROFILES,
    VllmHttpError,
    _build_payload,
    _validate_request_capacity,
    infer_one,
    write_combined_comparison,
)
from .run_vllm_benchmark_server_wsl import build_server_command


def _item(index: int = 1, *, candidates: int = 1) -> CollectionItem:
    ticker = f"T{index:03d}"
    return CollectionItem(
        sample_id=f"N{index:04d}",
        split="fit",
        blinded={
            "source_id": f"source-{index}",
            "publication": {
                "title": "Issuer raises guidance",
                "teaser": "",
                "author": "",
                "provider_tags": [],
                "channels": [],
                "provider_tickers": [ticker],
            },
            "point_in_time_issuer_candidates": [
                {"ticker": ticker if offset == 0 else f"X{offset:03d}", "identity_evidence": []}
                for offset in range(candidates)
            ],
            "rendered_product": {"text": "The issuer raised full-year guidance."},
        },
        truth={
            "source_text_sha256": "1" * 64,
            "annotation_sha256": "2" * 64,
            "extraction_decision": "labeled",
            "content_role": "primary_event",
            "source_origin": "issuer_direct",
            "issuer_units": [
                {
                    "ticker": ticker,
                    "event_concepts": ["guidance.raise"],
                    "semantic_direction": "positive",
                    "forecast_trigger_eligible": True,
                    "reaction_evaluation_eligible": True,
                    "issuer_history_context_eligible": True,
                }
            ],
        },
    )


class OssGoldBenchmarkTests(unittest.TestCase):
    def test_capacity_gate_requires_room_for_broad_output(self) -> None:
        item = _item(candidates=77)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "gold_sample.jsonl"
            bundle.write_text("{}\n", encoding="utf-8")
            config = OssBenchmarkConfig(
                shared_root=root,
                runtime_root=root,
                profile="20b",
                max_model_len=65_536,
            )
            _validate_request_capacity(config, (item,))
            with self.assertRaisesRegex(RuntimeError, "cannot preserve"):
                _validate_request_capacity(
                    OssBenchmarkConfig(
                        shared_root=root,
                        runtime_root=root,
                        profile="20b",
                        max_model_len=12_000,
                    ),
                    (item,),
                )

    def test_inference_uses_same_structured_contract(self) -> None:
        item = _item()
        response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "extraction_decision": "labeled",
                                "content_role": "primary_event",
                                "source_origin": "issuer_direct",
                                "issuer_units": [
                                    {
                                        "canonical_instrument_id": "T001",
                                        "semantic_direction": "positive",
                                        "event_families": ["guidance"],
                                        "forecast_trigger_eligible": True,
                                        "reaction_evaluation_eligible": True,
                                        "issuer_history_context_eligible": True,
                                    }
                                ],
                            }
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "gold_sample.jsonl"
            bundle.write_text("{}\n", encoding="utf-8")
            config = OssBenchmarkConfig(root, root, "20b", attempts=1)
            with patch(
                "research.text_intelligence.semantic_calibration_v1.oss_gold_benchmark._post_json",
                return_value=response,
            ):
                result = infer_one(item, config, OSS_PROFILES["20b"])
        self.assertEqual(result["prediction"]["labels"][0]["ticker"], "T001")
        self.assertEqual(result["completion_tokens"], 50)
        self.assertEqual(len(result["bundle_sha256"]), 64)

    def test_transport_and_invalid_structured_outputs_are_retried(self) -> None:
        item = _item()
        good_response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "extraction_decision": "labeled",
                                "content_role": "primary_event",
                                "source_origin": "issuer_direct",
                                "issuer_units": [
                                    {
                                        "canonical_instrument_id": "T001",
                                        "semantic_direction": "positive",
                                        "event_families": ["guidance"],
                                        "forecast_trigger_eligible": True,
                                        "reaction_evaluation_eligible": True,
                                        "issuer_history_context_eligible": True,
                                    }
                                ],
                            }
                        )
                    },
                }
            ],
            "usage": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "gold_sample.jsonl").write_text("{}\n", encoding="utf-8")
            config = OssBenchmarkConfig(root, root, "20b", attempts=3)
            with patch(
                "research.text_intelligence.semantic_calibration_v1.oss_gold_benchmark._post_json",
                side_effect=[VllmHttpError(503, "busy"), good_response],
            ) as post:
                infer_one(item, config, OSS_PROFILES["20b"])
            self.assertEqual(post.call_count, 2)
            with patch(
                "research.text_intelligence.semantic_calibration_v1.oss_gold_benchmark._post_json",
                return_value={"choices": [{"finish_reason": "stop", "message": {"content": "{"}}]},
            ) as post:
                with self.assertRaisesRegex(RuntimeError, "inference_failed"):
                    infer_one(item, config, OSS_PROFILES["20b"])
            self.assertEqual(post.call_count, 3)

    def test_truncated_output_retries_with_a_larger_budget(self) -> None:
        item = _item()
        good_response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "extraction_decision": "labeled",
                                "content_role": "primary_event",
                                "source_origin": "issuer_direct",
                                "issuer_units": [
                                    {
                                        "canonical_instrument_id": "T001",
                                        "semantic_direction": "positive",
                                        "event_families": ["guidance"],
                                        "forecast_trigger_eligible": True,
                                        "reaction_evaluation_eligible": True,
                                        "issuer_history_context_eligible": True,
                                    }
                                ],
                            }
                        )
                    },
                }
            ],
            "usage": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "gold_sample.jsonl").write_text("{}\n", encoding="utf-8")
            config = OssBenchmarkConfig(root, root, "20b", attempts=3)
            with patch(
                "research.text_intelligence.semantic_calibration_v1.oss_gold_benchmark._post_json",
                side_effect=[
                    {"choices": [{"finish_reason": "length", "message": {"content": ""}}]},
                    good_response,
                ],
            ) as post:
                result = infer_one(item, config, OSS_PROFILES["20b"])
        self.assertEqual(post.call_count, 2)
        self.assertGreater(
            post.call_args_list[1].args[1]["max_tokens"],
            post.call_args_list[0].args[1]["max_tokens"],
        )
        self.assertEqual(result["attempt"], 2)

    def test_model_profiles_emit_only_supported_request_options(self) -> None:
        messages = [{"role": "user", "content": "label this"}]
        qwen = _build_payload(OSS_PROFILES["qwen35-a3b"], messages, 2048)
        mistral = _build_payload(
            OSS_PROFILES["mistral-small-3.1-24b"], messages, 2048
        )
        gpt_oss = _build_payload(OSS_PROFILES["20b"], messages, 2048)
        self.assertEqual(qwen["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(qwen["top_k"], 20)
        self.assertNotIn("reasoning_effort", qwen)
        self.assertEqual(mistral["temperature"], 0.15)
        self.assertNotIn("chat_template_kwargs", mistral)
        self.assertEqual(gpt_oss["reasoning_effort"], "low")

    def test_server_profiles_use_family_specific_vllm_arguments(self) -> None:
        qwen = build_server_command(profile=OSS_PROFILES["qwen35-a3b"])
        mistral = build_server_command(
            profile=OSS_PROFILES["mistral-small-3.1-24b"]
        )
        qwen_shell = qwen[-1]
        mistral_shell = mistral[-1]
        self.assertEqual(qwen[-3:-1], ["bash", "-lc"])
        self.assertIn('source "$HOME"/.venvs/vllm/bin/activate', qwen_shell)
        self.assertIn(
            "exec env VLLM_USE_FLASHINFER_SAMPLER=0 vllm serve", qwen_shell
        )
        self.assertIn("--language-model-only", qwen_shell)
        self.assertIn("--reasoning-parser", qwen_shell)
        self.assertIn("--tokenizer-mode", mistral_shell)
        self.assertIn("--config-format", mistral_shell)
        self.assertNotIn("--safetensors-load-strategy", mistral_shell)

    def test_server_command_quotes_paths_after_activating_wsl_venv(self) -> None:
        command = build_server_command(
            profile=OSS_PROFILES["20b"],
            model_path="/mnt/d/models with spaces/openai-gpt-oss-20b",
        )
        self.assertIn(
            "'/mnt/d/models with spaces/openai-gpt-oss-20b'", command[-1]
        )

    def test_combined_report_adds_local_row_without_equating_speed_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_path = root / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "benchmark_version": "news_gold_openai_benchmark_v4",
                        "models": {
                            "gpt-5.6-sol": {
                                "quality_score": 0.7,
                                "manifest": {
                                    "completed_rows": 100,
                                    "actual_batch_cost_usd": "1.0",
                                    "batch_elapsed_seconds": 120,
                                    "articles_per_minute": 50,
                                },
                                "metrics": {
                                    "semantic_direction": {"macro_f1": 0.8},
                                    "eligibility": {
                                        "forecast_trigger_eligible": {"f1": 0.75}
                                    },
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            model_root = root / "models" / "20b"
            model_root.mkdir(parents=True)
            (model_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "completed_rows": 100,
                        "quality_score": 0.6,
                        "wall_seconds": 300,
                        "articles_per_minute": 20,
                        "completion_tokens_per_second": 40,
                    }
                ),
                encoding="utf-8",
            )
            (model_root / "metrics.json").write_text(
                json.dumps(
                    {
                        "semantic_direction": {"macro_f1": 0.65},
                        "eligibility": {
                            "forecast_trigger_eligible": {"f1": 0.55}
                        },
                    }
                ),
                encoding="utf-8",
            )
            report = write_combined_comparison(root, baseline_path)
            text = report.read_text(encoding="utf-8")
        self.assertIn("gpt-oss-20b", text)
        self.assertIn("not latency-equivalent", text)
        self.assertIn("not represented as zero compute cost", text)


if __name__ == "__main__":
    unittest.main()
