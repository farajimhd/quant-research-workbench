import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("validation", Path(__file__).resolve().parents[1] / "scripts/validate_structure_optimization.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class ComparisonTests(unittest.TestCase):
    def case(self):
        value = {field: [] for field in validation.FIELDS}
        value.update(ticker="TEST", status="completed", events=10, days=[{"checkpoint_sha256": "abc"}], apply_seconds=2.0)
        return value

    def test_identical_results_allow_different_timings(self):
        original = self.case()
        candidate = copy.deepcopy(original)
        candidate["apply_seconds"] = 1.0
        self.assertEqual(validation.compare(original, candidate)["apply_speedup"], 2.0)

    def test_changed_checkpoint_input_or_event_output_fails(self):
        original = self.case()
        for field in ("days", "boundaries", "input_sha256", "emissions_sha256"):
            candidate = copy.deepcopy(original)
            candidate[field] = "different"
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                validation.compare(original, candidate)

    def test_missing_evidence_or_incomplete_run_fails(self):
        original = self.case()
        candidate = copy.deepcopy(original)
        del candidate["boundaries"]
        with self.assertRaises(RuntimeError):
            validation.compare(original, candidate)
        original["status"] = "failed"
        with self.assertRaises(RuntimeError):
            validation.compare(original, original)


if __name__ == "__main__":
    unittest.main()
