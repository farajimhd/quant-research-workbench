"""Bounded SELECT-only reader for central immutable program releases.

No installation, publication, feature calculation, disk fallback or strategy
registration takes place here. The caller must pin a previously approved seal.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from uuid import UUID

from .arte_journal_schema import storage_preflight
from .columnar_strategy_release import TABLES, ProgramRelease, restore_release, _identity
from .columnar_strategy_schema import RELEASE, FIELD, RULE, NODE, UNIVERSE

_LIMITS = {FIELD.name: 4096, RULE.name: 5, NODE.name: 160, UNIVERSE.name: 1, RELEASE.name: 1}


def release_read_permission_preflight(client):
    """Require a dedicated SELECT-only principal with explicit table grants."""
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("Program release reader principal is invalid")
    allowed = {("arte", t.name) for t in TABLES} | {("system", name) for name in (
        "tables", "columns", "parts", "storage_policies", "data_skipping_indices")}
    lines = client.execute("SHOW GRANTS FINAL").splitlines()
    observed = set()
    for line in lines:
        match = re.fullmatch(r"GRANT SELECT ON ([A-Za-z_][A-Za-z0-9_]*)\."
                             r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match.group(3) != user or match.group(1, 2) not in allowed:
            raise ValueError("Program release reader has an unauthorized or delegated grant")
        observed.add(match.group(1, 2))
    if observed != allowed:
        raise ValueError("Program release reader grants are incomplete")


def _select(client, table, predicate):
    columns = ",".join(name for name, _ in table.columns)
    sql = (f"SELECT {columns} FROM arte.{table.name} WHERE {predicate} "
           f"ORDER BY {table.order} LIMIT {_LIMITS[table.name] + 1} FORMAT JSONEachRow")
    text = client.execute(sql)
    if not isinstance(text, str) or len(text.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("Program release readback exceeds bounded text response")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if len(rows) > _LIMITS[table.name]:
        raise ValueError(f"Program release has duplicate or excessive rows: {table.name}")
    expected = {name for name, _ in table.columns}
    for row in rows:
        if set(row) != expected:
            raise ValueError("Program release reader columns differ")
        for name, kind in table.columns:
            value = row[name]
            if kind.startswith("Decimal("):
                row[name] = Decimal(str(value))
            elif kind.startswith("DateTime64("):
                # The query's declared DateTime64 UTC contract owns this clock.
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                row[name] = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            elif kind == "UUID":
                row[name] = str(UUID(value))
            elif kind == "Float64":
                if type(value) not in (float, int):
                    raise ValueError("Program release float type differs")
                row[name] = float(value)
            elif kind.startswith("UInt") or kind.startswith("Nullable(UInt"):
                if value is not None and type(value) is not int:
                    raise ValueError("Program release integer type differs")
    return tuple(rows)


def read_program_release(client, *, strategy_number: int, expected_content_hash: str,
                         expected_code_hash: str, expected_winner_hash: str) -> ProgramRelease:
    """Fail closed on schema/placement, duplicate rows, changed seals or code."""
    if type(strategy_number) is not int or not 1 <= strategy_number < 2**32:
        raise ValueError("Invalid strategy number")
    for value in (expected_content_hash, expected_code_hash, expected_winner_hash):
        _identity(value)
    release_read_permission_preflight(client)
    storage_preflight(client, tables=TABLES)
    predicate = f"strategy_number={strategy_number}"
    head = _select(client, RELEASE, predicate)
    if len(head) != 1:
        raise ValueError("Program release is not sealed")
    rid = str(UUID(head[0]["release_id"]))
    rows = {RELEASE.name: head}
    for table in (FIELD, RULE, NODE, UNIVERSE):
        rows[table.name] = _select(client, table, f"release_id='{rid}'")
    restored = restore_release(rows)
    if (restored.header["content_hash"] != expected_content_hash
            or restored.header["code_hash"] != expected_code_hash
            or restored.header["source_winner_hash"] != expected_winner_hash):
        raise ValueError("Program release differs from pinned approval")
    if _select(client, RELEASE, predicate) != head:
        raise ValueError("Program release changed during readback")
    return restored


async def load_program_release(client_factory, **identity) -> ProgramRelease:
    """Offload blocking ClickHouse calls; own and close the dedicated reader."""
    def read():
        client = client_factory()
        try:
            return read_program_release(client, **identity)
        finally:
            client.close()
    task = asyncio.create_task(asyncio.to_thread(read))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # The reader belongs to the worker until its finally block closes it.
        # Settle that same read before allowing this owner to disappear.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException as failure:
                raise cancelled from failure
        try:
            task.result()
        except BaseException as failure:
            raise cancelled from failure
        raise
