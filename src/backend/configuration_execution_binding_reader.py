"""Bounded cold reads of shared execution bindings; no writes or defaults."""
from uuid import UUID
import re

from .strategy_configuration_reader import _select
from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.configuration_execution_binding import (
    POLICY, SEAL, TABLES, restore_execution_binding)


def read_configuration_execution_binding(client_factory, *, configuration_id,
        expected_configuration_hash, expected_content_hash):
    if (not callable(client_factory) or type(configuration_id) is not str
            or str(UUID(configuration_id)) != configuration_id
            or any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                for value in (expected_configuration_hash, expected_content_hash))):
        raise ValueError('Execution binding requires its pinned configuration and content hashes')
    client = client_factory()
    try:
        if client.execute("SELECT getSetting('readonly')").strip() != '1':
            raise ValueError('Execution binding reader must be SELECT-only')
        storage_preflight(client, tables=TABLES)
        before = _select(client, SEAL, configuration_id)
        if len(before) != 1 or before[0]['configuration_hash'] != expected_configuration_hash:
            raise ValueError('Execution binding seal or configuration reference differs')
        if before[0]['content_hash'] != expected_content_hash:
            raise ValueError('Execution binding seal differs from its approved content hash')
        policies = _select(client, POLICY, configuration_id)
        result = restore_execution_binding({POLICY.name: policies, SEAL.name: before})
        after = _select(client, SEAL, configuration_id)
        if after != before:
            raise ValueError('Execution binding seal changed during cold read')
        return result
    finally:
        client.close()


async def load_configuration_execution_binding(client_factory, **pins):
    return await _owned_worker(read_configuration_execution_binding, client_factory, **pins)


def read_configuration_execution_risk(client_factory, *, configuration_id,
        expected_configuration_hash, expected_content_hash):
    from src.trading_runtime.configuration_execution_risk import RISK, restore_execution_risk
    if (not callable(client_factory) or type(configuration_id) is not str
            or str(UUID(configuration_id)) != configuration_id
            or any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                for value in (expected_configuration_hash, expected_content_hash))):
        raise ValueError('Execution risk requires pinned configuration and content hashes')
    client = client_factory()
    try:
        if client.execute("SELECT getSetting('readonly')").strip() != '1':
            raise ValueError('Execution risk reader must be SELECT-only')
        storage_preflight(client, tables=(RISK,))
        before = _select(client, RISK, configuration_id)
        result = restore_execution_risk({RISK.name: before})
        if (result.header['configuration_hash'] != expected_configuration_hash
                or result.header['content_hash'] != expected_content_hash):
            raise ValueError('Execution risk differs from approved configuration/hash')
        if _select(client, RISK, configuration_id) != before:
            raise ValueError('Execution risk changed during cold read')
        return result
    finally:
        client.close()


async def load_configuration_execution_risk(client_factory, **pins):
    return await _owned_worker(read_configuration_execution_risk, client_factory, **pins)
