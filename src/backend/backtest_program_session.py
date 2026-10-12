"""Session scheduling over the existing prepared native cohort/fence API.

Preparation remains in QMD/scanner/Signal Stream producers and the admitted
compute funnel. This scheduler neither reads research files nor builds features.
"""
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
from datetime import timedelta
import asyncio
from itertools import islice


@dataclass(frozen=True)
class PreparedProgramCohortWork:
    first_bucket: int
    stop_bucket: int
    blocks: Mapping
    population: object
    projection: object
    scope: Mapping

    def __post_init__(self):
        if (type(self.first_bucket) is not int or type(self.stop_bucket) is not int
                or not 14400 <= self.first_bucket < self.stop_bucket <= 72000
                or self.stop_bucket - self.first_bucket > 256
                or not isinstance(self.blocks, Mapping) or not isinstance(self.scope, Mapping)
                or {"controller", "blocks", "population", "projection", "first_bucket", "stop_bucket", "initial_packet", "terminal"} & set(self.scope)):
            raise ValueError("Programme cohort work lacks bounded controller-owned scheduling")
        from inspect import signature, Parameter
        from .backtest_program_cohort import run_prepared_program_cohort
        parameters = signature(run_prepared_program_cohort).parameters
        allowed = {name for name, value in parameters.items() if value.kind is Parameter.KEYWORD_ONLY}
        required = {name for name in allowed if parameters[name].default is Parameter.empty}
        required -= {"first_bucket", "stop_bucket"}
        if set(self.scope) - allowed or required - set(self.scope):
            raise ValueError("Programme cohort work differs from its complete native scope")
        object.__setattr__(self, "blocks", MappingProxyType(dict(self.blocks)))
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))


@dataclass(frozen=True)
class ProgramSessionResult:
    status: str
    completed_cohorts: int
    planned_cohorts: int
    liquidity_boundaries: int
    source_clocks: int
    qualified_decisions: int
    policy_packet: object


def require_program_session_owner(controller):
    if getattr(controller, "_runtime_finished", False):
        raise RuntimeError("Programme runtime has already finished")
    if getattr(controller, "_program_terminal_active", False):
        raise RuntimeError("Programme terminal already owns controller dispatch")
    if (getattr(controller, "_program_session_active", False)
            and getattr(controller, "_program_session_task", None) is not asyncio.current_task()):
        raise RuntimeError("Programme session already owns controller dispatch")


async def run_prepared_program_session(controller, cohorts, *, initial_packet=None, terminal=True):
    """Evaluate contiguous prepared session coverage, fencing each bounded unit.

    `evaluated` means the requested clocks are fenced. Terminal lifecycle,
    financial captures and close publication still belong to the main run owner.
    """
    from .replay_run_service import ReplayRunController
    require_program_session_owner(controller)
    if (type(controller) is not ReplayRunController or type(terminal) is not bool
            or isinstance(cohorts, (str, bytes))
            or getattr(controller, "_program_session_active", False)):
        raise ValueError("Programme session lacks its exclusive native owner")
    work, definition, actor = validate_prepared_program_session(controller, cohorts)
    controller._program_session_active = True
    controller._program_session_task = asyncio.current_task()
    controller._program_evaluated_terminal = None
    packet, completed, liquidity, clocks, decisions = initial_packet, 0, 0, 0, 0
    try:
        for index, unit in enumerate(work):
            if controller._stop_requested:
                return ProgramSessionResult("stopped", completed, len(work), liquidity, clocks, decisions, packet)
            if controller.definition is not definition or controller._program_strategy is not actor:
                raise RuntimeError("Programme session ownership changed before its next cohort")
            result = await controller.run_fenced_program_cohort(unit.blocks, unit.population, unit.projection,
                **unit.scope, first_bucket=unit.first_bucket, stop_bucket=unit.stop_bucket,
                initial_packet=packet, terminal=terminal and index == len(work) - 1)
            packet = result.policy_packet
            liquidity += result.liquidity_boundaries
            clocks += result.source_clocks
            decisions += result.qualified_decisions
            if result.status not in ("completed", "stopped"):
                raise RuntimeError("Programme cohort returned an unrecognized completion state")
            if result.status == "stopped":
                return ProgramSessionResult("stopped", completed, len(work), liquidity, clocks, decisions, packet)
            completed += 1
        if terminal:
            controller._program_evaluated_terminal = (definition, controller._runtime, actor,
                controller.current_time, controller._journal_publisher.fenced_sequence)
        return ProgramSessionResult("evaluated", completed, len(work), liquidity, clocks, decisions, packet)
    finally:
        controller._program_session_active = False
        controller._program_session_task = None


def validate_prepared_program_session(controller, cohorts):
    """Check the complete requested plan without advancing any runtime clock."""
    from .backtest_program_inputs import PreparedProgramBlock, PreparedProgramScannerProjection
    from .qmd_columnar_population import CertifiedPopulationWindow
    from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
    work = tuple(islice(cohorts, 57601))
    if not work or len(work) > 57600 or any(type(unit) is not PreparedProgramCohortWork for unit in work):
        raise ValueError("Programme session needs bounded prepared cohort coverage")
    definition, actor = controller.definition, controller._program_strategy
    if actor is None or controller._program_dispatch_failure is not None:
        raise RuntimeError("Programme session requires its available programme runtime")
    day = definition.session_date.isoformat()
    start = market_day_boundary(day, 0)
    first_ms = (definition.requested_start - start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    stop_ms = (definition.session_end - start) / timedelta(milliseconds=1) + SESSION_OPEN_OFFSET_MS
    if first_ms % 1000 or stop_ms % 1000:
        raise ValueError("Programme session scheduling requires explicit whole-second endpoints")
    first, stop = int(first_ms // 1000), int(stop_ms // 1000)
    if work[0].first_bucket != first or work[-1].stop_bucket != stop:
        raise ValueError("Programme session cohorts do not cover the requested endpoints")
    required = set(actor._by_assignment)
    prior = first
    revision = None
    for unit in work:
        if (unit.first_bucket != prior or set(unit.blocks) != required
                or type(unit.population) is not CertifiedPopulationWindow
                or type(unit.projection) is not PreparedProgramScannerProjection
                or unit.projection.release_hash != actor.program_release.header["content_hash"]
                or unit.projection.source_revision != unit.population.source_revision
                or any(type(block) is not PreparedProgramBlock
                    or block.release_hash != actor.program_release.header["content_hash"]
                    for block in unit.blocks.values())):
            raise ValueError("Programme session has a gap, overlap or changed prepared authority")
        if revision is None:
            revision = unit.population.source_revision
        if unit.population.source_revision != revision:
            raise ValueError("Programme session changed its scanner population authority")
        prior = unit.stop_bucket
    return work, definition, actor
