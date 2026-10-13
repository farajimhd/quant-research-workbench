"""Read-only programme definition evidence, without restart or legacy lookup."""
from dataclasses import dataclass
from uuid import UUID
from types import MappingProxyType
import re

from .qmd_columnar_history_transport import _owned_worker
from .program_configuration_selection_service import program_configuration_selection_service
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix, V4CommittedPrefix
from src.trading_runtime.arte_journal_writer import load_typed_run_context
from src.trading_runtime.arte_backtest_definition import load_backtest_definition


@dataclass(frozen=True)
class ProgramSavedDefinitionEvidence:
    prefix: object
    context: object
    saved: object
    revision: object


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


async def load_program_saved_definition(client_factory, run_id, *, expected_code_hash):
    """Require the same running prefix around typed reads and central selection.

    This evidence is not an execution preflight, recovered runtime, owner lease
    or permission to start. Market, policy and financial recovery stay separate.
    """
    if (not callable(client_factory) or type(run_id) is not str or str(UUID(run_id)) != run_id
            or type(expected_code_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_code_hash) is None):
        raise ValueError("Programme saved definition needs canonical bounded source pins")
    selector = program_configuration_selection_service()
    if selector is None:
        raise ValueError("Programme saved definition requires its installed selection authority")
    connections = []
    def read():
        client = client_factory()
        if any(client is prior for prior in connections):
            raise ValueError("Programme definition reads cannot reuse an owned connection")
        connections.append(client)
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != "1":
                raise ValueError("Programme definition requires a read-only journal principal")
            prefix = load_verified_v4_prefix(client, run_id)
            if type(prefix) is not V4CommittedPrefix or prefix.status != "running" or prefix.last_sequence < 1:
                raise ValueError("Programme recovery definition requires a running V4 prefix")
            context = load_typed_run_context(client, run_id)
            if context["mode"] != "backtest" or context["code_hash"] != expected_code_hash:
                raise ValueError("Programme definition differs from its Backtest source pins")
            saved = load_backtest_definition(client, run_id, run_context=context)
            if load_verified_v4_prefix(client, run_id) != prefix:
                raise RuntimeError("Programme definition prefix moved during cold reads")
            return prefix, _freeze(context), _freeze(saved)
        finally:
            client.close()
    prefix, context, saved = await _owned_worker(read)
    candidate = saved["definition"]["configuration_revision_id"]
    if not candidate.startswith("strategy-configuration:"):
        raise ValueError("Saved definition is not a central programme selection")
    revision = await selector.snapshot(candidate)
    if (program_configuration_selection_service() is not selector
            or revision["content_hash"] != context["configuration_hash"]):
        raise ValueError("Programme saved configuration differs from its committed run")
    final_prefix, final_context, final_saved = await _owned_worker(read)
    if (final_prefix != prefix or final_context != context or final_saved != saved):
        raise RuntimeError("Programme definition changed during central selection review")
    return ProgramSavedDefinitionEvidence(prefix, _freeze(context), _freeze(saved), _freeze(revision))
