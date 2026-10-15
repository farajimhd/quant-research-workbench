"""Explicit operator ClickHouse/Keeper transport for shared selections.

No DDL, grants, producers or implicit recovery. Unfinished persistent claims
require operator reconciliation; reconnect never re-adopts an active claim.
"""
import re
from threading import Lock
from concurrent.futures import ThreadPoolExecutor

from src.market_engine.typed_product_insert import insert_product_rows
from src.trading_runtime import strategy_configuration_schema as schema
from src.trading_runtime.strategy_configuration_selection import restore_configuration_selection
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.keeper_session import ManagedKeeperSession
from .strategy_configuration_reader import _select
from .strategy_configuration_publication import ConfigurationSelectionPublisher
from .qmd_columnar_history_transport import _owned_worker
from .certified_program_configuration import ConfigurationReferenceReader

_ROOT = "/quant-research-workbench/trading/strategy-configuration-v1"


def configuration_writer_preflight(client, *, require_execution_bindings=False, require_execution_risk=False):
    from src.trading_runtime.configuration_execution_binding import TABLES as execution_tables
    from src.trading_runtime.configuration_execution_risk import TABLES as risk_tables
    if type(require_execution_bindings) is not bool or type(require_execution_risk) is not bool:
        raise ValueError('Execution binding grant mode must be explicit')
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("Configuration writer principal is invalid")
    tables = {("arte", table.name) for table in schema.TABLES}
    execution_scopes = {('arte', table.name) for table in execution_tables}
    risk_scopes = {('arte', table.name) for table in risk_tables}
    recognized_tables = tables | execution_scopes | risk_scopes
    allowed_reads = recognized_tables | {("system", name) for name in
        ("tables", "columns", "parts", "storage_policies", "data_skipping_indices")}
    reads, writes = set(), set()
    for line in client.execute("SHOW GRANTS FINAL").splitlines():
        match = re.fullmatch(r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*)\."
                            r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match[4] != user:
            raise ValueError("Configuration writer has unknown or delegated grants")
        scope = match[2], match[3]
        for privilege in {item.strip() for item in match[1].split(",")}:
            if privilege == "SELECT" and scope in allowed_reads:
                reads.add(scope)
            elif privilege == "INSERT" and scope in recognized_tables:
                writes.add(scope)
            else:
                raise ValueError("Configuration writer has unauthorized write or DDL grants")
    system_reads = {('system', name) for name in
        ('tables', 'columns', 'parts', 'storage_policies', 'data_skipping_indices')}
    extra_reads, extra_writes = reads & execution_scopes, writes & execution_scopes
    risk_reads, risk_writes = reads & risk_scopes, writes & risk_scopes
    if (reads - execution_scopes - risk_scopes != tables | system_reads
            or writes - execution_scopes - risk_scopes != tables
            or extra_reads != extra_writes or extra_reads not in (set(), execution_scopes)
            or risk_reads != risk_writes or risk_reads not in (set(), risk_scopes)
            or require_execution_bindings and extra_reads != execution_scopes
            or require_execution_risk and risk_reads != risk_scopes):
        raise ValueError("Configuration writer grants are incomplete")
    storage_preflight(client, tables=schema.TABLES)
    if extra_reads:
        storage_preflight(client, tables=execution_tables)
    if risk_reads:
        storage_preflight(client, tables=risk_tables)


class ClickHouseConfigurationStorage:
    """Owned connection factories and one persistent immutable publication claim."""
    def __init__(self, reader, reader_factory, writer_factory, session, selected, reference_reader, concurrency):
        if (type(session) is not ManagedKeeperSession or not callable(reader_factory)
                or not callable(writer_factory) or type(reference_reader) is not ConfigurationReferenceReader
                or reference_reader.endpoint != getattr(reader, "base_url", None)
                or type(getattr(reader, "base_url", None)) is not str or not reader.base_url):
            raise ValueError("Configuration transport lacks explicit central authorities")
        self.reader, self.reader_factory, self.writer_factory = reader, reader_factory, writer_factory
        if type(concurrency) is not int or not 1 <= concurrency <= 6:
            raise ValueError("Configuration transport requires bounded read concurrency")
        self.concurrency = concurrency
        self.session, self.selected, self.reference_reader = session, selected, reference_reader
        self.keeper = session.client
        self.generation, self.client_id = session._generation, session.client.client_id
        self.path = _ROOT + "/" + selected.header["configuration_id"]
        self.claim = self.creation_id = None
        self.committed = False
        self.connections, self.lock = [reader], Lock()

    def _session(self):
        if (self.session.client is not self.keeper or not self.session.writable
                or self.session._generation != self.generation
                or self.keeper.client_id != self.client_id):
            raise RuntimeError("Configuration publication Keeper session changed")

    def _owned(self, factory):
        client = factory()
        with self.lock:
            if any(client is prior for prior in self.connections):
                raise ValueError("Configuration transport cannot reuse an owned connection")
            self.connections.append(client)
        if getattr(client, "base_url", None) != self.reader.base_url:
            client.close()
            raise ValueError("Configuration transport connection changed endpoint")
        return client

    def preflight(self):
        if self.reader.execute("SELECT getSetting('readonly')").strip() != "1":
            raise ValueError("Configuration publication reader must be SELECT-only")
        storage_preflight(self.reader, tables=schema.TABLES)
        writer = self._owned(self.writer_factory)
        try:
            configuration_writer_preflight(writer)
        finally:
            writer.close()
        self._session()
        digest = self.selected.header["content_hash"]
        started, committed = f"1\nstarted\n{digest}".encode(), f"1\ncommitted\n{digest}".encode()
        existing = self.read_selection(self.selected.header["configuration_id"])
        if existing is not None:
            if restore_configuration_selection(existing).header["content_hash"] != digest:
                raise ValueError("Configuration identity already has another immutable selection")
            value, stat = self.keeper.get(self.path)
            if value != committed:
                raise RuntimeError("Configuration seal lacks committed ownership proof; reconcile explicitly")
            self.claim, self.committed = committed, True
        else:
            self.keeper.ensure_path(_ROOT)
            self._session()
            try:
                self.keeper.create(self.path, started, ephemeral=False)
            except Exception as exc:
                raise RuntimeError("Configuration claim exists or acknowledgement is ambiguous; reconcile explicitly") from exc
            value, stat = self.keeper.get(self.path)
            if value != started:
                raise RuntimeError("Configuration claim changed after creation")
            self.claim = started
        self.creation_id = stat.czxid
        if type(self.creation_id) is not int or self.creation_id < 0:
            raise RuntimeError("Configuration claim lacks creation identity")
        self.assert_owner()

    def assert_owner(self):
        self._session()
        if self.claim is None:
            raise RuntimeError("Configuration publication ownership was not acquired")
        value, stat = self.keeper.get(self.path)
        if value != self.claim or stat.czxid != self.creation_id:
            raise RuntimeError("Configuration publication ownership claim changed")
        self._session()
        return stat

    def verify_references(self, selected):
        self.assert_owner()
        verified = self.reference_reader.verify(selected)
        if verified.selection != selected:
            raise ValueError("Configuration reference verification returned another selection")
        self.assert_owner()

    def _identity(self, identity):
        if identity != self.selected.header["configuration_id"]:
            raise ValueError("Configuration transport cannot access another selection")

    def read_children(self, identity):
        self._identity(identity)
        def read(table):
            client = self._owned(self.reader_factory)
            try:
                if client.execute("SELECT getSetting('readonly')").strip() != "1":
                    raise ValueError("Configuration child reader must be SELECT-only")
                return _select(client, table, identity)
            finally:
                client.close()
        rows, errors = {}, []
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="configuration-readback") as pool:
            futures = [(table, pool.submit(read, table)) for table in schema.CHILDREN]
            for table, future in futures:
                try:
                    rows[table.name] = future.result()
                except BaseException as exc:
                    errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Configuration child readback failed", errors)
        return rows

    def read_selection(self, identity):
        self._identity(identity)
        head = _select(self.reader, schema.SELECTION, identity)
        return {schema.SELECTION.name: head, **self.read_children(identity)} if head else None

    def _insert(self, table, rows):
        self.assert_owner()
        if self.committed or rows != self.selected.rows[table.name]:
            raise ValueError("Configuration insert differs from its immutable publication")
        if not rows:
            return
        client = self._owned(self.writer_factory)
        try:
            configuration_writer_preflight(client)
            self.assert_owner()
            insert_product_rows(client, table, [dict(row) for row in rows])
            self.assert_owner()
        finally:
            client.close()

    def insert_child(self, name, rows):
        table = next((table for table in schema.CHILDREN if table.name == name), None)
        if table is None:
            raise ValueError("Configuration insert escaped its defined child families")
        self._insert(table, rows)

    def insert_seal(self, rows):
        self._insert(schema.SELECTION, rows)

    def mark_committed(self):
        stat = self.assert_owner()
        if self.committed:
            return
        committed = f"1\ncommitted\n{self.selected.header['content_hash']}".encode()
        try:
            self.keeper.set(self.path, committed, version=stat.version)
        except Exception as exc:
            raise RuntimeError("Configuration commit acknowledgement is ambiguous; reconcile explicitly") from exc
        self.claim, self.committed = committed, True
        self.assert_owner()


async def publish_configuration_selection(*, reader_factory, writer_factory, session,
        source, reference_reader, concurrency):
    """Explicit operator entry point; it is not wired to automatic app creation."""
    selected = restore_configuration_selection(source)
    def run():
        reader = reader_factory()
        try:
            storage = ClickHouseConfigurationStorage(reader, reader_factory, writer_factory,
                session, selected, reference_reader, concurrency)
            publisher = ConfigurationSelectionPublisher(storage, concurrency=concurrency)
            restored = publisher._publish(selected)
            storage.mark_committed()
            return restored
        finally:
            reader.close()
    return await _owned_worker(run)
