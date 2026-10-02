"""Historical manager reference verification for the prepared liquidity exit.

First-held authority is read from its exact persisted checkpoint, never from
fill timestamps or current Keeper state. Broker quantity and pending orders
remain separate verification obligations; this module does not attest them.
"""
from dataclasses import replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .arte_liquidity_fade_failure_v4 import restore_liquidity_fade_failure
from .strategy_liquidity_fade_source import validate_liquidity_fade_state
from .strategy_liquidity_fade_entry_source import validate_liquidity_fade_entry_source


def load_liquidity_fade_manager_checkpoint(client, prefix, row, parent, event, financial,
                                         *, first_price_source=None):
    """Require this decision's committed cursor, snapshot hash and entry source.

    The prefix must already be independently verified. This is cold/worker
    verification, not a query per 100ms decision. The caller must independently
    verify the financial argument against native broker/OMS authority before
    treating this result as publication or recovery evidence.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_journal_projection import load_latest_backtest_cursor
    from .strategy_one_management_snapshot import load_unattested_manager_snapshot_rows, restore_manager_snapshot
    witness = restore_liquidity_fade_failure(row)
    sequence = row['source_manager_checkpoint_sequence']
    if (type(prefix) is not V4CommittedPrefix or prefix.status != 'running'
            or prefix.run_id != row['run_id'] or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]
            or not sequence <= prefix.last_sequence
            or type(event['sequence']) is not int or not sequence < event['sequence']):
        raise ValueError('Liquidity manager reference lacks its independently verified preceding prefix')
    instant = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    day = instant.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    # This historical ceiling is a lookup scope only, not a new journal head,
    # execution lease, prefix attestation or permission to resume an actor.
    cursor = load_latest_backtest_cursor(client, replace(prefix, last_sequence=sequence))
    if (not isinstance(cursor, dict) or cursor.get('event_sequence') != sequence
            or cursor.get('run_id') != prefix.run_id
            or str(cursor.get('batch_id')) not in prefix.batch_ids
            or cursor.get('boundary_ms') != witness.boundary_ms
            or cursor.get('session_date') != day):
        raise ValueError('Liquidity manager reference differs from its exact committed decision cursor')
    rows = load_unattested_manager_snapshot_rows(client, run_id=prefix.run_id, checkpoint_sequence=sequence)
    seal = rows.snapshot
    if (str(seal.get('snapshot_id')) != row['source_manager_snapshot_id']
            or seal.get('content_hash') != row['source_manager_snapshot_hash']
            or seal.get('run_id') != prefix.run_id or seal.get('checkpoint_sequence') != sequence
            or seal.get('boundary_ms') != witness.boundary_ms or seal.get('session_date') != day):
        raise ValueError('Liquidity manager snapshot differs from its immutable reference')
    state = restore_manager_snapshot(rows)
    source = validate_liquidity_fade_state(witness, state, financial)
    if float(parent['quantity']) != financial.position_quantity:
        raise ValueError('Liquidity exit quantity differs from its independently verified financial view')
    validate_liquidity_fade_entry_source(client, row, parent, event, verified_prefix=prefix,
        first_price_source=first_price_source, manager_source=source)
    return state
