"""Exit-only financial verification from native broker and committed OMS rows.

This cold/worker path does not infer positions from prices, a strategy copy or
an exit's claimed quantity. It neither grants order admission nor attests the
view's permissions, pending acquisitions or completed-entry counters.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .arte_liquidity_fade_failure_v4 import restore_liquidity_fade_failure
from .strategy_liquidity_fade_exit import liquidity_fade_reason, validate_liquidity_fade_financial
from .strategy_one_contract import STRATEGY_ID


@dataclass(frozen=True, slots=True)
class LiquidityFadeFinancialCheckpoint:
    """Native exit preconditions verified at one historical committed cursor."""
    run_id: str
    checkpoint_sequence: int
    boundary_ms: int
    broker_snapshot_id: str
    broker_snapshot_hash: str
    account_id: str
    assignment_id: str
    ticker: str
    conid: int
    held_quantity: float


def load_liquidity_fade_financial_checkpoint(client, prefix, row, parent, event, financial,
                                           *, first_price_source=None, original_risk_diagnostic=None):
    """Verify exact held quantity and absence of pending assignment exits.

    Requires an independently verified preceding V4 prefix. Native context,
    cursor, broker root/children and full OMS lineage are verified by existing
    bounded readers. No database query belongs in the 100ms decision loop.
    Manager first-held and producer observations remain separate mandatory
    checks. The installed Strategy 35 OMS gate remains closed until integration.
    """
    from src.backend.backtest_market_data import market_day_boundary
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_journal_writer import load_typed_run_context
    from .arte_journal_projection import load_latest_backtest_cursor
    from .strategy_one_broker_match_snapshot import (
        load_unattested_broker_match_snapshot, verify_broker_match_snapshot,
    )
    from .arte_oms_projection import load_recovered_strategy_one_oms_lineage

    if original_risk_diagnostic is None:
        witness = restore_liquidity_fade_failure(row)
        expected_reason = liquidity_fade_reason(row['strategy_number'])
    else:
        from .arte_followthrough_failure_v4 import restore_failure, REASON
        from .arte_original_risk_diagnostic_v4 import diagnostic_policy
        from .confirmed_original_risk_failure import OriginalRiskDecisionDiagnostic
        if (type(original_risk_diagnostic) is not OriginalRiskDecisionDiagnostic
                or diagnostic_policy(row['strategy_number']) is None):
            raise ValueError('Native financial checkpoint lacks selected typed original-risk capability')
        witness = restore_failure(row,diagnostic=original_risk_diagnostic)
        expected_reason = REASON
    validate_liquidity_fade_financial(financial)
    sequence = row['source_manager_checkpoint_sequence']
    if (type(prefix) is not V4CommittedPrefix or prefix.status != 'running'
            or prefix.run_id != row['run_id'] or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]
            or type(event['sequence']) is not int
            or not sequence <= prefix.last_sequence < event['sequence']
            or any(value['run_id'] != row['run_id'] or value['batch_id'] != row['batch_id']
                   for value in (parent, event))
            or parent['record_id'] != row['parent_record_id'] or event['record_id'] != parent['record_id']
            or parent['account_id'] != event['account_id'] or financial.account_id != event['account_id']
            or financial.assignment_id != row['assignment_id'] or financial.ticker != parent['ticker']
            or parent['action'] != 'exit' or parent['reason'] != expected_reason):
        raise ValueError('Liquidity financial checkpoint lacks its exact preceding exit graph')
    at = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    day = at.astimezone(ZoneInfo('America/New_York')).date()
    if market_day_boundary(day, witness.boundary_ms).astimezone(timezone.utc) != at.astimezone(timezone.utc):
        raise ValueError('Liquidity financial event differs from its decision clock')
    context = load_typed_run_context(client, prefix.run_id)
    accounts = context.get('account_ids')
    if (context.get('run_id') != prefix.run_id or context.get('mode') != 'backtest'
            or context.get('strategy_id') != STRATEGY_ID
            or type(context.get('strategy_revision')) is not int
            or context['strategy_revision'] != row['strategy_number']
            or context.get('evaluation_interval_ms') != 100 or context.get('session_date') != day.isoformat()
            or type(accounts) is not tuple or not 0 < len(accounts) <= 65_535
            or any(type(account) is not str or not account for account in accounts)
            or len(set(accounts)) != len(accounts) or financial.account_id not in accounts):
        raise ValueError('Liquidity financial checkpoint differs from its pinned Backtest context')
    ceiling = replace(prefix, last_sequence=sequence)
    cursor = load_latest_backtest_cursor(client, ceiling)
    if (not isinstance(cursor, dict) or cursor.get('event_sequence') != sequence
            or cursor.get('run_id') != prefix.run_id or str(cursor.get('batch_id')) not in prefix.batch_ids
            or cursor.get('boundary_ms') != witness.boundary_ms or cursor.get('session_date') != day.isoformat()):
        raise ValueError('Liquidity financial checkpoint differs from its exact committed cursor')
    image = verify_broker_match_snapshot(load_unattested_broker_match_snapshot(
        client, run_id=prefix.run_id, checkpoint_sequence=sequence))
    root = image.snapshot
    if (root['snapshot_id'] != row['source_broker_snapshot_id']
            or root['content_hash'] != row['source_broker_snapshot_hash']
            or root['run_id'] != prefix.run_id or root['checkpoint_sequence'] != sequence
            or root['session_date'] != day.isoformat() or root['boundary_ms'] != witness.boundary_ms
            or {item['account_id'] for item in image.accounts} != set(accounts)):
        raise ValueError('Liquidity broker snapshot differs from its immutable checkpoint reference')
    from .selected_checkpoint_products import source_for_client,load_historical_checkpoint
    selected_source=source_for_client(client)
    selected_image=(load_historical_checkpoint(client,prefix,source=selected_source,sequence=sequence)
        if selected_source is not None else None)
    if selected_image is not None:
        ceiling=selected_image.prefix
    lineage = load_recovered_strategy_one_oms_lineage(client, ceiling,
        allowed_accounts=frozenset(accounts), strategy_number=row['strategy_number'], first_price_source=first_price_source,
        **({'fixed_lot_checkpoint':selected_image} if selected_image is not None else {}))
    from .native_held_oms_checkpoint import verify_native_held_oms_position
    conid, quantity = verify_native_held_oms_position(prefix, sequence, image, lineage,
        accounts=accounts, strategy_number=row['strategy_number'], financial=financial,
        source_entry_intent_id=row['source_entry_intent_id'], expected_exit_quantity=float(parent['quantity']))
    return LiquidityFadeFinancialCheckpoint(prefix.run_id, sequence, witness.boundary_ms,
        root['snapshot_id'], root['content_hash'], financial.account_id, financial.assignment_id,
        financial.ticker, conid, quantity)
