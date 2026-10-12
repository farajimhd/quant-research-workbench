"""Bounded concurrent SELECT-only reads of shared immutable selections."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from uuid import UUID
import json
import re

from src.trading_runtime import strategy_configuration_schema as schema
from src.trading_runtime.strategy_configuration_selection import restore_configuration_selection
from src.trading_runtime.arte_journal_schema import storage_preflight
from .qmd_columnar_history_transport import _owned_worker


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Configuration response contains duplicate fields")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite configuration JSON")


def _select(client, table, identity):
    limit = 1 if table is schema.SELECTION else 4096
    columns = ",".join(name for name, _ in table.columns)
    raw = client.execute(f"SELECT {columns} FROM arte.{table.name} "
        f"WHERE configuration_id=toUUID('{identity}') ORDER BY {table.order} "
        f"LIMIT {limit + 1} FORMAT JSONEachRow")
    if type(raw) is not str or len(raw.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("Configuration response exceeds its bounded byte budget")
    lines = tuple(line for line in raw.splitlines() if line.strip())
    if len(lines) > limit:
        raise ValueError("Configuration response exceeds its bounded row budget")
    rows = []
    for line in lines:
        row = json.loads(line, object_pairs_hook=_unique_object,
                         parse_constant=_reject_constant)
        if type(row) is not dict or set(row) != {name for name, _ in table.columns}:
            raise ValueError("Configuration response columns differ from defined schema")
        for name, kind in table.columns:
            if kind == "Float64":
                if type(row[name]) not in (int, float):
                    raise ValueError("Configuration response numeric type differs")
                row[name] = float(row[name])
            elif kind.startswith("DateTime64"):
                if type(row[name]) is not str:
                    raise ValueError("Configuration response clock type differs")
                at = datetime.fromisoformat(row[name].replace("Z", "+00:00"))
                # The declared ClickHouse UTC column owns a timezone-less wire clock.
                row[name] = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
        rows.append(row)
    return tuple(rows)


def read_configuration_selection(client_factory, *, configuration_id,
        expected_content_hash, concurrency):
    """Recheck the seal after every owned child read has settled.

    This verifies selection facts only. Programme and policy references need
    their independent certified readers before configuration admission.
    """
    if (not callable(client_factory) or type(configuration_id) is not str
            or str(UUID(configuration_id)) != configuration_id
            or type(expected_content_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_content_hash) is None
            or type(concurrency) is not int or not 1 <= concurrency <= 6):
        raise ValueError("Configuration reader requires a bounded pinned identity")
    lock, used = Lock(), []
    def owned():
        client = client_factory()
        with lock:
            if any(client is prior for prior in used):
                raise ValueError("Configuration reads cannot reuse an owned connection")
            used.append(client)
        return client
    root = owned()
    try:
        if root.execute("SELECT getSetting('readonly')").strip() != "1":
            raise ValueError("Configuration reader must be SELECT-only")
        storage_preflight(root, tables=schema.TABLES)
        head = _select(root, schema.SELECTION, configuration_id)
        if len(head) != 1 or head[0]["content_hash"] != expected_content_hash:
            raise ValueError("Configuration selection is unsealed or differs from pinned approval")
        def child(table):
            client = owned()
            try:
                if client.execute("SELECT getSetting('readonly')").strip() != "1":
                    raise ValueError("Configuration child reader must be SELECT-only")
                return _select(client, table, configuration_id)
            finally:
                client.close()
        rows, errors = {schema.SELECTION.name: head}, []
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="configuration-read") as pool:
            futures = [(table, pool.submit(child, table)) for table in schema.CHILDREN]
            for table, future in futures:
                try:
                    rows[table.name] = future.result()
                except BaseException as exc:
                    errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Configuration child reads failed", errors)
        result = restore_configuration_selection(rows)
        if _select(root, schema.SELECTION, configuration_id) != head:
            raise ValueError("Configuration selection changed during readback")
        return result
    finally:
        root.close()


async def load_configuration_selection(client_factory, **identity):
    """Nonblocking caller with repeated-cancellation cleanup of owned reads."""
    return await _owned_worker(read_configuration_selection, client_factory, **identity)
