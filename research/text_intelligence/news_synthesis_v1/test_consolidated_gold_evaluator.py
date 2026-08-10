from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from .consolidated_gold_evaluator import (
    INFERENCE_VERSION,
    _canonical_source_from_catalog_row,
    _file_summary,
    _load_partition,
    _score_article,
    _sentiment_metrics,
    _validate_source_article,
    compare_audits,
    certify_source_catalog,
    create_frozen_split,
    evaluate_inference,
    validate_audit,
)
from .contracts import sha256_json
from .direct_trading_sentiment_audit import article_source
from .engine import ENGINE_VERSION
from .gold_label_consolidation import _write_dataset


class ConsolidatedGoldEvaluatorTests(unittest.TestCase):
    def test_split_is_deterministic_disjoint_and_preserves_final_only(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            consolidated = runtime / "consolidated"
            consolidated.mkdir()
            _write_dataset(
                consolidated,
                [
                    (_authority("development", "model_development_allowed"), iter([
                        _gold(f"source-{index}", "development", "model_development_allowed")
                        for index in range(5)
                    ])),
                    (_authority("sealed", "final_evaluation_only"), iter([
                        _gold("source-final", "sealed", "final_evaluation_only")
                    ])),
                ],
            )

            first = create_frozen_split(
                consolidated, runtime / "split-1", runtime_root=runtime, seed="fixed"
            )
            second = create_frozen_split(
                consolidated, runtime / "split-2", runtime_root=runtime, seed="fixed"
            )

            self.assertEqual(
                first["partition_source_ids_sha256"],
                second["partition_source_ids_sha256"],
            )
            partitions = {
                name: set(_ids(runtime / "split-1" / f"{name}_gold.jsonl"))
                for name in ("audit", "development_test", "final_test")
            }
            self.assertEqual(partitions["final_test"], {"source-final"})
            self.assertTrue(partitions["audit"])
            self.assertTrue(partitions["development_test"])
            self.assertFalse(partitions["audit"] & partitions["development_test"])
            self.assertEqual(set().union(*partitions.values()), {
                *(f"source-{index}" for index in range(5)),
                "source-final",
            })
            with (runtime / "split-1" / "audit_gold.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(json.dumps(_gold(
                    "tampered-final", "sealed", "final_evaluation_only"
                )) + "\n")
            with self.assertRaisesRegex(RuntimeError, "partition integrity mismatch"):
                _load_partition(runtime / "split-1", "audit", allow_test=False)

    def test_test_partitions_require_explicit_release(self) -> None:
        with TemporaryDirectory() as directory:
            split = Path(directory)
            (split / "final_test_gold.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "explicit release flag"):
                _load_partition(split, "final_test", allow_test=False)

    def test_source_text_hash_mismatch_fails_closed(self) -> None:
        gold = {
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T00:00:00Z",
            "source_hashes": {
                "body_sha256": hashlib.sha256(b"certified body").hexdigest()
            },
        }
        source = {
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T00:00:00Z",
            "publication": {"title": "Title"},
            "rendered_product": {"text": "changed body"},
        }
        with self.assertRaisesRegex(RuntimeError, "Source text hash mismatch"):
            _validate_source_article(source, gold)

    def test_title_only_source_hash_uses_engine_text_fallback(self) -> None:
        title = "Title-only source"
        gold = {
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T00:00:00Z",
            "source_hashes": {
                "source_text_sha256": hashlib.sha256(title.encode()).hexdigest()
            },
        }
        source = {
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T00:00:00Z",
            "publication": {"title": title},
            "rendered_product": {"text": ""},
        }
        _validate_source_article(source, gold)

    def test_source_catalog_certification_binds_explicit_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            artifact = runtime / "source.json"
            source_record = {
                "source_id": "source-1",
                "source_timestamp": "2026-01-01T00:00:00Z",
                "publication": {"title": "Title"},
                "rendered_product": {"text": "Body"},
            }
            artifact.write_text(json.dumps(source_record) + "\n", encoding="utf-8")
            catalog = runtime / "catalog.jsonl"
            catalog.write_text(json.dumps({
                "source_id": "source-1",
                "source_schema": "canonical_benchmark_article_v1",
                "source_record": source_record,
                "source_lineage": {
                    "runtime_relative_path": "source.json",
                    "artifact_record_sha256": sha256_json(source_record),
                },
            }) + "\n", encoding="utf-8")
            manifest = certify_source_catalog(
                catalog,
                [artifact],
                runtime / "catalog.manifest.json",
                runtime_root=runtime,
            )
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["source_artifacts"][0]["sha256"], _sha256_path(artifact))

            unrelated = runtime / "unrelated.json"
            unrelated.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "undeclared artifact"):
                certify_source_catalog(
                    catalog,
                    [unrelated],
                    runtime / "unrelated.manifest.json",
                    runtime_root=runtime,
                )

    def test_forecast_source_adapter_preserves_engine_domain_input(self) -> None:
        record = {
            "review_id": "review-1",
            "title": "Title",
            "full_rendered_body": "Body",
            "published_at_utc": "2026-01-01T00:00:00Z",
            "provider_domain": "news.example.com",
            "provider_tickers": ["AAA"],
        }
        catalog = {
            "source_id": "source-1",
            "source_schema": "forecast_blind_full_source_v1",
            "source_record": record,
            "source_lineage": {
                "runtime_relative_path": "source.jsonl",
                "artifact_record_sha256": sha256_json(record),
            },
        }
        adapted = _canonical_source_from_catalog_row(
            catalog,
            {"authority_article_id": "review-1"},
        )
        engine_input = article_source(adapted)
        self.assertEqual(engine_input["url_domain"], "news.example.com")
        self.assertEqual(engine_input["tickers"], ["AAA"])

    def test_scoring_excludes_unknown_sentiment_and_records_missing_identity(self) -> None:
        gold = _gold("source-1", "development", "model_development_allowed")
        gold["issuer_units"] = [
            {**gold["issuer_units"][0], "sentiment": "unknown",
             "normalization_status": "missing_eligible_sentiment"},
            {**gold["issuer_units"][0], "unit_id": "source-1::BBB", "ticker": "BBB",
             "sentiment": "positive"},
            {**gold["issuer_units"][0], "unit_id": "source-1::issuer", "ticker": "",
             "forecast_eligibility": "ineligible", "sentiment": "not_applicable"},
        ]
        prediction = {"entities": [], "issuer_views": [], "eligibility": []}

        _article, units, _extras = _score_article(gold, prediction)

        self.assertEqual([row["scoring_status"] for row in units], [
            "prediction_identity_unresolved",
            "prediction_identity_unresolved",
            "gold_unit_without_ticker",
        ])
        self.assertFalse(units[0]["sentiment_scored"])
        self.assertTrue(units[1]["sentiment_scored"])
        self.assertFalse(units[2]["eligibility_scored"])
        metrics = _sentiment_metrics([row for row in units if row["sentiment_scored"]])
        self.assertEqual(metrics["scored_units"], 1)
        self.assertEqual(metrics["confusion"]["positive"]["missing"], 1)

    def test_sentiment_is_scored_independently_of_predicted_eligibility(self) -> None:
        gold = _gold("source-1", "development", "model_development_allowed")
        prediction = {
            "entities": [{"entity_id": "security:AAA", "ticker": "AAA"}],
            "issuer_views": [{
                "entity_id": "security:AAA",
                "composite_sentiment": "positive",
            }],
            "eligibility": [{
                "entity_id": "security:AAA",
                "product": "forecast_trigger",
                "eligible": False,
            }],
        }
        _article, units, _extras = _score_article(gold, prediction)
        self.assertEqual(units[0]["confusion"], "FN")
        self.assertTrue(units[0]["sentiment_exact"])

    def test_comparison_rejects_changed_population_before_reading_units(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            previous, current = root / "previous", root / "current"
            previous.mkdir()
            current.mkdir()
            base = {
                "authority": {
                    "article_population_sha256": "a",
                    "issuer_unit_population_sha256": "b",
                    "gold_labels_sha256": "c",
                }
            }
            (previous / "manifest.json").write_text(json.dumps(base), encoding="utf-8")
            changed = json.loads(json.dumps(base))
            changed["authority"]["article_population_sha256"] = "changed"
            (current / "manifest.json").write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "population or gold changed"):
                compare_audits(
                    previous,
                    current,
                    root / "comparison",
                    runtime_root=root,
                )

    def test_evaluate_writes_traceable_mismatch_chunks(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            consolidated = runtime / "consolidated"
            split = runtime / "split"
            inference = runtime / "inference"
            inference.mkdir()
            gold = _gold("source-1", "development", "model_development_allowed")
            consolidated.mkdir()
            _write_dataset(
                consolidated,
                [(
                    _authority("development", "model_development_allowed"),
                    iter([gold]),
                )],
            )
            create_frozen_split(
                consolidated,
                split,
                runtime_root=runtime,
            )
            audit_gold = split / "audit_gold.jsonl"
            prediction_row = {
                "source_id": "source-1",
                "source": {"source_id": "source-1", "rendered_product": {"text": "body"}},
                "prediction": {
                    "entities": [{
                        "entity_id": "security:AAA",
                        "ticker": "AAA",
                    }],
                    "issuer_views": [{
                        "entity_id": "security:AAA",
                        "composite_sentiment": "negative",
                    }],
                    "eligibility": [{
                        "entity_id": "security:AAA",
                        "product": "forecast_trigger",
                        "eligible": True,
                        "reasons": ["eligible_under:forecast_trigger"],
                        "blocking_flags": [],
                    }],
                },
                "error": None,
            }
            predictions = inference / "predictions.jsonl"
            predictions.write_text(json.dumps(prediction_row) + "\n", encoding="utf-8")
            identity = inference / "identity_snapshot.json"
            identity.write_text("{}\n", encoding="utf-8")
            inference_manifest = {
                "version": INFERENCE_VERSION,
                "status": "complete",
                "partition": "audit",
                "authority": {
                    "engine_version": ENGINE_VERSION,
                    "code_authority": [],
                    "partition_source_ids_sha256": sha256_json(["source-1"]),
                    "split_manifest_sha256": _sha256_path(split / "manifest.json"),
                    "partition_gold_sha256": _sha256_path(audit_gold),
                    "evaluation_targets_sha256": sha256_json([{
                        "source_id": "source-1",
                        "tickers": ["AAA"],
                    }]),
                },
                "files": {
                    "predictions.jsonl": _file_summary(predictions),
                    "identity_snapshot.json": _file_summary(identity),
                },
            }
            (inference / "manifest.json").write_text(
                json.dumps(inference_manifest) + "\n", encoding="utf-8"
            )

            report = evaluate_inference(
                split,
                consolidated,
                inference,
                runtime / "audit",
                runtime_root=runtime,
                mismatch_chunk_size=1,
            )

            self.assertEqual(report["population"]["mismatch_articles"], 1)
            self.assertEqual(report["metrics"]["issuer_sentiment"]["exact"], 0)
            self.assertTrue((runtime / "audit" / "mismatch_chunks" / "chunk_0001.jsonl").is_file())
            validation = json.loads(
                (runtime / "audit" / "VALIDATION.json").read_text(encoding="utf-8")
            )
            self.assertEqual(validation["status"], "pass")
            chunk = runtime / "audit" / "mismatch_chunks" / "chunk_0001.jsonl"
            chunk.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Mismatch chunk integrity mismatch"):
                validate_audit(runtime / "audit", runtime_root=runtime)


def _authority(authority_id: str, usage_policy: str) -> dict[str, object]:
    return {
        "authority_id": authority_id,
        "authority_version": "test-v1",
        "certification_level": "test",
        "partition": "sealed_test" if usage_policy == "final_evaluation_only" else "test",
        "usage_policy": usage_policy,
        "articles": 1,
        "root_relative_path": authority_id,
        "manifest_relative_path": f"{authority_id}/manifest.json",
        "manifest_sha256": "a" * 64,
    }


def _gold(source_id: str, authority_id: str, usage_policy: str) -> dict[str, object]:
    partition = "sealed_test" if usage_policy == "final_evaluation_only" else "test"
    return {
        "source_id": source_id,
        "source_timestamp": "2026-01-01T00:00:00Z",
        "authority_article_id": source_id,
        "authority_id": authority_id,
        "authority_version": "test-v1",
        "certification_level": "test",
        "partition": partition,
        "usage_policy": usage_policy,
        "source_hashes": {"source_text_sha256": "b" * 64},
        "article_forecast_eligible": True,
        "issuer_units": [{
            "unit_id": f"{source_id}::AAA",
            "authority_unit_id": f"{source_id}::AAA",
            "ticker": "AAA",
            "entity_id": "security:AAA",
            "entity_kind": "security",
            "identity_status": "resolved",
            "forecast_eligibility": "eligible",
            "sentiment": "positive",
            "reason_codes": [],
            "concepts": [],
            "gold_resolution": "test",
            "normalization_status": "complete",
        }],
        "lineage": {
            "source_relative_path": f"{authority_id}/{source_id}.json",
            "source_artifact_sha256": "c" * 64,
            "authority_manifest_sha256": "a" * 64,
        },
        "raw_gold_payload": {},
    }


def _ids(path: Path) -> list[str]:
    return [json.loads(line)["source_id"] for line in path.read_text(encoding="utf-8").splitlines()]


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
