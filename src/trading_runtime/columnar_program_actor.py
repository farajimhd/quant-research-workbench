"""Assigned program actor on the existing runtime and OMS execution authority.

The vectorized evaluator never receives this actor's execution ledger. Only
the sequential selector sees actual position state. This actor owns no table,
file journal, feature calculation or alternative broker.
"""
from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence

from .columnar_program_coordinator import ProgramBoundary, ManagementState, select_program_proposal
from .columnar_strategy_release import ProgramRelease
from .filled_position_state import FillFact, FeeFact, FilledPosition, FilledPositionState
from .ibkr_schema import Execution
from .strategy_engine import StrategyAssignment
from .program_target_state import ProgramTargetState


class AssignedProgramStrategy:
    automatic = True

    def __init__(self, release: ProgramRelease, assignments: Sequence[StrategyAssignment],
                 listing_by_assignment: Mapping[str, str]):
        if not isinstance(release, ProgramRelease) or not 1 <= len(assignments) <= 10000:
            raise ValueError("Program actor needs a sealed release and bounded assignments")
        self.program_release = release
        self.strategy_id = release.header["strategy_id"]
        self.revision = release.header["strategy_number"]
        if any(type(row) is not StrategyAssignment or
               (row.strategy_id, row.strategy_revision) != (self.strategy_id, self.revision)
               for row in assignments):
            raise ValueError("Program assignments differ from immutable release identity")
        identities = [row.assignment_id for row in assignments]
        accounts = [(row.account_id, row.ticker) for row in assignments]
        if (len(set(identities)) != len(identities) or len(set(accounts)) != len(accounts)
                or set(listing_by_assignment) != set(identities)
                or any(type(value) is not str or not value for value in listing_by_assignment.values())):
            raise ValueError("Program assignments need unique exact listing attribution")
        self._assignments = tuple(assignments)
        self._by_assignment = {row.assignment_id: row for row in assignments}
        self._listings = MappingProxyType(dict(listing_by_assignment))
        self.execution_state = FilledPositionState()
        self.target_state = ProgramTargetState(release.header["target_fraction"])

    def assignments(self):
        return self._assignments

    def bind_campaign_registry(self, registry):
        for assignment in self._assignments:
            registry.register(assignment)

    async def on_event(self, event, account_id):
        raise RuntimeError("Columnar programs require completed certified batches, not raw events")

    async def on_observation(self, observation, account_id):
        raise RuntimeError("Columnar programs cannot consume legacy strategy frames")

    def position_for(self, assignment_id: str):
        assignment = self._by_assignment[assignment_id]
        key = (assignment.account_id, self.strategy_id, assignment_id, self._listings[assignment_id])
        return self.execution_state.positions.get(key, FilledPosition(*key, assignment.ticker))

    def proposals(self, boundary: ProgramBoundary, *, assignment_id: str,
                  management: ManagementState, terminal: bool = False):
        return select_program_proposal(self.program_release, boundary,
            position=self.position_for(assignment_id), management=management, terminal=terminal)

    def on_broker_executions(self, executions: Sequence[Execution], order_manager):
        """Resolve exact OMS request/role lineage before projecting any fill.

        Broker prices and commissions are execution facts. Neither an intent
        nor an acknowledgement creates a position. Repeated executions pass
        through the ledger's exact-content deduplication; conflicting IDs fail.
        """
        if len(executions) > 4096:
            raise ValueError("Program execution batch exceeds bounded projection capacity")
        facts = []
        for execution in executions:
            if type(execution) is not Execution:
                raise TypeError("Program actor accepts typed broker executions only")
            lineage = order_manager.execution_attribution(execution)
            assignment = self._by_assignment.get(lineage.assignment_id)
            if (assignment is None or lineage.account_id != assignment.account_id
                    or execution.symbol != assignment.ticker or execution.conid != assignment.conid):
                raise ValueError("Execution differs from exact program assignment")
            if lineage.role == "entry" and lineage.action in {"enter_long", "add_long"}:
                action = lineage.action
            elif lineage.role == "managed_exit" and lineage.action in {"reduce_long", "exit"}:
                action = ("take_profit" if lineage.action == "reduce_long"
                    and lineage.reason == "program:target" else lineage.action)
            elif lineage.role == "profit_target":
                action = "take_profit"
            elif lineage.role in {"protective_stop", "trailing_stop", "protective_exit"}:
                action = "exit"
            else:
                raise ValueError("Execution role is outside the long-program contract")
            if execution.side.upper() not in ({"B", "BUY"} if action in {"enter_long", "add_long"} else {"S", "SELL"}):
                raise ValueError("Execution side differs from actual OMS role")
            fact = FillFact(execution.execution_id, execution.order_id, lineage.intent_id,
                assignment.account_id, self.strategy_id, assignment.assignment_id,
                self._listings[assignment.assignment_id], assignment.ticker, action,
                Decimal(str(execution.size)), Decimal(str(execution.price)), execution.trade_time)
            fee = (FeeFact(execution.execution_id, Decimal(str(execution.commission)), execution.currency)
                   if execution.commission is not None else None)
            facts.append((fact, fee))
        for fact, fee in facts:
            before = self.position_for(fact.assignment_id)
            known = fact.execution_id in self.execution_state._fills
            after = self.execution_state.apply_fill(fact)
            if not known:
                self.target_state.apply_new_fill(fact, before, after)
            if fee is not None:
                self.execution_state.apply_fee(fee)
