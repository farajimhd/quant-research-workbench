"""Native assignment identity for the completed-boundary Strategy 43 executor."""
from __future__ import annotations

from .strategy_engine import StrategyAssignment
from .strategy_forty_three_release import StrategyFortyThreeContract, release_contract
from .strategy_forty_three_rules import STRATEGY_ID, STRATEGY_NUMBER
from .strategy_registry import (
    FixedStrategyExecutorRegistration, register_fixed_strategy_executor,
    register_numbered_strategy,
)


class AssignedStrategyFortyThree:
    strategy_id = STRATEGY_ID
    revision = STRATEGY_NUMBER
    automatic = True
    contract = StrategyFortyThreeContract()

    def __init__(self, assignments: list[StrategyAssignment]):
        if (type(assignments) is not list or not assignments
                or any(type(row) is not StrategyAssignment
                    or (row.strategy_id, row.strategy_revision) != (STRATEGY_ID, STRATEGY_NUMBER)
                    or row.ticker == "LGHL" for row in assignments)
                or len({row.assignment_id for row in assignments}) != len(assignments)
                or len({(row.account_id, row.ticker) for row in assignments}) != len(assignments)):
            raise ValueError("Strategy 43 needs unique native tradable listing assignments")
        self._assignments = tuple(assignments)

    def assignments(self):
        return self._assignments

    def bind_campaign_registry(self, registry):
        for assignment in self._assignments:
            registry.register(assignment)

    async def on_event(self, *_args, **_kwargs):
        raise RuntimeError("Strategy 43 cannot evaluate raw events")

    async def on_observation(self, *_args, **_kwargs):
        raise RuntimeError("Strategy 43 cannot evaluate legacy frames")


def ensure_installed():
    """Install the immutable code catalog; this does NOT publish or launch it."""
    release = release_contract()
    register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
        STRATEGY_ID, STRATEGY_NUMBER, "100ms", StrategyFortyThreeContract,
        AssignedStrategyFortyThree))
    register_numbered_strategy(release)
    return release
