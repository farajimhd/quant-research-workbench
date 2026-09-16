"""Guard metadata-only history lists without changing full selected authority."""
from pathlib import Path
import unittest


FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"


class ConfigurationSummaryContractTests(unittest.TestCase):
    def test_history_lists_do_not_normalize_or_retain_configuration_payloads(self):
        source = (FRONTEND / "pages/TradingConfigurationPage.tsx").read_text(encoding="utf-8")
        self.assertIn("setCandidates(candidatePayload.rows.map(candidateSummary))", source)
        self.assertIn("setRevisions(revisionPayload.rows.map(revisionSummary))", source)
        self.assertIn("[candidateSummary(candidate), ...current.filter", source)
        self.assertNotIn("normalizeDraft(row.payload)", source)
        self.assertIn("normalizeDraft(approvedPayload.approved.payload)", source)

    def test_summary_types_exclude_payload_but_keep_display_identity(self):
        source = (FRONTEND / "features/trading-configuration/release.tsx").read_text(encoding="utf-8")
        for name, fields in [
            ("TestCandidateSummary", ["candidate_id", "candidate_revision", "content_hash", "created_at", "label", "release_state"]),
            ("RevisionSummary", ["revision_id", "revision", "content_hash", "approved_at", "label"]),
        ]:
            declaration = source.split(f"export type {name} = {{", 1)[1].split("};", 1)[0]
            self.assertNotIn("payload", declaration)
            for field in fields:
                self.assertIn(f"{field}:", declaration)
        self.assertIn("export type Revision = RevisionSummary & {", source)

    def test_debug_picker_shares_summary_contract(self):
        debug = (FRONTEND / "pages/BacktestDebugPage.tsx").read_text(encoding="utf-8")
        self.assertIn("setCandidates(payload.rows.map(candidateSummary))", debug)
        self.assertNotIn("type TestCandidateSummary =", debug)



if __name__ == "__main__":
    unittest.main()
