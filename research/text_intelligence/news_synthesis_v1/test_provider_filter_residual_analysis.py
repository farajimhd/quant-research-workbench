from __future__ import annotations

import unittest

from .provider_filter_residual_analysis import candidate_class, select_new_paths


def report_row(feature: str, rates: tuple[float, float, float]) -> dict[str, object]:
    return {
        "feature": feature,
        "support": 900,
        "eligible_rate": sum(rates) / 3,
        "discovery_support": 300,
        "validation_support": 300,
        "final_support": 300,
        "discovery_eligible_rate": rates[0],
        "validation_eligible_rate": rates[1],
        "final_eligible_rate": rates[2],
    }


class ProviderFilterResidualAnalysisTests(unittest.TestCase):
    def test_candidate_classes_are_temporal_and_directional(self) -> None:
        self.assertEqual(candidate_class(report_row("tag=noise", (0.01, 0.02, 0.03))), "stable_ineligible")
        self.assertEqual(candidate_class(report_row("tag=event", (0.97, 0.98, 0.96))), "stable_eligible")
        self.assertEqual(candidate_class(report_row("tag=mixed", (0.40, 0.45, 0.50))), "stable_mixed")
        self.assertEqual(candidate_class(report_row("tag=drift", (0.10, 0.30, 0.60))), "temporal_drift")

    def test_selection_excludes_prior_and_nonmetadata_paths(self) -> None:
        rows = [
            report_row("tag=new", (0.01, 0.01, 0.01)),
            report_row("tag=old", (0.01, 0.01, 0.01)),
            report_row("hour_et=10", (0.01, 0.01, 0.01)),
        ]
        selected = select_new_paths(rows, {"tag=old"})
        self.assertEqual([row["feature"] for row in selected], ["tag=new"])
        self.assertEqual(selected[0]["blind_review_exception_label"], "eligible")

    def test_support_floor_prevents_small_path_promotion(self) -> None:
        row = report_row("tag=small", (0.0, 0.0, 0.0))
        row["final_support"] = 29
        self.assertIsNone(candidate_class(row))


if __name__ == "__main__":
    unittest.main()
