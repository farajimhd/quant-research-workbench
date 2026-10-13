"""Join existing cold programme authorities without installing or restarting."""
from dataclasses import dataclass
from datetime import date
from threading import Lock

from .program_saved_definition import load_program_saved_definition, reconstruct_program_saved_definition
from .program_checkpoint_recovery import recover_program_financial_checkpoint
from .backtest_market_data import market_day_boundary, SESSION_OPEN_OFFSET_MS
from .backtest_v4_run_context import historical_strategy_one_portfolio_profiles


@dataclass(frozen=True)
class ProgramRecoveryEvidence:
    definition: object
    financial: object
    run_definition: object


async def load_program_recovery_evidence(client_factory, run_id, *, expected_code_hash,
        broker_contract, broker_head_reader, observation_head_reader, profiles, broker_read_concurrency,
        source_preflight):
    """Require coherent definition/account/clock evidence from owned readers.

    The explicit asynchronous source preflight must prove current certified
    database plans; native reconstruction must reproduce all saved rows.
    Policy replay, OMS lineage and recovered-owner admission remain mandatory
    gates. This grants no restart or write permission.
    """
    if not callable(client_factory) or not callable(source_preflight):
        raise ValueError("Programme recovery evidence requires its owned reader factory")
    connections, lock = [], Lock()
    def owned_factory():
        client = client_factory()
        with lock:
            if any(client is prior for prior in connections):
                raise ValueError("Programme recovery evidence cannot reuse an owned connection")
            connections.append(client)
        return client
    definition = await load_program_saved_definition(owned_factory, run_id, expected_code_hash=expected_code_hash)
    # The caller resolves certified database coverage using its existing source
    # authorities; a saved plan token alone never establishes current readiness.
    preflight = await source_preflight(definition)
    run_definition = reconstruct_program_saved_definition(definition, preflight)
    profiles = tuple(profiles)
    if ({profile.account_id for profile in profiles} != set(definition.context["account_ids"])
            or len(profiles) != len(definition.context["account_ids"])):
        raise ValueError("Programme recovery profiles differ from the committed run accounts")
    expected_profiles, _ = historical_strategy_one_portfolio_profiles(
        run_definition.configuration_revision["payload"])
    if profiles != expected_profiles:
        raise ValueError("Programme recovery profiles differ from the committed portfolio configuration")
    financial = await recover_program_financial_checkpoint(owned_factory, prefix=definition.prefix,
        broker_contract=broker_contract, broker_head_reader=broker_head_reader,
        observation_head_reader=observation_head_reader, profiles=profiles,
        broker_read_concurrency=broker_read_concurrency)
    parent = definition.saved["definition"]
    day = date.fromisoformat(definition.context["session_date"])
    if (financial.prefix != definition.prefix
            or not market_day_boundary(day, parent["start_local_ms"] - SESSION_OPEN_OFFSET_MS)
                <= financial.completed_at
                <= market_day_boundary(day, parent["end_local_ms"] - SESSION_OPEN_OFFSET_MS)):
        raise ValueError("Programme recovery checkpoint differs from its saved session window")
    final = await load_program_saved_definition(owned_factory, run_id, expected_code_hash=expected_code_hash)
    if final != definition:
        raise RuntimeError("Programme recovery definition moved during financial reads")
    return ProgramRecoveryEvidence(definition, financial, run_definition)
