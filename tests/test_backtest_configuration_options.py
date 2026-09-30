"""Backtest setup exposes only the normalized Strategy 1 release."""
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

    def test_only_numbered_release_is_listed_without_sqlite_candidate_reads(self):
        release = {"revision_id": "strategy-one-1:attempt", "content_hash": "a" * 64,
                   "run_plan_id": "balanced-replay", "revision": 1, "label": "Strategy 1",
                   "available_run_plans": [{"run_plan_id": "balanced-replay",
                                            "name": "Strategy 1", "profile_id": "strategy-one-1",
                                            "strategy_id": "early-squeeze-strategy",
                                            "strategy_revision": 1}]}
        with patch.object(TradingJournal, "trading_configuration_candidate_summaries",
                          side_effect=AssertionError("SQLite candidate read")), patch(
            "src.backend.backtest_strategy_one_configuration.selected_numbered_revision",
            return_value=release) as selected, patch(
            "src.backend.backtest_strategy_one_configuration.numbered_configuration_options",
            return_value=[release, {**release, "revision_id": "strategy-one-2:attempt", "revision": 2, "label": "Strategy 2"}]):
            result = backtest_configuration_options()
        selected.assert_called_once_with(revision_id="")
        self.assertEqual(result["candidate_id"], release["revision_id"])
        self.assertEqual(result["run_plan_id"], "balanced-replay")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][1]["label"], "Strategy 2")
        self.assertEqual(result["candidates"][0]["label"], "Strategy 1")
        self.assertNotIn("payload", result["candidates"][0])

    def test_foreign_revision_fails_closed(self):
        with patch("src.backend.backtest_strategy_one_configuration.selected_strategy_one_revision",
                   side_effect=ValueError("Only the immutable Strategy 1 configuration can Backtest")):
            with self.assertRaisesRegex(ValueError, "immutable numbered"):
                backtest_configuration_options("old")

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
