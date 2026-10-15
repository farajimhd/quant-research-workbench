"""Explicit binding publication through the existing immutable Keeper protocol."""
from threading import Lock

from .strategy_configuration_transport import (
    ClickHouseConfigurationStorage, configuration_writer_preflight)
from .strategy_configuration_reader import _select, read_configuration_selection
from .configuration_execution_binding_publication import ConfigurationExecutionBindingPublisher
from .qmd_columnar_history_transport import _owned_worker
from src.market_engine.typed_product_insert import insert_product_rows
from src.trading_runtime.configuration_execution_binding import POLICY, SEAL, TABLES, restore_execution_binding
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.keeper_session import ManagedKeeperSession

_ROOT = '/quant-research-workbench/trading/configuration-execution-v1'


class ClickHouseExecutionBindingStorage(ClickHouseConfigurationStorage):
    restore = staticmethod(restore_execution_binding)
    tables = TABLES
    seal = SEAL
    children = (POLICY,)
    root = _ROOT
    writer_requirements = {'require_execution_bindings': True}
    def __init__(self, reader, reader_factory, writer_factory, session, selected, concurrency):
        if (type(session) is not ManagedKeeperSession or not callable(reader_factory)
                or not callable(writer_factory) or type(concurrency) is not int or not 1 <= concurrency <= 6
                or type(getattr(reader, 'base_url', None)) is not str or not reader.base_url):
            raise ValueError('Execution publication lacks managed bounded authorities')
        self.selected = self.restore(selected.rows)
        self.reader, self.reader_factory, self.writer_factory = reader, reader_factory, writer_factory
        self.session, self.keeper = session, session.client
        self.generation, self.client_id = session._generation, session.client.client_id
        self.concurrency = concurrency
        self.path = self.root + '/' + selected.header['configuration_id']
        self.claim = self.creation_id = None
        self.committed = False
        self.connections, self.lock = [reader], Lock()

    def preflight(self):
        if self.reader.execute("SELECT getSetting('readonly')").strip() != '1':
            raise ValueError('Execution publication reader must be SELECT-only')
        storage_preflight(self.reader, tables=self.tables)
        writer = self._owned(self.writer_factory)
        try:
            configuration_writer_preflight(writer, **self.writer_requirements)
        finally:
            writer.close()
        self._session()
        digest = self.selected.header['content_hash']
        started, committed = f'1\nstarted\n{digest}'.encode(), f'1\ncommitted\n{digest}'.encode()
        existing = self.read_selection(self.selected.header['configuration_id'])
        if existing is not None:
            if self.restore(existing).header['content_hash'] != digest:
                raise ValueError('Execution identity already has another immutable binding')
            value, stat = self.keeper.get(self.path)
            if value != committed:
                raise RuntimeError('Execution seal lacks committed ownership; reconcile explicitly')
            self.claim, self.committed = committed, True
        else:
            self.keeper.ensure_path(self.root)
            self._session()
            try:
                self.keeper.create(self.path, started, ephemeral=False)
            except Exception as exc:
                raise RuntimeError('Execution claim exists or acknowledgement is ambiguous; reconcile explicitly') from exc
            value, stat = self.keeper.get(self.path)
            if value != started:
                raise RuntimeError('Execution claim changed after creation')
            self.claim = started
        self.creation_id = stat.czxid
        if type(self.creation_id) is not int or self.creation_id < 0:
            raise RuntimeError('Execution claim lacks creation identity')
        self.assert_owner()

    def verify_references(self, selected):
        self.assert_owner()
        configuration = read_configuration_selection(self.reader_factory,
            configuration_id=selected.header['configuration_id'],
            expected_content_hash=selected.header['configuration_hash'], concurrency=self.concurrency)
        if configuration.header['oms_limit_offset_bps'] != selected.header['limit_offset_bps']:
            raise ValueError('Execution limit offset differs from the central configuration')
        self.assert_owner()

    def read_children(self, identity):
        self._identity(identity)
        client = self._owned(self.reader_factory)
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Execution child reader must be SELECT-only')
            return {table.name: _select(client, table, identity) for table in self.children}
        finally:
            client.close()

    def read_selection(self, identity):
        self._identity(identity)
        head = _select(self.reader, self.seal, identity)
        return {self.seal.name: head, **self.read_children(identity)} if head else None

    def _insert(self, table, rows):
        self.assert_owner()
        if self.committed or rows != self.selected.rows[table.name]:
            raise ValueError('Execution insert differs from its immutable publication')
        client = self._owned(self.writer_factory)
        try:
            configuration_writer_preflight(client, **self.writer_requirements)
            self.assert_owner()
            insert_product_rows(client, table, [dict(row) for row in rows])
            self.assert_owner()
        finally:
            client.close()

    def insert_child(self, name, rows):
        if name != POLICY.name:
            raise ValueError('Execution insert escaped its shared policy family')
        self._insert(POLICY, rows)

    def insert_seal(self, rows):
        self._insert(self.seal, rows)


async def publish_configuration_execution_binding(*, reader_factory, writer_factory, session,
        source, concurrency):
    selected = restore_execution_binding(source)
    def run():
        reader = reader_factory()
        try:
            storage = ClickHouseExecutionBindingStorage(reader, reader_factory, writer_factory,
                session, selected, concurrency)
            result = ConfigurationExecutionBindingPublisher(storage, concurrency=concurrency)._publish(selected)
            storage.mark_committed()
            return result
        finally:
            reader.close()
    return await _owned_worker(run)


from src.trading_runtime.configuration_execution_risk import RISK, restore_execution_risk
from .configuration_execution_binding_publication import ConfigurationExecutionRiskPublisher


class ClickHouseExecutionRiskStorage(ClickHouseExecutionBindingStorage):
    restore = staticmethod(restore_execution_risk)
    tables = (RISK,)
    seal = RISK
    children = ()
    root = '/quant-research-workbench/trading/configuration-execution-risk-v1'
    writer_requirements = {'require_execution_risk': True}

    def verify_references(self, selected):
        self.assert_owner()
        read_configuration_selection(self.reader_factory,
            configuration_id=selected.header['configuration_id'],
            expected_content_hash=selected.header['configuration_hash'], concurrency=self.concurrency)
        self.assert_owner()

    def insert_child(self, name, rows):
        raise ValueError('Execution risk has no child row families')


async def publish_configuration_execution_risk(*, reader_factory, writer_factory, session,
        source, concurrency):
    """Explicit operator publication; never called by app selection or startup."""
    selected = restore_execution_risk(source)
    def run():
        reader = reader_factory()
        try:
            storage = ClickHouseExecutionRiskStorage(reader, reader_factory, writer_factory,
                session, selected, concurrency)
            result = ConfigurationExecutionRiskPublisher(storage, concurrency=concurrency)._publish(selected)
            storage.mark_committed()
            return result
        finally:
            reader.close()
    return await _owned_worker(run)
