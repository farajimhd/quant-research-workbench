"""Automatic runtime identity for the unpublished fixed-bar Strategy 1.

The certified columnar gate and sparse coordinator own all decisions. Raw
events and legacy observation callbacks must never evaluate this strategy or
silently substitute an event-derived input for an ARTE product.
"""
from __future__ import annotations

from src.trading_runtime.signals import StrategyEvaluation
from src.trading_runtime.strategy_engine import StrategyAssignment
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


class AssignedStrategyOne:
    strategy_id = STRATEGY_ID
    revision = STRATEGY_NUMBER
    automatic = True

    def __init__(self, assignments: list[StrategyAssignment]) -> None:
        if (not isinstance(assignments, list) or not assignments
                or any(not isinstance(row, StrategyAssignment)
                       or (row.strategy_id, row.strategy_revision)
                       != (self.strategy_id, self.revision)
                       for row in assignments)):
            raise ValueError("Strategy 1 needs numbered assignments")
        keys = [(row.account_id, row.ticker) for row in assignments]
        if len(keys) != len(set(keys)):
            raise ValueError("Strategy 1 account/ticker assignment is duplicated")
        self._assignments = tuple(assignments)

    def assignments(self) -> tuple[StrategyAssignment, ...]:
        return self._assignments

    def bind_campaign_registry(self, registry) -> None:
        for assignment in self._assignments:
            registry.register(assignment)

    async def on_event(self, event, account_id: str) -> StrategyEvaluation:
        raise RuntimeError("Strategy 1 cannot evaluate raw events")

    async def on_observation(self, observation, account_id: str) -> StrategyEvaluation:
        raise RuntimeError("Strategy 1 cannot evaluate legacy strategy frames")
