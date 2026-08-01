from __future__ import annotations

import unittest

from .comparison import canonical_concept_family


class CanonicalConceptFamilyTests(unittest.TestCase):
    def test_precise_human_concepts_map_to_broad_families(self) -> None:
        self.assertEqual(canonical_concept_family("guidance.raise"), "guidance")
        self.assertEqual(canonical_concept_family("analyst.price_target_raise"), "analyst_action")
        self.assertEqual(canonical_concept_family("registered_direct_offering"), "financing")
        self.assertEqual(canonical_concept_family("clinical.fda_approval"), "regulatory")

    def test_unknown_concepts_are_excluded_from_family_scoring(self) -> None:
        self.assertEqual(canonical_concept_family("unmapped.precise_judgment"), "")


if __name__ == "__main__":
    unittest.main()
