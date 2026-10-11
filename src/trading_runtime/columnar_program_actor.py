"""Assigned program actor on the existing runtime and OMS execution authority.

The vectorized evaluator never receives this actor's execution ledger. Only
the sequential selector sees actual position state. This actor owns no table,
file journal, feature calculation or alternative broker.
"""
from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from .columnar_program_coordinator import ProgramBoundary, ManagementState, select_program_proposal
from .columnar_strategy_release import ProgramRelease
from .filled_position_state import FillFact, FeeFact, FilledPosition, FilledPositionState
from .ibkr_schema import Execution
from .strategy_engine import StrategyAssignment
from .program_target_state import ProgramTargetState
from .program_trail_state import ProgramTrailState
from .oms_assignment_observation import AssignmentOrderObservation


@dataclass(frozen=True)
class ProgramSelection:
    status: str
    proposals: tuple


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
        self.trail_state = ProgramTrailState()
        self._selection_clocks = {}

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

    def select_from_orders(self, boundary: ProgramBoundary, *, assignment_id: str,
            observation: AssignmentOrderObservation, terminal: bool = False) -> ProgramSelection:
        """Select using actual OMS facts and the actor's execution-derived policy.

        Missing/uniformity/coverage observations remain explicit wait reasons.
        Neither caller-provided target prices nor broker quote peaks are used.
        """
        assignment = self._by_assignment[assignment_id]
        position = self.position_for(assignment_id)
        clocks = (boundary.boundary_at, boundary.available_at, observation.observed_at)
        if any(getattr(clock, "tzinfo", None) is None or clock.utcoffset() is None for clock in clocks):
            raise ValueError("Program selection requires timezone-aware completed clocks")
        if (type(observation) is not AssignmentOrderObservation
                or (observation.account_id, observation.assignment_id, observation.ticker) !=
                   (assignment.account_id, assignment_id, assignment.ticker)
                or observation.observed_at != boundary.boundary_at
                or boundary.listing_id != position.listing_id or boundary.ticker != position.ticker
                or set(boundary.rules) != set(self.program_release.programs)
                or set(boundary.validity) != set(self.program_release.programs)
                or any(type(value) is not bool for value in (*boundary.rules.values(), *boundary.validity.values()))
                or type(boundary.eligible) is not bool or type(terminal) is not bool):
            raise ValueError("Program selection differs from its completed assignment inputs")
        if any(type(value) is not bool for value in (observation.pending_acquisition, observation.pending_reduction)):
            raise ValueError("Program selection requires typed pending-order state")
        if position.last_management_fill_at is not None and position.last_management_fill_at > boundary.boundary_at:
            raise ValueError("Program selection cannot see a future fill")
        signature = (boundary, terminal)
        previous = self._selection_clocks.get(assignment_id)
        if previous is not None and boundary.boundary_at <= previous[0]:
            if boundary.boundary_at == previous[0] and signature == previous[1]:
                return ProgramSelection("already_consumed", ())
            raise ValueError("Program selection boundary is conflicting or out of order")
        self.trail_state.observe(boundary, position,
            minimum_age_seconds=self.program_release.minimum_age_seconds["trail"])
        stops = [order for order in observation.working_orders
            if order.role in {"protective_stop", "trailing_stop"} and order.stop_price > 0]
        prices = {order.stop_price for order in stops}
        target = self.target_state.target_for(position)
        management = ManagementState(observation.observed_at, observation.pending_acquisition,
            observation.pending_reduction, next(iter(prices)) if len(prices) == 1 else 0.,
            float(target) if target is not None else 0., float(self.trail_state.water_for(position)))
        if (not observation.pending_acquisition and not observation.pending_reduction
                and (observation.unobserved_order_ids or position.position_open and (len(prices) != 1
                    or sum(order.remaining_quantity for order in stops) + 1e-6 < float(position.quantity)))):
            status, proposals = "awaiting_protection_observation", ()
        else:
            proposals = self.proposals(boundary, assignment_id=assignment_id,
                management=management, terminal=terminal)
            status = "selected" if proposals else "pending_reduction" if observation.pending_reduction else (
                "pending_acquisition" if observation.pending_acquisition else "wait")
        self._selection_clocks[assignment_id] = (boundary.boundary_at, signature)
        return ProgramSelection(status, proposals)

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
                self.trail_state.apply_new_fill(fact, before, after)
            if fee is not None:
                self.execution_state.apply_fee(fee)
