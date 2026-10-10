"""Issued immutable publication images; raw rows confer no writer authority."""
from dataclasses import dataclass
from weakref import WeakKeyDictionary
from uuid import UUID
import json

from .journal_contract import canonical_json
from .arte_journal_writer import verify_typed_contract_row, _wire_row, _CONTRACTS
from .native_channel_manager_snapshot_schema import parent_contract
from .arte_native_channel_pending_schema import TABLES as CHANNEL_TABLES
from .strategy_one_management_snapshot import SOURCE, BREAK, HIGH, CLOSED, FIRST_HELD
from .strategy_one_protection_snapshot import TABLES as PROTECTION_TABLES
from .original_risk_pending_snapshot import PENDING as ORIGINAL_PENDING


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class NativeChannelManagerPublication:
    run_id: str
    sequence: int
    batch_id: str
    snapshot_hash: str
    rows_json: str


_ISSUED = WeakKeyDictionary()


def publication_contracts():
    return (PROTECTION_TABLES[1], PROTECTION_TABLES[2], PROTECTION_TABLES[0], SOURCE, BREAK, HIGH, CLOSED, FIRST_HELD,
            ORIGINAL_PENDING, *CHANNEL_TABLES, parent_contract())


def issue_native_channel_manager_publication(client, rows, *, policy, contexts,
        journal_batch_id, first_price_source=None):
    """Reprove pending facts and original entries at the actual writer frontier."""
    from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot
    from .strategy_one_management_snapshot import restore_manager_snapshot, attach_committed_momentum_sources
    from .arte_journal_commit_v4 import load_writer_v4_snapshot_prefix
    from .arte_journal_projection import load_latest_backtest_cursor
    from .native_channel_writer_profile import validate_channel_writer_profile
    validate_channel_writer_profile(policy,
        risk_policy=getattr(client, 'confirmed_original_risk_policy', None),
        entry_spread_risk=getattr(client, 'entry_spread_risk_profile', False),
        automatic_ladder=getattr(client, 'automatic_ladder_profile', False),
        ladder_geometry_policy=getattr(client, 'ladder_geometry_policy', None),
        fixed_structural_lot_profile=getattr(client, 'fixed_structural_lot_profile', None))
    if (getattr(client, 'native_channel_management_policy', None) != policy
            or policy is None or str(UUID(journal_batch_id)) != journal_batch_id
            or UUID(journal_batch_id).int == 0):
        raise ValueError('Publication requires declared client policy and canonical nonzero batch')
    reprove_native_channel_manager_snapshot(rows, policy, contexts)
    root, inherited = rows.snapshot, rows.inherited
    source = {} if first_price_source is None else {'first_price_source': first_price_source}
    prefix = load_writer_v4_snapshot_prefix(client, root['run_id'], **source)
    if (prefix is None or prefix.status != 'running' or prefix.last_sequence != root['checkpoint_sequence']
            or prefix.last_batch_id != journal_batch_id):
        raise ValueError('Publication lacks the exact running journal frontier')
    attach_committed_momentum_sources(client, prefix, restore_manager_snapshot(inherited), **source)
    cursor = load_latest_backtest_cursor(client, prefix)
    expected = dict(run_id=root['run_id'], event_sequence=root['checkpoint_sequence'],
        batch_id=journal_batch_id, boundary_ms=root['boundary_ms'], session_date=root['session_date'])
    if not isinstance(cursor, dict) or any(cursor.get(key) != value for key, value in expected.items()):
        raise ValueError('Publication market cursor differs from complete manager capture')
    if any(header['batch_id'] != journal_batch_id for header in rows.pending):
        raise ValueError('Pending publication facts belong to another journal batch')
    protection = inherited.protection
    families = (protection.states, protection.resistances, (protection.snapshot,),
        inherited.sources, inherited.pending_breaks, inherited.position_highs, inherited.closed_positions,
        inherited.first_held_boundaries, inherited.original_risk_requests,
        rows.pending, rows.sources, rows.executions, (root,))
    wire = {contract.name: [(_wire_row(contract.name, row) if contract.name in _CONTRACTS
                            else verify_typed_contract_row(contract, row)) for row in family]
            for contract, family in zip(publication_contracts(), families)}
    image = NativeChannelManagerPublication(root['run_id'], root['checkpoint_sequence'],
        journal_batch_id, root['content_hash'], canonical_json(wire))
    _ISSUED[image] = (client, policy, image.run_id, image.sequence, image.batch_id,
                      image.snapshot_hash, image.rows_json)
    return image


def require_native_channel_manager_publication(image, *, client):
    if type(image) is not NativeChannelManagerPublication:
        raise ValueError('Exact independently issued manager publication required')
    binding = _ISSUED.get(image)
    if (binding is None or binding[0] is not client
            or binding[1] != getattr(client, 'native_channel_management_policy', None)
            or binding[2:] != (image.run_id, image.sequence, image.batch_id, image.snapshot_hash, image.rows_json)):
        raise ValueError('Manager publication is unissued, changed or belongs to another client')
    return json.loads(image.rows_json)


def verify_native_channel_manager_insert(image, *, client, table, rows,
        sequence, batch_id, snapshot_hash):
    """Bind one complete family to its issued immutable publication image."""
    families = require_native_channel_manager_publication(image, client=client)
    contracts = {contract.name: contract for contract in publication_contracts()}
    if (table not in families or (sequence, batch_id, snapshot_hash) !=
            (image.sequence, image.batch_id, image.snapshot_hash)):
        raise ValueError('Manager INSERT differs from issued family or journal frontier')
    if type(rows) is not tuple or canonical_json(list(rows)) != canonical_json(families[table]):
        raise ValueError('Manager INSERT differs from complete frozen family')
    return contracts[table], rows


def publish_native_channel_manager_publication(client, session, image):
    """Write children/root, verify all families, then CAS the selected head.

    Lost INSERT responses leave pending dispatch operations. No head is
    selected from orphan rows, partial families or an unacknowledged insert.
    """
    from .keeper_session import ManagedKeeperSession
    from .arte_typed_insert_dispatch import TypedInsertDispatch
    from .strategy_one_management_snapshot import ManagedManagerSnapshotHeadReader
    from .arte_journal_writer import _insert, _literal
    from src.backend.backtest_market_data import assert_select_only
    families = require_native_channel_manager_publication(image, client=client)
    dispatch = getattr(client, 'typed_insert_dispatch', None)
    if (not isinstance(session, ManagedKeeperSession) or not session.writable
            or getattr(client, 'typed_insert_strict', False) is not True
            or not isinstance(dispatch, TypedInsertDispatch) or dispatch.keeper is not session.client):
        raise RuntimeError('Channel manager publication lacks its fenced writer session')
    reader = ManagedManagerSnapshotHeadReader(session)
    previous = (None if session.client.exists(reader.path(image.run_id)) is None
                else reader.read_head(run_id=image.run_id))
    repeat = previous is not None and previous.checkpoint_sequence == image.sequence
    if previous is not None and (previous.checkpoint_sequence > image.sequence
            or (repeat and (previous.journal_batch_id != image.batch_id
                            or previous.snapshot_hash != image.snapshot_hash))):
        raise RuntimeError('Channel manager publication conflicts with the selected head')
    operations = []
    if not repeat:
        for contract in publication_contracts():
            rows = tuple(families[contract.name])
            if not rows:
                continue
            token = f'manager-state:{image.run_id}:{image.sequence}:{image.snapshot_hash}:{contract.name}'
            _insert(client, contract.name, rows, token, journal_profile='backtest_v4',
                dispatch_sequence=image.sequence, dispatch_batch_id=image.batch_id,
                dispatch_manager_snapshot_hash=image.snapshot_hash, dispatch_native_channel_manager_image=image)
            operations.append((contract.name, token))
    snapshot_id = families[parent_contract().name][0]['snapshot_id']
    channel_names = {contract.name for contract in CHANNEL_TABLES}
    for contract in publication_contracts():
        expected = families[contract.name]
        key = 'parent_record_id' if contract.name in channel_names else 'snapshot_id'
        projection = ','.join(f'toString({name}) AS {name}' if 'Decimal(' in kind else name
                              for name, kind in contract.columns)
        sql = assert_select_only(f'SELECT {projection} FROM arte.{contract.name} '
            f'WHERE run_id={_literal(image.run_id)} AND {key}=toUUID({_literal(snapshot_id)}) '
            f'LIMIT {len(expected)+1} FORMAT JSONEachRow')
        observed = [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
        canonical = [(_wire_row(contract.name, row) if contract.name in _CONTRACTS
                      else verify_typed_contract_row(contract, row, stored_utc=True)) for row in observed]
        if len(canonical) != len(expected) or sorted(map(canonical_json, canonical)) != sorted(map(canonical_json, expected)):
            raise RuntimeError(f'Channel manager publication readback differs: {contract.name}')
    if repeat:
        if reader.read_head(run_id=image.run_id) != previous:
            raise RuntimeError('Channel manager selected head changed during repeat verification')
        return previous
    for table, token in operations:
        dispatch.seal_verified_operation(run_id=image.run_id, table=table, token=token,
            batch_id=image.batch_id, batch_last_sequence=image.sequence, manager_snapshot=True)
    dispatch.compact_verified_manager_snapshot(run_id=image.run_id, batch_id=image.batch_id,
        last_sequence=image.sequence, snapshot_hash=image.snapshot_hash, operations=tuple(operations),
        previous=previous, confirmed_original_risk_policy=client.confirmed_original_risk_policy,
        native_channel_management_policy=client.native_channel_management_policy)
    selected = reader.read_head(run_id=image.run_id)
    if (selected.checkpoint_sequence != image.sequence or selected.journal_batch_id != image.batch_id
            or selected.snapshot_hash != image.snapshot_hash):
        raise RuntimeError('Channel manager selected head differs after publication')
    return selected
