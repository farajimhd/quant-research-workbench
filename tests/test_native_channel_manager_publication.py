from dataclasses import replace
from types import SimpleNamespace
import json
import re

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_manager_recovery import manager_case
from tests.test_native_channel_manager_reader import Reader
from src.trading_runtime.native_channel_manager_publication import (
    issue_native_channel_manager_publication, require_native_channel_manager_publication,
    publication_contracts,
)


def issuance_case(installed, monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4, arte_journal_projection
    from src.trading_runtime import strategy_one_management_snapshot as manager
    rows, policy, contexts = manager_case(installed)
    root, batch = rows.snapshot, rows.pending[0]['batch_id']
    prefix = SimpleNamespace(status='running', last_sequence=root['checkpoint_sequence'], last_batch_id=batch)
    cursor = dict(run_id=root['run_id'], event_sequence=root['checkpoint_sequence'], batch_id=batch,
        boundary_ms=root['boundary_ms'], session_date=root['session_date'])
    monkeypatch.setattr(arte_journal_commit_v4, 'load_writer_v4_snapshot_prefix', lambda *_a, **_k: prefix)
    monkeypatch.setattr(arte_journal_projection, 'load_latest_backtest_cursor', lambda *_a: cursor)
    attached = []
    monkeypatch.setattr(manager, 'attach_committed_momentum_sources',
                        lambda _c, _p, state, **_kw: attached.append(state))
    return Reader(rows, policy), rows, policy, contexts, batch, prefix, cursor, attached


def test_issued_image_is_complete_root_last_client_bound_and_immutable(installed, monkeypatch):
    client, rows, policy, contexts, batch, _, _, attached = issuance_case(installed, monkeypatch)
    image = issue_native_channel_manager_publication(client, rows, policy=policy, contexts=contexts,
        journal_batch_id=batch)
    saved = require_native_channel_manager_publication(image, client=client)
    assert set(saved) == {t.name for t in publication_contracts()}
    assert publication_contracts()[-1].name == 'trading_strategy_one_manager_snapshot_v5'
    assert attached and len(saved['strategy_native_channel_pending_v1']) == 1
    saved['strategy_native_channel_pending_v1'].clear()
    assert len(require_native_channel_manager_publication(image, client=client)['strategy_native_channel_pending_v1']) == 1
    with pytest.raises(ValueError): require_native_channel_manager_publication(image, client=Reader(rows, policy))
    with pytest.raises(ValueError): require_native_channel_manager_publication(replace(image), client=client)
    with pytest.raises(ValueError): require_native_channel_manager_publication(replace(image, sequence=image.sequence+1), client=client)


@pytest.mark.parametrize('defect', ['prefix', 'cursor', 'evidence', 'batch', 'policy'])
def test_issuance_requires_full_independent_evidence_at_actual_frontier(installed, monkeypatch, defect):
    client, rows, policy, contexts, batch, prefix, cursor, _ = issuance_case(installed, monkeypatch)
    if defect == 'prefix': prefix.last_sequence += 1
    if defect == 'cursor': cursor['boundary_ms'] += 100
    if defect == 'evidence': contexts = {}
    if defect == 'batch': batch = '88888888-8888-4888-8888-888888888888'
    if defect == 'policy': client.native_channel_management_policy = None
    with pytest.raises(ValueError):
        issue_native_channel_manager_publication(client, rows, policy=policy, contexts=contexts, journal_batch_id=batch)


def test_real_insert_encoder_accepts_only_complete_issued_families(installed, monkeypatch):
    from src.trading_runtime.arte_journal_writer import _insert, _CONTRACTS
    client, rows, policy, contexts, batch, _, _, _ = issuance_case(installed, monkeypatch)
    image = issue_native_channel_manager_publication(client, rows, policy=policy, contexts=contexts,
        journal_batch_id=batch)
    families = require_native_channel_manager_publication(image, client=client)
    calls = []

    class Dispatch:
        def execute_typed_insert(self, _client, **kwargs):
            calls.append(kwargs)
    # This test exercises the real encoder/admission seam; Keeper CAS is
    # covered by the separate typed-dispatch suite, not this transport double.
    client.typed_insert_dispatch = Dispatch()
    client.typed_insert_strict = True
    kwargs = dict(journal_profile='backtest_v4', dispatch_sequence=image.sequence,
        dispatch_batch_id=batch, dispatch_manager_snapshot_hash=image.snapshot_hash,
        dispatch_native_channel_manager_image=image)
    for contract in publication_contracts():
        family = tuple(families[contract.name])
        token = f'manager-state:{image.run_id}:{image.sequence}:{image.snapshot_hash}:{contract.name}'
        _insert(client, contract.name, family, token, **kwargs)
    assert calls[-1]['table'] == 'trading_strategy_one_manager_snapshot_v5'
    assert calls[-1]['table'] not in _CONTRACTS
    assert all(call['batch_last_sequence'] == image.sequence for call in calls)
    table = 'strategy_native_channel_pending_v1'
    with pytest.raises(ValueError): _insert(client, table, tuple(families[table]), 'foreign')
    before = len(calls)
    with pytest.raises(ValueError): _insert(client, table, (), 'foreign', **kwargs)
    with pytest.raises(ValueError):
        _insert(client, table, tuple(families[table]), 'foreign',
            **{**kwargs, 'dispatch_sequence': image.sequence + 1})
    forged = ({**families[table][0], 'ask_int': families[table][0]['ask_int'] + 1},)
    with pytest.raises(ValueError): _insert(client, table, forged, 'foreign', **kwargs)
    assert len(calls) == before


@pytest.mark.parametrize('defect', [None, 'missing', 'extra', 'lost_response'])
def test_publication_real_keeper_dispatch_selects_only_full_readback(installed, monkeypatch, defect):
    from tests.test_arte_typed_insert_dispatch import Keeper, Stat
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch, _Gate, _gate_path, _context_receipt_path
    from src.trading_runtime.strategy_one_management_snapshot import ManagedManagerSnapshotHeadReader
    from src.trading_runtime.native_channel_manager_publication import publish_native_channel_manager_publication
    client, rows, policy, contexts, batch, _, _, _ = issuance_case(installed, monkeypatch)
    image = issue_native_channel_manager_publication(client, rows, policy=policy, contexts=contexts,
        journal_batch_id=batch)
    keeper = Keeper()
    keeper.add_listener = lambda _listener: None
    keeper.connected, keeper.client_id = True, (101, b'synthetic')
    keeper.exists = lambda path: keeper.rows.get(path)
    session = ManagedKeeperSession(keeper)
    session._on_state('CONNECTED')
    dispatch = TypedInsertDispatch(keeper)
    dispatch.initialize_new_run(image.run_id)
    keeper.create(_context_receipt_path(image.run_id), b'1\n' + b'a' * 64)
    gate, version = dispatch._read_gate(image.run_id)
    keeper.rows[_gate_path(image.run_id)] = (_Gate('open', 0, gate.epoch, 0,
        image.sequence, batch, 'a' * 64, '00000000-0000-0000-0000-000000000000').wire(), Stat(version+1))
    tables, writes = {}, []
    frozen = require_native_channel_manager_publication(image, client=client)
    contracts = {contract.name: contract for contract in publication_contracts()}

    def execute(sql, *, query_id=None):
        header = sql.split(b'\n', 1)[0].decode() if isinstance(sql, bytes) else sql.split('\n', 1)[0]
        table = re.search(r'(?:INTO|FROM) arte\.([a-z0-9_]+)', header)[1]
        if header.startswith('INSERT '):
            if isinstance(sql, bytes):
                from src.trading_runtime.arte_journal_rowbinary import encode_journal_rows
                assert 'FORMAT RowBinary' in header
                assert sql.split(b'\n', 1)[1] == encode_journal_rows(contracts[table].columns, tuple(frozen[table]))
                # The in-memory transport stores the independently frozen
                # expected rows; binary-format bits have their own codec tests.
                inserted = frozen[table]
            else:
                assert 'FORMAT JSONEachRow' in sql
                inserted = [json.loads(line) for line in sql.split('\n', 1)[1].splitlines()]
            tables.setdefault(table, []).extend(inserted)
            writes.append(table)
            if defect == 'lost_response' and table.endswith('snapshot_v5'):
                raise RuntimeError('Controlled lost root INSERT response')
            return ''
        assert header.startswith('SELECT ')
        observed = tables.get(table, [])
        if table == 'strategy_native_channel_pending_source_v1':
            if defect == 'missing': observed = []
            if defect == 'extra': observed = observed * 2
        return '\n'.join(json.dumps(row) for row in observed)

    client.execute = execute
    client.typed_insert_strict, client.typed_insert_dispatch = True, dispatch
    path = ManagedManagerSnapshotHeadReader.path(image.run_id)
    if defect is None:
        selected = publish_native_channel_manager_publication(client, session, image)
        assert selected.snapshot_hash == image.snapshot_hash
        assert writes[-1] == 'trading_strategy_one_manager_snapshot_v5'
        assert dispatch._read_gate(image.run_id)[0].registered == 0
        before = tuple(writes)
        assert publish_native_channel_manager_publication(client, session, image) == selected
        assert tuple(writes) == before
        from src.trading_runtime import arte_journal_writer as writer
        monkeypatch.setattr(writer, '_v4_preflight', lambda _client: None)
        monkeypatch.setattr(writer, '_verify_run_identity', lambda _client, _run:
                            dict(mode='backtest', account_ids=(rows.pending[0]['account_id'],)))
        client.manager_keeper_session = session
        journal = writer.ArteJournalWriter(client, run_id=image.run_id,
                                          journal_profile='backtest_v4', coalesce_batches=False)
        try:
            journal._last_commit_id = batch  # Synthetic fixture's verified predecessor.
            assert journal.submit_native_channel_manager_publication(image).result(timeout=5) == batch
            assert journal.metrics()['publish_by_unit']['_ManagerSnapshotUnit']['units'] == 1
            assert tuple(writes) == before
            with pytest.raises(ValueError, match='complete issued'):
                journal.submit_manager_snapshot(session_date=None, checkpoint_sequence=image.sequence,
                    journal_batch_id=batch, state=None)
            with pytest.raises(ValueError): journal.submit_native_channel_manager_publication(replace(image))
        finally:
            journal.close()
    else:
        with pytest.raises(RuntimeError): publish_native_channel_manager_publication(client, session, image)
        assert path not in keeper.rows
        assert dispatch._read_gate(image.run_id)[0].registered > 0
