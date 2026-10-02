"""Prepared scalar binding after checkpoint hash and commit verification.

This does not certify a checkpoint or an entry commit. Writer/recovery callers
must first load those authorities through the existing native sealed readers.
"""
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from .strategy_one_stateful import StrategyOneEntryProposal, StrategyOneFinancialView
from .strategy_profit_giveback_exit import validate_profit_giveback_witness


def validate_profit_giveback_state(witness, state, financial) -> StrategyOneEntryProposal:
    """Bind the prior high, held clock and original risk to the exact position."""
    validate_profit_giveback_witness(witness)
    if type(state) is not StrategyOneManagementState or type(financial) is not StrategyOneFinancialView:
        raise ValueError('Profit source needs exact manager and financial types')
    if state.boundary_ms != witness.prior_high_through_boundary_ms:
        raise ValueError('Profit high differs from prior manager boundary')
    key = (financial.account_id, financial.assignment_id, financial.ticker)
    families = {}
    for name in ('submitted', 'positions', 'position_highs', 'first_held_boundaries'):
        rows = getattr(state, name)
        if len({k for k, _ in rows}) != len(rows):
            raise ValueError('Profit source repeats a manager position identity')
        families[name] = dict(rows)
        if key not in families[name]:
            raise ValueError('Profit source lacks exact held position identity')
    source = families['submitted'][key]
    if (type(source) is not StrategyOneEntryProposal or source.strategy_number not in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42)
            or (source.account_id, source.assignment_id, source.ticker) != key
            or source.reference_ask != witness.reference_ask
            or source.initial_stop != witness.initial_stop
            or not source.boundary_ms < witness.first_held_boundary_ms
            or families['first_held_boundaries'][key] != witness.first_held_boundary_ms
            or families['position_highs'][key] != witness.prior_high_int
            or families['positions'][key].boundary_ms > state.boundary_ms):
        raise ValueError('Profit source differs from original entry or checkpoint high')
    return source


def load_profit_giveback_checkpoint(client, prefix, row, financial, *, first_price_source=None):
    """Load a historical checkpoint from a verified prefix, never current head.

    Required native Strategy 31 readers/routing are a separate integration
    gate. This function does not replace prefix verification or attest Keeper.
    """
    from dataclasses import replace
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_journal_projection import load_latest_backtest_cursor
    from .strategy_one_management_snapshot import (
        load_unattested_manager_snapshot_rows, restore_manager_snapshot,
        attach_committed_momentum_sources,
    )
    from .arte_profit_giveback_v4 import restore_profit_giveback
    from .arte_followthrough_failure_v4 import _source_entry
    witness = restore_profit_giveback(row)
    sequence = row['source_manager_checkpoint_sequence']
    if (type(prefix) is not V4CommittedPrefix or prefix.run_id != row['run_id']
            or not prefix.batch_ids or prefix.last_batch_id != prefix.batch_ids[-1]
            or row['assignment_id'] != financial.assignment_id
            or type(sequence) is not int or not 0 < sequence <= prefix.last_sequence):
        raise ValueError('Profit checkpoint lacks its verified committed prefix')
    # The full prefix is already verified. Restrict the existing cursor reader
    # to the referenced sequence without treating the synthetic ceiling as a
    # new execution/recovery admission or a Keeper-selected head.
    ceiling = replace(prefix, last_sequence=sequence)
    cursor = load_latest_backtest_cursor(client, ceiling)
    if (not isinstance(cursor, dict) or cursor.get('event_sequence') != sequence
            or cursor.get('run_id') != prefix.run_id
            or str(cursor.get('batch_id')) not in prefix.batch_ids
            or cursor.get('boundary_ms') != witness.prior_high_through_boundary_ms):
        raise ValueError('Profit checkpoint differs from committed market cursor')
    rows = load_unattested_manager_snapshot_rows(
        client, run_id=prefix.run_id, checkpoint_sequence=sequence)
    if (rows.snapshot.get('snapshot_id') != row['source_manager_snapshot_id']
            or rows.snapshot.get('run_id') != prefix.run_id
            or rows.snapshot.get('checkpoint_sequence') != sequence
            or rows.snapshot.get('boundary_ms') != cursor['boundary_ms']
            or rows.snapshot.get('session_date') != cursor['session_date']):
        raise ValueError('Profit checkpoint snapshot differs from committed cursor')
    state = attach_committed_momentum_sources(
        client, prefix, restore_manager_snapshot(rows), first_price_source=first_price_source)
    source = validate_profit_giveback_state(witness, state, financial)
    entry, event, child = _source_entry(
        client, prefix.run_id, str(row['source_entry_intent_id']),
        prior_batch_id=str(cursor['batch_id']), exit_batch_id=str(row['batch_id']),
        verified_prefix=prefix, first_price_source=first_price_source)
    if (entry['action'] != 'enter_long' or entry['reason'] != 'strategy_one_entry'
            or str(entry['batch_id']) not in prefix.batch_ids
            or entry['ticker'] != financial.ticker or event['account_id'] != financial.account_id
            or child['strategy_number'] != row['strategy_number']
            or source.strategy_number != row['strategy_number']
            or child['assignment_id'] != financial.assignment_id
            or child['boundary_ms'] != source.boundary_ms
            or float(entry['reference_price']) != witness.reference_ask
            or float(entry['invalidation_price']) != witness.initial_stop
            or event['sequence'] >= sequence):
        raise ValueError('Profit checkpoint differs from committed source entry')
    return state
