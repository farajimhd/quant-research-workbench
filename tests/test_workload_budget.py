from __future__ import annotations

import asyncio
import unittest

from src.backend.workload_budget import (
    WorkloadBudgetManager,
    WorkloadBudgetRejected,
    classify_workload,
)


class WorkloadClassificationTests(unittest.TestCase):
    def test_routes_are_assigned_to_isolated_lanes(self) -> None:
        self.assertEqual(classify_workload("POST", "/api/trading/replay/runs"), "simulation")
        self.assertEqual(classify_workload("GET", "/api/market-data/chart"), "charts")
        self.assertEqual(classify_workload("GET", "/api/market-discovery/scanner/history"), "discovery")
        self.assertEqual(
            classify_workload(
                "POST", "/api/market-discovery/configuration/materialize"
            ),
            "commands",
        )
        self.assertEqual(
            classify_workload(
                "GET", "/api/market-discovery/signal-stream/runtime"
            ),
            "general",
        )
        self.assertEqual(classify_workload("POST", "/api/build/submit"), "offline")
        self.assertEqual(classify_workload("POST", "/api/configuration/publish"), "commands")
        self.assertEqual(classify_workload("GET", "/api/health"), "general")


class WorkloadBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturated_lane_rejects_without_consuming_another_lane(self) -> None:
        manager = WorkloadBudgetManager(
            {"charts": 1, "commands": 1, "general": 1}, wait_seconds=0.01
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_chart() -> None:
            async with manager.lease("charts"):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(hold_chart())
        await entered.wait()
        with self.assertRaises(WorkloadBudgetRejected):
            async with manager.lease("charts"):
                self.fail("saturated chart lane must not admit another request")
        async with manager.lease("commands"):
            self.assertEqual(manager.snapshot()["lanes"]["commands"]["active"], 1)
        release.set()
        await holder

        snapshot = manager.snapshot()
        self.assertEqual(snapshot["lanes"]["charts"]["rejected"], 1)
        self.assertEqual(snapshot["lanes"]["charts"]["completed"], 1)
        self.assertEqual(snapshot["lanes"]["commands"]["completed"], 1)
