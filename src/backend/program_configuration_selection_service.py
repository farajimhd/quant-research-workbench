"""Explicit app binding of approved immutable programme selections.

Startup installation is separate from source implementation and publication.
This module never discovers credentials, installs releases or starts a run.
"""
import asyncio
from copy import deepcopy
from types import MappingProxyType
from threading import Lock
from uuid import UUID
import re

from .certified_program_configuration import load_certified_program_configuration

_SERVICE = None


class ProgramConfigurationSelectionService:
    def __init__(self, *, approved_selections, selection_client_factory,
            release_client_factory, policy_client_factory, policy_dispatch, concurrency):
        pins = dict(approved_selections)
        if (not 1 <= len(pins) <= 256 or type(concurrency) is not int or not 1 <= concurrency <= 6
                or any(type(identity) is not str or str(UUID(identity)) != identity
                       or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                       for identity, digest in pins.items())
                or any(not callable(factory) for factory in
                       (selection_client_factory, release_client_factory, policy_client_factory))):
            raise ValueError("Programme selection service needs explicit bounded approved pins")
        self.pins = MappingProxyType(pins)
        self.authorities = MappingProxyType(dict(selection_client_factory=selection_client_factory,
            release_client_factory=release_client_factory, policy_client_factory=policy_client_factory,
            policy_dispatch=policy_dispatch, concurrency=concurrency))
        self._prepared_lock = Lock()
        self._prepared = None

    async def snapshot(self, candidate_id, run_plan_id=""):
        if type(candidate_id) is not str or not candidate_id.startswith("strategy-configuration:"):
            raise ValueError("Programme selection requires its exact central revision identity")
        identity = candidate_id.removeprefix("strategy-configuration:")
        digest = self.pins.get(identity)
        if digest is None:
            raise ValueError("Programme configuration selection is not approved for this app")
        certified = await load_certified_program_configuration(configuration_id=identity,
            expected_content_hash=digest, **self.authorities)
        revision = deepcopy(certified.revision)
        if run_plan_id and revision["run_plan_id"] != run_plan_id:
            raise ValueError("Programme selection differs from the requested run plan")
        revision["available_run_plans"] = [{
            "run_plan_id": revision["run_plan_id"], "name": revision["label"],
            "strategy_id": certified.selection.header["strategy_id"],
            "strategy_revision": certified.selection.header["strategy_revision"],
            "profile_id": candidate_id,
        }]
        return revision

    async def options(self):
        # Each snapshot already has bounded concurrent reads. Keep one authority
        # packet active at a time rather than multiplying nested connection pools.
        return tuple([await self.snapshot("strategy-configuration:" + identity)
                      for identity in sorted(self.pins)])

    async def prepare_controller(self, run_service, definition, *, assignments,
            listing_by_assignment, liquidity_resolution_ms):
        """Use installed app authorities for native certified preparation only."""
        from .replay_run_service import ReplayRunService, ReplayRunDefinition
        if (program_configuration_selection_service() is not self
                or type(run_service) is not ReplayRunService
                or type(definition) is not ReplayRunDefinition):
            raise ValueError("Programme preparation requires its installed app authority")
        revision = definition.configuration_revision
        candidate = revision.get("revision_id")
        if type(candidate) is not str or not candidate.startswith("strategy-configuration:"):
            raise ValueError("Programme preparation requires its reviewed central selection")
        digest = self.pins.get(candidate.removeprefix("strategy-configuration:"))
        if digest is None or revision.get("selection_hash") != digest:
            raise ValueError("Programme preparation selection is not approved for this app")
        controller = await run_service.prepare_certified_program_controller(definition,
            assignments=assignments, listing_by_assignment=listing_by_assignment,
            liquidity_resolution_ms=liquidity_resolution_ms, **self.authorities)
        if program_configuration_selection_service() is not self:
            raise RuntimeError("Programme preparation lost its installed app authority")
        return controller

    async def launch_controller(self, run_service, controller):
        """Recheck app approval, then use the existing fully bound native gate.

        Operational authorization and complete typed preparation belong to the
        caller. This adds no alternative engine, journal or publication path.
        """
        await self._review_controller(run_service, controller)
        with self._prepared_lock:
            if self._prepared is not None and self._prepared == (run_service, controller):
                self._prepared = None
        return await run_service.launch_prepared_program_controller(controller)

    async def stage_controller(self, run_service, controller):
        """Retain one fully prepared native handoff without admission or start."""
        from .backtest_program_launch import require_bound_program_start
        await self._review_controller(run_service, controller)
        await require_bound_program_start(controller)
        with self._prepared_lock:
            if self._prepared is not None and self._prepared != (run_service, controller):
                raise RuntimeError("App already retains another prepared programme handoff")
            if self._prepared is None:
                self._prepared = (run_service, controller)
        return controller

    async def staged_controller(self, run_service):
        """Recheck the retained native proof before an app preflight can use it."""
        from .backtest_program_launch import require_bound_program_start
        with self._prepared_lock:
            prepared = self._prepared
        if prepared is None or prepared[0] is not run_service:
            raise ValueError("Programme Backtest has no bound native launch prepared for this app")
        controller = prepared[1]
        await self._review_controller(run_service, controller)
        await require_bound_program_start(controller)
        with self._prepared_lock:
            if self._prepared is not prepared:
                raise RuntimeError("Prepared programme handoff changed during app review")
        return controller

    async def _review_controller(self, run_service, controller):
        from .replay_run_service import ReplayRunService, ReplayRunController
        from .backtest_program_journal_bootstrap import _configuration_hash
        from src.trading_runtime.strategy_configuration_selection import StrategyConfigurationSelection
        if (program_configuration_selection_service() is not self
                or type(run_service) is not ReplayRunService or type(controller) is not ReplayRunController
                or controller.runtime_root != run_service.runtime_root
                or controller.status != "created" or controller._task is not None):
            raise ValueError("Programme launch requires its installed app authority")
        definition = controller.definition
        revision = definition.configuration_revision
        candidate = revision.get("revision_id")
        selection = getattr(controller, "_program_configuration_selection", None)
        if (type(selection) is not StrategyConfigurationSelection or type(candidate) is not str
                or candidate != "strategy-configuration:" + selection.header["configuration_id"]
                or self.pins.get(selection.header["configuration_id"]) != selection.header["content_hash"]
                or revision.get("selection_hash") != selection.header["content_hash"]):
            raise ValueError("Programme launch selection is not approved for this app")
        reviewed_hash = _configuration_hash(definition)
        current = await self.snapshot(candidate)
        if (program_configuration_selection_service() is not self or controller.definition is not definition
                or current["content_hash"] != reviewed_hash or _configuration_hash(definition) != reviewed_hash
                or current["selection_hash"] != selection.header["content_hash"]):
            raise RuntimeError("Programme launch changed its reviewed central selection")

    async def preflight(self, run_service, *, configuration_revision, sessions,
            start_time, end_time, initial_cash):
        """Describe existing prepared coverage only for the exact reviewed scope."""
        from datetime import time
        from src.trading_runtime.journal_contract import canonical_json
        controller = await self.staged_controller(run_service)
        definition = controller.definition
        revision = definition.configuration_revision
        if (type(sessions) is not tuple or sessions != (definition.session_date,)
                or definition.final_session_date not in (None, definition.session_date)
                or start_time != definition.start_time or end_time != (definition.end_time or time(20))
                or type(initial_cash) not in (int, float) or initial_cash != definition.initial_cash
                or configuration_revision.get("revision_id") != revision["revision_id"]
                or configuration_revision.get("selection_hash") != revision["selection_hash"]
                or configuration_revision.get("content_hash") != revision["content_hash"]
                or canonical_json(configuration_revision.get("payload")) != canonical_json(revision["payload"])):
            raise ValueError("Programme preflight differs from its prepared session or reviewed configuration")
        days = [day.isoformat() for day in sessions]
        return dict(schema_version=1, mode="backtest", ready=True, strategy_run_ready=True,
            prepared_run_id=controller.run_id, execution_interval=definition.execution_interval,
            window=dict(sessions=days, session_count=len(days), start=days[0], end=days[-1]),
            market_data_plan=deepcopy(definition.market_data_plan),
            causal_v7_plan=deepcopy(definition.causal_v7_plan),
            checks=[dict(id="prepared_programme_funnel", label="Prepared programme funnel", status="ready",
                required=True, summary="Certified inputs and native typed launch ownership verified.",
                evidence=controller.run_id)], coverage={}, gateway={"source": "qmd_history_certified"})


def install_program_configuration_selection_service(service):
    """Operator-authorized startup hook; no public HTTP installation endpoint."""
    global _SERVICE
    if type(service) is not ProgramConfigurationSelectionService:
        raise ValueError("App programme selection requires the central built-in service")
    if _SERVICE is not None and _SERVICE is not service:
        raise RuntimeError("Installed programme selection authority cannot be replaced")
    _SERVICE = service


def program_configuration_selection_service():
    return _SERVICE


def selected_program_configuration(candidate_id, run_plan_id=""):
    """Synchronous configuration boundary; HTTP callers run it in owned workers."""
    service = program_configuration_selection_service()
    if service is None:
        raise ValueError("Programme configuration selection is not installed")
    return asyncio.run(service.snapshot(candidate_id, run_plan_id))
