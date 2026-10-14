"""Operator-only ClickHouse/Keeper transport for central program releases.

Explicit operator invocation only. No DDL, role installation, app registration,
or implicit recovery. A persistent claim is single use: ambiguous or unfinished
publication requires operator reconciliation rather than automatic takeover.
"""
from __future__ import annotations

import asyncio
import re
from uuid import UUID

from src.market_engine.typed_product_insert import insert_product_rows
from .arte_journal_schema import storage_preflight
from .columnar_strategy_schema import RELEASE
from .columnar_strategy_release import TABLES, ReleasePublisher, restore_release
from .columnar_strategy_reader import _select, release_read_permission_preflight


_ROOT = "/quant-research-workbench/trading/columnar-release-v1"


def _writer_permission(client):
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("Release writer principal identity is invalid")
    tables = {("arte", table.name) for table in TABLES}
    allowed_read = tables | {("system", name) for name in
        ("tables", "columns", "parts", "storage_policies", "data_skipping_indices")}
    reads, writes = set(), set()
    for line in client.execute("SHOW GRANTS FINAL").splitlines():
        match = re.fullmatch(r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*)\."
            r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match.group(4) != user:
            raise ValueError("Release writer has unknown or delegated grants")
        scope = match.group(2, 3)
        for privilege in {value.strip() for value in match.group(1).split(",")}:
            if privilege == "SELECT" and scope in allowed_read:
                reads.add(scope)
            elif privilege == "INSERT" and scope in tables:
                writes.add(scope)
            else:
                raise ValueError("Release writer has unauthorized write/DDL grants")
    if reads != allowed_read or writes != tables:
        raise ValueError("Release writer grants are incomplete")


class ClickHouseReleaseStorage:
    """One numbered release, two isolated connections, one persistent fence."""
    def __init__(self, reader, writer, keeper, release):
        if (reader is writer or not isinstance(getattr(reader, "base_url", None), str)
                or reader.base_url != getattr(writer, "base_url", None)):
            raise ValueError("Release publication needs distinct clients on the same endpoint")
        self.reader, self.writer, self.keeper = reader, writer, keeper
        self.release = release
        self.path = f"{_ROOT}/{release.header['strategy_number']}"
        self.claim = None
        self.claim_creation_id = None
        self.committed = False

    def preflight(self):
        release_read_permission_preflight(self.reader)
        _writer_permission(self.writer)
        storage_preflight(self.reader, tables=TABLES)
        storage_preflight(self.writer, tables=TABLES)
        self._connected()
        content = self.release.header["content_hash"]
        started = f"1\nstarted\n{content}".encode("ascii")
        committed = f"1\ncommitted\n{content}".encode("ascii")
        # Only an already committed *exact* release may be verified without a
        # new claim. An unfinished persistent claim is never adopted.
        existing = self.read_number(self.release.header["strategy_number"])
        if existing is not None:
            if restore_release(existing).header["content_hash"] != content:
                raise ValueError("Strategy number has a different immutable release")
            value, stat = self.keeper.get(self.path)
            if value != committed:
                raise RuntimeError("Sealed release lacks a committed ownership proof; reconcile explicitly")
            self.claim, self.committed = committed, True
            self.claim_creation_id = stat.czxid
        else:
            self.keeper.ensure_path(_ROOT)
            try:
                self.keeper.create(self.path, started, ephemeral=False)
            except Exception as exc:
                raise RuntimeError("Release claim exists or creation is ambiguous; reconcile explicitly") from exc
            self.claim = started
            value, stat = self.keeper.get(self.path)
            if value != started:
                raise RuntimeError("Release claim changed immediately after creation")
            self.claim_creation_id = stat.czxid
        if type(self.claim_creation_id) is not int or self.claim_creation_id < 0:
            raise RuntimeError("Release claim lacks a verified creation identity")
        self.assert_owner()

    def _connected(self):
        state = getattr(self.keeper, "client_state", None)
        if (not getattr(self.keeper, "connected", False)
                or getattr(state, "name", str(state)) == "CONNECTED_RO"):
            raise RuntimeError("Release ownership Keeper is unavailable or read-only")

    def assert_owner(self):
        self._connected()
        if self.claim is None:
            raise RuntimeError("Release ownership was not acquired")
        value, stat = self.keeper.get(self.path)
        if value != self.claim or stat.czxid != self.claim_creation_id:
            raise RuntimeError("Release ownership claim changed")
        self._connected()
        return stat

    def read_number(self, strategy_number):
        if strategy_number != self.release.header["strategy_number"]:
            raise ValueError("Release storage cannot read another number")
        header = _select(self.reader, RELEASE, f"strategy_number={strategy_number}")
        if not header:
            return None
        rid = str(UUID(header[0]["release_id"]))
        return {RELEASE.name: header, **self.read_children(rid)}

    def read_children(self, release_id):
        rid = str(UUID(release_id))
        if rid != self.release.header["release_id"]:
            raise ValueError("Release storage cannot read another release")
        return {table.name: _select(self.reader, table, f"release_id='{rid}'")
                for table in TABLES[:-1]}

    def insert_rows(self, table_name, rows):
        self.assert_owner()
        if self.committed:
            raise RuntimeError("Committed release cannot receive more writes")
        table = next((table for table in TABLES if table.name == table_name), None)
        if table is None or not rows or len(rows) > 4096:
            raise ValueError("Release writer scope or batch is invalid")
        fields = {name for name, _ in table.columns}
        for row in rows:
            if (set(row) != fields or row.get("release_id") != self.release.header["release_id"]
                    or (table == RELEASE and row["strategy_number"] != self.release.header["strategy_number"])):
                raise ValueError("Release writer row escapes its immutable identity")
        insert_product_rows(self.writer, table, rows)
        self.assert_owner()

    def mark_committed(self):
        if self.committed:
            self.assert_owner()
            return
        stat = self.assert_owner()
        value = f"1\ncommitted\n{self.release.header['content_hash']}".encode("ascii")
        try:
            self.keeper.set(self.path, value, version=stat.version)
        except Exception as exc:
            raise RuntimeError("Release commit ownership acknowledgement is ambiguous; reconcile explicitly") from exc
        self.claim, self.committed = value, True
        self.assert_owner()


async def publish_program_release(reader_factory, writer_factory, keeper, rows):
    """Owned nonblocking transport; caller must supply approved operator authority."""
    snapshot = {name: tuple(dict(row) for row in family) for name, family in rows.items()}
    release = restore_release(snapshot)
    def run():
        reader = reader_factory()
        try:
            writer = writer_factory()
            try:
                storage = ClickHouseReleaseStorage(reader, writer, keeper, release)
                publisher = ReleasePublisher(storage)
                # All synchronous I/O executes within this one owned worker.
                restored = publisher._publish(snapshot, release)
                storage.mark_committed()
                return restored
            finally:
                writer.close()
        finally:
            reader.close()
    task = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise
