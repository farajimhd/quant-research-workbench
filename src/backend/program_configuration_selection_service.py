"""Explicit app binding of approved immutable programme selections.

Startup installation is separate from source implementation and publication.
This module never discovers credentials, installs releases or starts a run.
"""
import asyncio
from copy import deepcopy
from types import MappingProxyType
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
