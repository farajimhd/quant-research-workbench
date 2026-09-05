import unittest
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from src.backend import historical_runtime_versions as versions


class HistoricalRuntimeVersionsTests(unittest.TestCase):
    def check(self, revision=None, fingerprint="expected", backend="loaded"):
        with patch.object(versions, "qmd_source_fingerprint", return_value="expected"), \
             patch.object(versions, "backend_source_fingerprint", return_value=backend), \
             patch.object(versions, "LOADED_BACKEND_FINGERPRINT", "loaded"):
            return versions.runtime_version_check({"strategy": {
                "strategy_id": versions.STRATEGY_ID,
                "revision": versions.STRATEGY_REVISION if revision is None else revision}},
                {"source_fingerprint": fingerprint})

    def test_current_code_and_candidate_are_ready(self):
        self.assertEqual(self.check()["status"], "ready")

    def test_old_candidate_is_blocked_without_mutation(self):
        result = self.check(revision=versions.STRATEGY_REVISION - 1)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("new test candidate", result["summary"])

    def test_old_gateway_and_changed_backend_are_blocked(self):
        self.assertEqual(self.check(fingerprint=None)["status"], "blocked")
        self.assertEqual(self.check(backend="changed")["status"], "blocked")

    def test_workspace_fingerprint_is_deterministic(self):
        first = versions.qmd_source_fingerprint()
        self.assertEqual(len(first), 64)
        self.assertEqual(first, versions.qmd_source_fingerprint())

    def test_renamed_completed_candle_profile_preserves_effective_intervals(self):
        from copy import deepcopy
        from src.backend.trading_configuration_service import (
            _parameters_with_action_policies, _default_draft,
        )
        model = _default_draft()
        profile = next(row for row in model["strategy"]["profiles"]
                       if row["profile_id"] == "long-momentum-balanced")
        renamed = deepcopy(profile)
        renamed["profile_id"] = "renamed-momentum"
        rules = model["market_discovery"]["rule_sets"]
        self.assertEqual(_parameters_with_action_policies(profile, rules, []),
                         _parameters_with_action_policies(renamed, rules, []))


class BoundedStructurePrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_batches_bound_work_without_losing_competing_ticker_boundaries(self):
        from src.backend.replay_run_service import ReplayRunController
        controller = ReplayRunController.__new__(ReplayRunController)
        controller.run_id = "bounded-prefetch-test"
        controller._historical_structure_prefetch_task = None
        controller._historical_structure_prefetch_exhausted = False
        frames = [SimpleNamespace(ticker=("JUNS", "SUGP")[i % 2],
                                  as_of=datetime(2026, 8, 21, 11, 15, tzinfo=UTC) + timedelta(seconds=i))
                  for i in range(101)]
        controller.definition = SimpleNamespace(requested_start=frames[30].as_of)
        controller._historical_structure_frame_iterator = iter(frames)
        batches = []
        async def capture(groups):
            batches.append(groups)
        controller._fetch_historical_structure_prefetch = AsyncMock(side_effect=capture)
        with patch.dict(os.environ, {"REPLAY_STRUCTURE_PREFETCH_FRAMES": "16"}):
            while not controller._historical_structure_prefetch_exhausted:
                controller._schedule_historical_structure_prefetch()
                task = controller._historical_structure_prefetch_task
                if task is not None:
                    await task
                controller._historical_structure_prefetch_task = None
        self.assertTrue(all(sum(map(len, batch.values())) <= 16 for batch in batches))
        self.assertEqual(sorted((ticker, stamp) for batch in batches for ticker, stamps in batch.items() for stamp in stamps),
                         sorted((frame.ticker, frame.as_of) for frame in frames[30:]))
