"""Backtest setup choices retain the canonical candidate/run-plan authority."""
import unittest
from pathlib import Path
from unittest.mock import patch

from src.backend.trading_configuration_service import backtest_configuration_options
from src.trading_runtime.journal import TradingJournal


class BacktestConfigurationOptionsTests(unittest.TestCase):
    def test_defaults_to_active_profile_not_first_runtime(self):
        candidates = [{"candidate_id": "new", "candidate_revision": 68, "label": "Current",
                       "content_hash": "hash"}]
        plans = [{"run_plan_id": "balanced", "profile_id": "balanced"},
                 {"run_plan_id": "momentum", "profile_id": "active"}]
        snapshot = {"available_run_plans": plans, "run_plan_id": "balanced",
                    "configuration_model": {"strategy": {"active_profile_id": "active"}}}
        with patch.object(TradingJournal, "trading_configuration_candidate_summaries", return_value=candidates), patch(
            "src.backend.trading_configuration_service.backtest_configuration_snapshot", return_value=snapshot
        ) as resolve:
            result = backtest_configuration_options()
        resolve.assert_called_once_with(candidate_id="new")
        self.assertEqual(result["run_plan_id"], "momentum")
        self.assertEqual(result["available_run_plans"], plans)
        self.assertNotIn("payload", result["candidates"][0])

    def test_invalid_saved_candidate_keeps_selection_available(self):
        candidate = {"candidate_id": "old", "candidate_revision": 1, "label": "Old", "content_hash": "hash"}
        with patch.object(TradingJournal, "trading_configuration_candidate_summaries", return_value=[candidate]), patch(
            "src.backend.trading_configuration_service.backtest_configuration_snapshot", side_effect=ValueError("Invalid saved profile")
        ):
            result = backtest_configuration_options("old")
        self.assertEqual(result["candidate_id"], "old")
        self.assertEqual(result["error"], "Invalid saved profile")
        self.assertEqual(result["available_run_plans"], [])
        self.assertEqual(len(result["candidates"]), 1)

    def test_empty_and_missing_candidates_do_not_silently_change_selection(self):
        with patch.object(TradingJournal, "trading_configuration_candidate_summaries", return_value=[]):
            self.assertEqual(backtest_configuration_options()["available_run_plans"], [])
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                backtest_configuration_options("missing")

    def test_journal_summaries_do_not_decode_configuration_payloads(self):
        journal = TradingJournal(Path(":memory:"))
        try:
            for revision in (1, 2):
                journal.save_trading_configuration_candidate(candidate_id=str(revision),
                    candidate_revision=revision, label=f"Candidate {revision}", content_hash=str(revision), payload={"large": "model"})
            with patch("src.trading_runtime.journal.json.loads", side_effect=AssertionError("Summary decoded a model")):
                rows = journal.trading_configuration_candidate_summaries()
            self.assertEqual([row["candidate_revision"] for row in rows], [2, 1])
            self.assertNotIn("payload", rows[0])
        finally:
            journal.close()
