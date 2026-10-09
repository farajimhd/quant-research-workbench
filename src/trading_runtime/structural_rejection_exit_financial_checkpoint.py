"""Historical own-rule financial joins; no order or writer capability.

The shared native reader establishes original entry instrument, held Broker
quantity and absence of pending assignment exits. This selected wrapper also
checks its complete historical OMS observation and Portfolio checkpoint roots.
Own manager witness and certified market replay remain separate obligations.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from hashlib import sha256
import json
from uuid import UUID,NAMESPACE_URL,uuid5

from .arte_structural_rejection_exit_v1 import EXIT
from .profit_armed_structural_rejection_exit import REASON


@dataclass(frozen=True,slots=True)
class StructuralRejectionExitFinancialCheckpoint:
    run_id: str
    checkpoint_sequence: int
    boundary_ms: int
    account_id: str
    assignment_id: str
    ticker: str
    conid: int
    held_quantity: float
    broker_snapshot_hash: str
    oms_snapshot_hash: str
    portfolio_state_hash: str


def load_structural_rejection_exit_financial_checkpoint(client,prefix,row,parent,event,financial,*,first_price_source):
    """Read worker-side native truth; a supplied view is only a claim to check."""
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from .strategy_liquidity_fade_financial_checkpoint import _load_native_exit_financial_checkpoint
    from .strategy_one_oms_observation_snapshot import load_unattested_oms_observation_snapshot
    from .arte_portfolio_snapshot import load_portfolio_snapshot
    from .arte_journal_projection import load_latest_backtest_cursor
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from src.backend.backtest_market_data import market_day_boundary
    from dataclasses import replace
    profile=require_native_structural_rejection_profile(getattr(client,'structural_rejection_profile',None))
    owner=profile.owner
    if (type(prefix) is not V4CommittedPrefix or prefix.run_id!=owner.manager.runtime.run_id
            or first_price_source is not owner.price_authority
            or type(row) is not dict or set(row)!={name for name,_ in EXIT.columns}
            or row['strategy_number']!=owner.manager.contract.strategy_number
            or row['account_id']!=financial.account_id or row['ticker']!=financial.ticker
            or row['assignment_id']!=financial.assignment_id
            or row['intent_id']!=parent.get('intent_id') or parent.get('reason')!=REASON
            or row['reference_bid_int']/10000!=float(parent['reference_price'])
            or row['position_quantity']!=financial.position_quantity):
        raise ValueError('Structural rejection financial graph differs from its declared own source')
    content={key:value for key,value in row.items() if key!='content_hash'}
    if sha256(json.dumps(content,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()!=row['content_hash']:
        raise ValueError('Structural rejection exit complete row hash changed')
    if row['record_id']!=str(uuid5(NAMESPACE_URL,
            f'{row["run_id"]}:{row["parent_record_id"]}:structural-rejection-exit-v1')):
        raise ValueError('Structural rejection exit lacks deterministic child identity')
    for family in ('manager','broker','oms'):
        identity=row[f'source_{family}_snapshot_id']
        if type(identity) is not str or str(UUID(identity))!=identity or UUID(identity).int==0:
            raise ValueError('Structural rejection exit lacks exact checkpoint UUID')
    proof=_load_native_exit_financial_checkpoint(client,prefix,row,parent,event,financial,
        decision_boundary_ms=row['boundary_ms'],expected_reason=REASON,first_price_source=first_price_source,
        expected_strategy_id=owner.manager.runtime.config.strategy_id)
    oms=load_unattested_oms_observation_snapshot(client,run_id=row['run_id'],
        checkpoint_sequence=proof.checkpoint_sequence)
    root=oms.root
    day=owner.manager.runtime.config.anchor_date
    if (root['snapshot_id']!=row['source_oms_snapshot_id']
            or root['content_hash']!=row['source_oms_snapshot_hash']
            or root['run_id']!=row['run_id'] or root['checkpoint_sequence']!=proof.checkpoint_sequence
            or root['boundary_ms']!=proof.boundary_ms or root['session_date']!=day.isoformat()):
        raise ValueError('Structural rejection complete OMS checkpoint differs from own reference')
    portfolio=load_portfolio_snapshot(client,run_id=row['run_id'],account_id=financial.account_id,
        state_revision=proof.checkpoint_sequence)
    at=market_day_boundary(day,proof.boundary_ms).astimezone(timezone.utc)
    captured=(None if portfolio is None else datetime.fromisoformat(portfolio['snapshot_at']))
    if (portfolio is None or portfolio['state_hash']!=row['source_portfolio_state_hash']
            or type(portfolio['state_revision']) is not int
            or portfolio['state_revision']!=proof.checkpoint_sequence
            or captured.tzinfo is None or captured.astimezone(timezone.utc)!=at):
        raise ValueError('Structural rejection complete Portfolio checkpoint differs from own reference')
    # Historical cursors are scoped lookups, never new execution leases or
    # permission to rewind the current writer head.
    cursor=load_latest_backtest_cursor(client,replace(prefix,last_sequence=proof.checkpoint_sequence))
    if (cursor is None or cursor.get('event_sequence')!=proof.checkpoint_sequence
            or cursor.get('run_id')!=row['run_id'] or cursor.get('boundary_ms')!=proof.boundary_ms
            or cursor.get('session_date')!=day.isoformat() or str(cursor.get('batch_id')) not in prefix.batch_ids):
        raise ValueError('Structural rejection financial source cursor changed during cold join')
    return StructuralRejectionExitFinancialCheckpoint(proof.run_id,proof.checkpoint_sequence,
        proof.boundary_ms,proof.account_id,proof.assignment_id,proof.ticker,proof.conid,proof.held_quantity,
        proof.broker_snapshot_hash,root['content_hash'],portfolio['state_hash'])
