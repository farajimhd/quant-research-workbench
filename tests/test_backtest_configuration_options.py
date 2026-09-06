"""Backtest setup choices retain the canonical candidate/run-plan authority."""
import unittest
from pathlib import Path
from unittest.mock import patch

from src.backend.trading_configuration_service import backtest_configuration_options
from src.trading_runtime.journal import TradingJournal
from src.backend import trading_configuration_service as service


class BacktestConfigurationOptionsTests(unittest.TestCase):
    def test_plan_metadata_filters_routes_and_modes_without_resolving(self):
        model = {
            "strategy": {"profiles": [{"profile_id": "p", "definition_id": "s", "definition_revision": 47}]},
            "run_plans": {"plans": [{"run_plan_id": "r", "name": "Plan", "profile_id": "p"}]},
            "sessions": {"execution_routes": [{"execution_route_id": "on"}, {"execution_route_id": "off", "enabled": False}],
                         "strategy_deployments": [
                             {"strategy_deployment_id": "valid", "run_plan_id": "r", "modes": ["backtest"], "execution_route_ids": ["on"]},
                             {"strategy_deployment_id": "wrong-mode", "run_plan_id": "r", "modes": ["live"], "execution_route_ids": ["on"]},
                             {"strategy_deployment_id": "off-route", "run_plan_id": "r", "modes": ["backtest"], "execution_route_ids": ["off"]},
                         ]},
        }
        self.assertEqual(service._available_run_plans(model, "backtest"), [
            {"run_plan_id": "r", "name": "Plan", "profile_id": "p", "strategy_id": "s", "strategy_revision": 47}])

    def test_candidate_model_cache_is_hash_scoped_and_does_not_cache_invalid_models(self):
        service._CANDIDATE_MODEL_CACHE.clear()
        candidate = {"candidate_id": "cache-test", "content_hash": "a", "payload": {"v": 1}}
        try:
            with patch.object(service, "_migrate_draft", side_effect=lambda model: model), patch.object(service, "_validate_draft") as validate:
                first = service._validated_candidate_model(candidate)
                self.assertIs(service._validated_candidate_model(candidate), first)
                self.assertEqual(validate.call_count, 1)
                changed = {**candidate, "content_hash": "b", "payload": {"v": 2}}
                self.assertEqual(service._validated_candidate_model(changed), {"v": 2})
                self.assertEqual(validate.call_count, 2)
                validate.side_effect = ValueError("invalid")
                invalid = {**candidate, "content_hash": "c"}
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, "invalid"):
                        service._validated_candidate_model(invalid)
                self.assertEqual(validate.call_count, 4)
        finally:
            service._CANDIDATE_MODEL_CACHE.clear()

    def test_defaults_to_active_profile_not_first_runtime(self):
        candidates = [{"candidate_id": "new", "candidate_revision": 68, "label": "Current",
                       "content_hash": "hash"}]
        plans = [{"run_plan_id": "balanced", "profile_id": "balanced"},
                 {"run_plan_id": "momentum", "profile_id": "active"}]
        snapshot = {"available_run_plans": plans, "run_plan_id": "balanced",
                    "configuration_model": {"strategy": {"active_profile_id": "active"}}}
        with patch.object(TradingJournal, "trading_configuration_candidate_summaries", return_value=candidates), patch(
            "src.backend.trading_configuration_service.configuration_candidate", return_value=candidates[0]
        ), patch("src.backend.trading_configuration_service._validated_candidate_model", return_value=snapshot["configuration_model"]), patch(
            "src.backend.trading_configuration_service._available_run_plans", return_value=plans
        ), patch("src.backend.trading_configuration_service._resolve_runtime_configuration", side_effect=AssertionError("Options resolved a runtime")
        ) as resolve:
            result = backtest_configuration_options()
        resolve.assert_not_called()
        self.assertEqual(result["run_plan_id"], "momentum")
        self.assertEqual(result["available_run_plans"], plans)
        self.assertNotIn("payload", result["candidates"][0])

    def test_invalid_saved_candidate_keeps_selection_available(self):
        candidate = {"candidate_id": "old", "candidate_revision": 1, "label": "Old", "content_hash": "hash"}
        with patch.object(TradingJournal, "trading_configuration_candidate_summaries", return_value=[candidate]), patch(
            "src.backend.trading_configuration_service.configuration_candidate", return_value=candidate
        ), patch("src.backend.trading_configuration_service._validated_candidate_model", side_effect=ValueError("Invalid saved profile")
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
