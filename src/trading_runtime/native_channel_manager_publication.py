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
