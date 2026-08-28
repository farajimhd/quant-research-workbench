from __future__ import annotations

import unittest

from scripts.build_news_v59_consolidated_gold_v2 import (
    is_operator_protected,
    merge_row,
)


def _parent(
    label: str,
    *,
    human_certified: bool = False,
    authority_class: str = "codex_provisional",
    usage_policy: str = "model_development_provisional",
) -> dict[str, object]:
    return {
        "source_id": "source-1",
        "forecast_eligibility_label": label,
        "forecast_eligible": label == "eligible",
        "human_certified": human_certified,
        "authority_class": authority_class,
        "usage_policy": usage_policy,
    }


def _audit(label: str, *, unresolved: bool = False) -> dict[str, object]:
    return {
        "review_id": "review-1",
        "final_label": label,
        "decision_path": "two_pass_agreement",
        "unresolved": unresolved,
    }


class BuildNewsV59ConsolidatedGoldV2Test(unittest.TestCase):
    def test_unaudited_parent_is_preserved_exactly(self) -> None:
        parent = _parent("ineligible", human_certified=True)
        merged, resolution = merge_row(parent, None, "successor")
        self.assertEqual(merged, parent)
        self.assertEqual(resolution, "parent_preserved_unaudited")

    def test_operator_label_precedes_conflicting_subagent_label(self) -> None:
        parent = _parent("ineligible", human_certified=True)
        merged, resolution = merge_row(parent, _audit("eligible"), "successor")
        self.assertEqual(merged["forecast_eligibility_label"], "ineligible")
        self.assertTrue(merged["human_certified"])
        self.assertEqual(resolution, "operator_manual_precedence")
        self.assertEqual(merged["reaudit_reviewed_label"], "eligible")

    def test_operator_protection_accepts_authority_and_usage_markers(self) -> None:
        self.assertTrue(
            is_operator_protected(
                _parent("eligible", authority_class="operator_reviewed_pattern_policy")
            )
        )
        self.assertTrue(
            is_operator_protected(
                _parent("eligible", usage_policy="model_development_human_policy_adjudicated")
            )
        )

    def test_resolved_subagent_label_updates_unprotected_parent(self) -> None:
        merged, resolution = merge_row(
            _parent("eligible"), _audit("ineligible"), "successor"
        )
        self.assertEqual(merged["forecast_eligibility_label"], "ineligible")
        self.assertFalse(merged["forecast_eligible"])
        self.assertEqual(resolution, "subagent_reaudit_applied")

    def test_unresolved_subagent_review_retains_and_excludes_parent(self) -> None:
        merged, resolution = merge_row(
            _parent("eligible"), _audit("eligible", unresolved=True), "successor"
        )
        self.assertEqual(merged["forecast_eligibility_label"], "eligible")
        self.assertEqual(merged["usage_policy"], "model_development_exclude_unresolved")
        self.assertFalse(merged["decisive"])
        self.assertEqual(resolution, "subagent_unresolved_parent_retained")

    def test_unresolved_review_does_not_downgrade_operator_authority(self) -> None:
        parent = _parent("eligible", human_certified=True)
        merged, resolution = merge_row(
            parent, _audit("ineligible", unresolved=True), "successor"
        )
        self.assertEqual(merged["forecast_eligibility_label"], "eligible")
        self.assertTrue(merged["human_certified"])
        self.assertEqual(resolution, "operator_manual_precedence")


if __name__ == "__main__":
    unittest.main()
