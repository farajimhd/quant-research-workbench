from __future__ import annotations

import unittest

from .fresh_acceptance import _manifest_source_ids
from .manual_acceptance_review import build_manual_annotation
from .run_record_fresh_acceptance import _parse_compact_rows
from .sol_teacher_corpus import select_teacher_candidates


class FreshAcceptanceTests(unittest.TestCase):
    def test_manifest_source_ids_require_exact_unique_authority(self) -> None:
        manifest = {
            "sample_count": 2,
            "items": [{"source_id": "a"}, {"source_id": "b"}],
        }
        self.assertEqual(
            _manifest_source_ids(manifest, expected=2, name="fixture"),
            {"a", "b"},
        )
        manifest["items"][1]["source_id"] = "a"
        with self.assertRaisesRegex(RuntimeError, "missing or duplicated"):
            _manifest_source_ids(manifest, expected=2, name="fixture")

    def test_fresh_selection_seed_is_explicit_and_repeatable(self) -> None:
        rows = []
        for year in range(2010, 2027):
            for index in range(12):
                scope_count = index % 3
                rows.append(
                    {
                        "source_id": f"{year}-{index}",
                        "source_timestamp": f"{year}-01-01 00:00:00",
                        "event": {"tickers": [f"T{value}" for value in range(scope_count)]},
                        "v5_units": [],
                    }
                )
        first = select_teacher_candidates(
            rows, sample_size=34, sampling_seed="fresh-test"
        )
        second = select_teacher_candidates(
            rows, sample_size=34, sampling_seed="fresh-test"
        )
        self.assertEqual(
            [row["source_id"] for row in first],
            [row["source_id"] for row in second],
        )
        self.assertEqual(len(first), 34)

    def test_manual_review_helper_does_not_infer_semantic_judgments(self) -> None:
        item = {
            "sample_id": "N1001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00",
            "source_text_sha256": "hash",
            "publication": {"provider_tickers": ["ABC"]},
            "rendered_product": {"text": "ABC announced an offering."},
        }
        spec = {
            "extraction_decision": "labeled",
            "content_role": "primary_event",
            "source_origin": "issuer_direct",
            "issuer_units": [{
                "ticker": "ABC",
                "issuer_role": "primary_subject",
                "evidence_scope": "ticker_specific",
                "event_concepts": ["financing.public_offering"],
                "evidence_quotes": ["ABC announced an offering."],
                "modality": "confirmed",
                "time_orientation": "current",
                "positive_evidence_level": 0,
                "negative_evidence_level": 3,
                "semantic_direction": "negative",
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
                "eligibility_reason": "New issuer financing event.",
                "semantic_rationale": "The offering introduces dilution risk.",
            }],
        }
        annotation = build_manual_annotation(item, spec)
        unit = annotation["issuer_units"][0]
        self.assertEqual(unit["semantic_direction"], "negative")
        self.assertEqual(unit["event_concepts"], ["financing.public_offering"])
        self.assertEqual(annotation["candidate_tickers"], ["ABC"])
        self.assertEqual(
            annotation["ticker_dispositions"][0]["disposition"],
            "labeled_issuer_unit",
        )

    def test_manual_review_helper_accepts_compact_explicit_unit(self) -> None:
        item = {
            "sample_id": "N1001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00",
            "source_text_sha256": "hash",
            "publication": {
                "provider_tickers": ["ABC"],
                "title": "ABC announces a new contract",
            },
            "rendered_product": {"text": "ABC announces a new contract"},
        }
        spec = {
            "extraction_decision": "labeled",
            "content_role": "primary_event",
            "source_origin": "issuer_direct",
            "issuer_units": [{
                "t": "ABC", "r": "primary_subject", "s": "ticker_specific",
                "c": ["commercial.contract_award"],
                "q": ["ABC announces a new contract"], "m": "confirmed",
                "time": "current", "pos": 3, "neg": 0, "d": "positive",
                "f": True, "e": True, "h": True,
                "why": "New issuer event.",
                "because": "A new contract is positive.",
            }],
        }
        annotation = build_manual_annotation(item, spec)
        unit = annotation["issuer_units"][0]
        self.assertEqual(unit["ticker"], "ABC")
        self.assertEqual(unit["event_concepts"], ["commercial.contract_award"])
        self.assertEqual(unit["semantic_direction"], "positive")

    def test_manual_review_helper_accepts_article_level_default_disposition(self) -> None:
        item = {
            "sample_id": "N1002",
            "source_id": "source-2",
            "source_timestamp": "2026-01-02 12:00:00",
            "source_text_sha256": "hash-2",
            "publication": {"provider_tickers": ["AAA", "BBB"], "title": "Movers"},
            "rendered_product": {"text": "Movers"},
        }
        annotation = build_manual_annotation(item, {
            "role": "mover_recap",
            "origin": "automated_summary",
            "units": [],
            "default_ticker_disposition": "observed_price_only",
        })
        self.assertEqual(annotation["extraction_decision"], "no_supported_event")
        self.assertEqual(
            {row["disposition"] for row in annotation["ticker_dispositions"]},
            {"observed_price_only"},
        )

    def test_compact_review_parser_accepts_powershell_utf8_bom(self) -> None:
        rows = _parse_compact_rows(
            "\ufeffN1001|P|I|L|i|ABC~p~commercial.contract_award~+~3~0~111"
        )
        self.assertEqual(rows[0]["sample_id"], "N1001")
        self.assertEqual(rows[0]["units"][0]["d"], "positive")


if __name__ == "__main__":
    unittest.main()
