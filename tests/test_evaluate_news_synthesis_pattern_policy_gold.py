from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scripts.evaluate_news_synthesis_pattern_policy_gold import (
    _authority_manifest,
    _result_block,
)


class EvaluateNewsSynthesisPatternPolicyGoldTest(unittest.TestCase):
    def test_result_block_keeps_split_confusion_and_paths_separate(self) -> None:
        result = _result_block(
            population_articles=12,
            gold_counts=Counter({"eligible": 5, "ineligible": 6, "unresolved": 1}),
            confusion=Counter({
                ("eligible", "eligible"): 4,
                ("eligible", "ineligible"): 1,
                ("ineligible", "eligible"): 2,
                ("ineligible", "ineligible"): 4,
            }),
            path_mismatches=Counter({"single_subject > report > issuer": 3}),
            title_policy_predictions=Counter({
                ("issuer_guidance", "eligible", "eligible"): 4,
            }),
        )
        self.assertEqual(result["confusion"], {"tp": 4, "fn": 1, "fp": 2, "tn": 4})
        self.assertEqual(result["mismatches"], 3)
        self.assertEqual(result["binary_gold_articles"], 11)
        self.assertEqual(result["nonbinary_gold_articles"], 1)
        self.assertEqual(
            result["mismatch_paths"], {"single_subject > report > issuer": 3}
        )

    def test_authority_manifest_accepts_successor_and_legacy_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "HASH_MANIFEST.json"
            legacy.write_text("{}\n", encoding="utf-8")
            self.assertEqual(_authority_manifest(root), legacy)
            successor = root / "MANIFEST.json"
            successor.write_text("{}\n", encoding="utf-8")
            self.assertEqual(_authority_manifest(root), successor)


if __name__ == "__main__":
    unittest.main()
