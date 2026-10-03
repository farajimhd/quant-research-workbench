"""Native assignment identity for the completed-boundary Strategy 45 executor."""
from __future__ import annotations

from .strategy_engine import StrategyAssignment
from .strategy_forty_five_release import StrategyFortyFiveContract, release_contract
from .strategy_forty_five_rules import STRATEGY_ID, STRATEGY_NUMBER
from .strategy_registry import (
    FixedStrategyExecutorRegistration, register_fixed_strategy_executor,
    register_numbered_strategy,
)


class AssignedStrategyFortyFive:
    strategy_id = STRATEGY_ID
    revision = STRATEGY_NUMBER
    automatic = True
    contract = StrategyFortyFiveContract()

    def __init__(self, assignments: list[StrategyAssignment]):
        if (type(assignments) is not list or not assignments
                or any(type(row) is not StrategyAssignment
                    or (row.strategy_id, row.strategy_revision) != (STRATEGY_ID, STRATEGY_NUMBER)
                    or row.ticker == "LGHL" for row in assignments)
                or len({row.assignment_id for row in assignments}) != len(assignments)
                or len({(row.account_id, row.ticker) for row in assignments}) != len(assignments)):
            raise ValueError("Strategy 45 needs unique native tradable listing assignments")
        self._assignments = tuple(assignments)

    def assignments(self):
        return self._assignments

    def bind_campaign_registry(self, registry):
        for assignment in self._assignments:
            registry.register(assignment)

    async def on_event(self, *_args, **_kwargs):
        raise RuntimeError("Strategy 45 cannot evaluate raw events")

    async def on_observation(self, *_args, **_kwargs):
        raise RuntimeError("Strategy 45 cannot evaluate legacy frames")


def ensure_installed():
    """Install the immutable code catalog; this does NOT publish or launch it."""
    release = release_contract()
    register_fixed_strategy_executor(FixedStrategyExecutorRegistration(
        STRATEGY_ID, STRATEGY_NUMBER, "100ms", StrategyFortyFiveContract,
        AssignedStrategyFortyFive))
    register_numbered_strategy(release)
    return release


from dataclasses import dataclass, fields
from .domain import TradingStateSnapshot
from .runtime import TradingRuntime


@dataclass(frozen=True)
class StrategyFortyFiveSnapshot(TradingStateSnapshot):
    leg_order_ownership: tuple[tuple[str,str,str], ...] = ()


class StrategyFortyFiveRuntime(TradingRuntime):
    """Capture native leg ownership at the engine boundary for presentation.

    Financial execution, cash, reservations and journal remain shared native
    authorities. No synthetic positions or extra broker raw fields are stored.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._leg_exit_sources = {}

    def register_leg_exit(self, exit_intent_id, entry_intent_id):
        previous = self._leg_exit_sources.setdefault(exit_intent_id,entry_intent_id)
        if previous != entry_intent_id:
            raise ValueError("Strategy 45 exit changed acquisition ownership")

    def projected_snapshot(self, *, as_of=None):
        snapshot = super().projected_snapshot(as_of=as_of)
        ownership = {}
        for group in self.order_manager._groups.values():
            if group.intent.action == "enter_long":
                entry = group.intent.intent_id
            elif group.intent.action == "exit":
                entry = self._leg_exit_sources.get(group.intent.intent_id)
                if not entry:
                    raise ValueError("Strategy 45 managed exit lacks native acquisition ownership")
            else:
                raise ValueError("Strategy 45 runtime has a foreign financial group")
            for broker_id in group.broker_order_roles:
                key = (group.account_id,str(broker_id))
                if key in ownership and ownership[key] != entry:
                    raise ValueError("Strategy 45 broker order belongs to two legs")
                ownership[key] = entry
        return StrategyFortyFiveSnapshot(**{f.name:getattr(snapshot,f.name)
            for f in fields(TradingStateSnapshot)}, leg_order_ownership=tuple(
                (account,broker_id,entry) for (account,broker_id),entry in sorted(ownership.items())))
