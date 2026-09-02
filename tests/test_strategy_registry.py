from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.trading_runtime.strategy_engine import (
    AssignmentStatus,
    STRATEGY_ID,
    StrategyAssignment,
    StrategyPermissions,
)
from src.trading_runtime.strategy_registry import (
    StrategyExecutorRegistration,
    installed_strategy_definitions,
    register_strategy_executor,
    strategy_executor,
    unregister_strategy_executor,
)


class StrategyExecutorRegistryTests(unittest.TestCase):
    def tearDown(self) -> None:
        unregister_strategy_executor("test-dynamic-strategy", 3)

    def test_registered_revision_is_discoverable_and_uses_its_own_contracts(self) -> None:
        registration = StrategyExecutorRegistration(
            strategy_id="test-dynamic-strategy",
            revision=3,
            implementation="tests.dynamic:Strategy",
            definition_factory=lambda: {
                "strategy_id": "test-dynamic-strategy",
                "revision": 3,
                "name": "Dynamic test strategy",
                "implementation": "tests.dynamic:Strategy",
                "automatic": True,
            },
            parameter_resolver=lambda values: {"threshold": float((values or {}).get("threshold", 0.4))},
            strategy_factory=lambda assignments: ("executor", tuple(assignments)),
            input_catalog_factory=lambda: [{"source_id": "market.last_price"}],
            timeframe_resolver=lambda _parameters: {"1m"},
            observation_projector=lambda _observation, timeframe: {"timeframe": timeframe},
            assignment_evaluator=lambda assignment, observation: (assignment, observation),
        )
        register_strategy_executor(registration)

        resolved = strategy_executor("test-dynamic-strategy", 3)

        self.assertEqual(resolved.parameter_resolver({})["threshold"], 0.4)
        self.assertEqual(resolved.strategy_factory(["assignment"]), ("executor", ("assignment",)))
        definition = next(
            row for row in installed_strategy_definitions()
            if row["strategy_id"] == "test-dynamic-strategy"
        )
        self.assertEqual(definition["executor"]["key"], "test-dynamic-strategy@3")

    def test_unregistered_revision_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "No installed Strategy executor"):
            strategy_executor("test-dynamic-strategy", 3)

    def test_builtin_historical_revision_26_remains_executable(self) -> None:
        registration = strategy_executor(STRATEGY_ID, 26)
        assignment = StrategyAssignment(
            assignment_id="historical-26",
            strategy_id=STRATEGY_ID,
            strategy_revision=26,
            account_id="SIM",
            ticker="SUGP",
            conid=1,
            status=AssignmentStatus.WATCHING,
            permissions=StrategyPermissions(enter=True),
            parameters=registration.parameter_resolver({}),
            state={},
            source="test",
            created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )

        executor = registration.strategy_factory([assignment])

        self.assertEqual(executor.revision, 26)
        definition = registration.definition()
        self.assertEqual(definition["revision"], 26)
        macd_group = next(
            row
            for row in definition["config"]["parameters"]["entry_rules"]["confirmation"]["groups"]
            if row["group_id"] == "macd-confirmation"
        )
        self.assertIn(
            "macd-signal-positive",
            [row["condition_id"] for row in macd_group["conditions"]],
        )

    def test_builtin_historical_revision_27_remains_executable(self) -> None:
        registration = strategy_executor(STRATEGY_ID, 27)

        self.assertEqual(registration.revision, 27)
        self.assertEqual(registration.definition()["revision"], 27)


if __name__ == "__main__":
    unittest.main()
